"""iEAST PK 量产参数包 导出/导入 (BP10_DSP_全参数接口协议_v1.1 §6.6)。

包结构: head8('P','K',fmt=1,family,pver,nsec,0,0) + 段表(nsec*8B) + 各段数据,
整包 CRC32(zlib, 0xEDB88320)。段: 0=SYS 1=DPU(仅110P) 2=SCH镜像(仅110P)。
通道: MCU+PAS+PKI/PKR/PKB/PKW/PKC/PKF& (HTTP 透传)。

铁律: 导入前必须 PKI 核对 family 一致(1=110P, 2=215), 不一致拒绝写入;
215 仅支持导入段 0。本模块保持无 HA 依赖, 便于离线单测。
"""

from __future__ import annotations

import logging
import re
import zlib
from dataclasses import dataclass
from typing import Any

from .api import IeastClient

_LOGGER = logging.getLogger(__name__)

PAGE_SIZE = 40            # PKR 每页 40B (hex 80 字符)
BLOCK_SIZE = 12           # PKW 每块 12B (hex 24 字符)
HEAD_SIZE = 8
SEG_ENTRY_SIZE = 8
SEGMENT_NAMES = {0: "SYS", 1: "DPU", 2: "SCH"}

PK_ERRORS = {
    1: "导出构建失败", 2: "格式错", 3: "页越界", 4: "Flash 读失败",
    5: "段不支持", 6: "未 PKB", 7: "块序号乱序", 8: "hex 非法", 9: "段超长",
    10: "方案库写失败", 11: "段号不符", 12: "CRC 不符",
}


class PackError(Exception):
    """PK 参数包导出/导入失败。"""


def crc32_of(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _pk_err(text: str) -> None:
    m = re.search(r"PKERR(\d+)", text)
    if m:
        code = int(m.group(1))
        raise PackError(f"设备拒绝({code}: {PK_ERRORS.get(code, '未知')})")


def _expect(text: str, token: str) -> None:
    _pk_err(text)
    if token not in text:
        raise PackError(f"应答异常, 期望 {token}: {text.strip()}")


@dataclass
class PkInfo:
    family: int
    pver: int
    pages: int
    crc32: int
    raw: str


def parse_pki(text: str) -> PkInfo:
    """PKI 应答解析: PKI<f><v><nnn><crc32 8hex>。v 宽度未知, 从右锚定解析。"""
    m = re.search(r"PKI(\d)(\d*?)(\d{3})([0-9A-Fa-f]{8})(?:&|$)", text)
    if not m:
        raise PackError(f"PKI 应答解析失败: {text.strip()}")
    return PkInfo(
        family=int(m.group(1)),
        pver=int(m.group(2) or 0),
        pages=int(m.group(3)),
        crc32=int(m.group(4), 16),
        raw=text.strip(),
    )


def parse_pkr(text: str) -> tuple[int, bytes]:
    """PKR 应答解析: PKR<nnn><hex 80> -> (页号, 数据)。"""
    m = re.search(r"PKR(\d{3})([0-9A-Fa-f]+)", text)
    if not m:
        raise PackError(f"PKR 应答解析失败: {text.strip()}")
    try:
        return int(m.group(1)), bytes.fromhex(m.group(2))
    except ValueError as err:
        raise PackError(f"PKR hex 非法: {err}") from err


async def pk_export(client: IeastClient) -> tuple[bytes, PkInfo]:
    """导出整包: PKI 取页数与整包 CRC -> 逐页 PKR -> 校验 CRC。"""
    info = parse_pki(await client.passthrough("PKI"))
    pages: list[bytes] = []
    for n in range(info.pages):
        page_no, data = parse_pkr(await client.passthrough(f"PKR{n:03d}"))
        if page_no != n:
            raise PackError(f"页序号错乱: 期望 {n} 实际 {page_no}")
        pages.append(data)
    blob = b"".join(pages)
    actual = crc32_of(blob)
    if actual != info.crc32:
        raise PackError(
            f"整包 CRC 校验失败: 设备={info.crc32:08X} 实际={actual:08X}"
        )
    return blob, info


@dataclass
class PkSegment:
    seg_id: int
    name: str
    crc32: int
    data: bytes


def parse_package(blob: bytes) -> dict[str, Any]:
    """解析参数包文件: head8 + 段表 + 段数据。"""
    if len(blob) < HEAD_SIZE:
        raise PackError(f"包太小({len(blob)}B)")
    if blob[0:2] != b"PK" or blob[2] != 1:
        raise PackError("不是 PK v1 参数包")
    family = blob[3]
    pver = blob[4]
    nsec = blob[5]
    off = HEAD_SIZE
    if len(blob) < off + nsec * SEG_ENTRY_SIZE:
        raise PackError("段表不完整")
    # 协议布局: 整张段表在前, 之后才是连续的各段数据
    entries: list[tuple[int, int, int]] = []
    for _ in range(nsec):
        entry = blob[off : off + SEG_ENTRY_SIZE]
        off += SEG_ENTRY_SIZE
        seg_id = entry[0]
        seg_len = entry[1] | (entry[2] << 8)
        seg_crc = int.from_bytes(entry[4:8], "little")
        entries.append((seg_id, seg_len, seg_crc))
    segments: dict[int, PkSegment] = {}
    for seg_id, seg_len, seg_crc in entries:
        data = blob[off : off + seg_len]
        off += seg_len
        if len(data) != seg_len:
            raise PackError(f"段{seg_id}({SEGMENT_NAMES.get(seg_id, seg_id)})数据不完整")
        actual = crc32_of(data)
        if actual != seg_crc:
            raise PackError(
                f"段{seg_id} CRC 校验失败: 表={seg_crc:08X} 实际={actual:08X}"
            )
        segments[seg_id] = PkSegment(seg_id, SEGMENT_NAMES.get(seg_id, str(seg_id)), seg_crc, data)
    return {"family": family, "pver": pver, "segments": segments}


async def pk_import(client: IeastClient, blob: bytes) -> dict[str, Any]:
    """导入参数包: family 核对 -> 每段 PKB/PKW/PKC -> PKF 收尾。

    返回导入摘要; 215 机型自动跳过段 1/2 (仅支持段 0)。
    """
    info = parse_pki(await client.passthrough("PKI"))
    pkg = parse_package(blob)
    if pkg["family"] != info.family:
        raise PackError(
            f"family 不匹配, 拒绝写入: 包=family{pkg['family']} "
            f"设备=family{info.family} (协议要求不一致必须拒绝)"
        )
    imported: list[str] = []
    skipped: list[str] = []
    for seg_id, seg in pkg["segments"].items():
        if info.family == 2 and seg_id != 0:
            skipped.append(seg.name)
            continue
        blocks = [seg.data[i : i + BLOCK_SIZE] for i in range(0, len(seg.data), BLOCK_SIZE)]
        _expect(await client.passthrough(f"PKB{seg_id}"), "PKB1")
        for n, block in enumerate(blocks):
            _expect(await client.passthrough(f"PKW{n:03d}{block.hex().upper()}"), "PKW1")
        _expect(
            await client.passthrough(f"PKC{seg_id}{seg.crc32:08X}"), "PKC1"
        )
        imported.append(seg.name)
    _expect(await client.passthrough("PKF"), "PKF1")
    return {
        "family": info.family,
        "imported": imported,
        "skipped": skipped,
        "total_bytes": len(blob),
    }
