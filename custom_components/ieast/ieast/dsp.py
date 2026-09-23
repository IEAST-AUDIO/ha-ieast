"""iEAST DSP 机型化挂载支持(BP10 家族, MCU+PAS 透传)。

协议: BP10_PEQ_接口协议_v1.0 / BP10_DSP_全参数接口协议_v1.1
通道: httpapi.asp?command=MCU+PAS+<CMD>& (HTTP 直传, 应答 PAS+...& 原样返回)

本模块保持无 HA 依赖, 便于离线单测。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from .api import IeastApiError, IeastClient

_LOGGER = logging.getLogger(__name__)

# 友好参数名 -> (组 g, 项 i)  [BP10_DSP_全参数接口协议_v1.1 §2]
FRIENDLY_PARAMS: dict[str, tuple[int, int]] = {
    "bass_boost": (0, 0),        # MaxxBass 强度
    "bass_cutoff": (0, 1),       # MaxxBass 截止频率
    "treble_boost": (1, 0),      # MaxxTreble 强度
    "treble_freq": (1, 1),       # MaxxTreble 频率
    "maxx3d": (2, 0),            # 旁路字节: 0x00=全开 0x05=全旁路
    "dialog_center": (3, 0),     # MaxxDialog 中置
    "dialog_side": (3, 1),       # MaxxDialog 侧置
    "volume_gain": (4, 0),       # MaxxVolume 增益
    "volume_type": (4, 1),       # MaxxVolume 类型(0=Soft-Knee)
    "volume_dyn_range": (4, 2),  # 动态范围(初始化表标注 Invalid, 谨慎开放)
    "volume_low_gain": (4, 3),
    "volume_gate": (4, 4),
    "leveler": (5, 0),           # 0/1
    "leveler_target": (5, 1),
    "leveler_range": (5, 2),
    "balance": (6, 0),           # 0x78=居中
}

PEQ_TYPES = {0: "Bell", 1: "LowShelf", 2: "HighShelf"}


def resolve_param(name: str) -> tuple[int, int] | None:
    """'bass_boost' 或 'g3i1' -> (组, 项)。"""
    name = name.strip().lower()
    if name in FRIENDLY_PARAMS:
        return FRIENDLY_PARAMS[name]
    m = re.fullmatch(r"g([0-6])i([0-7])", name)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


# 每组参数项数 [BP10_DSP_全参数接口协议_v1.1 §2]
DPU_GROUP_ITEMS: dict[int, int] = {0: 5, 1: 4, 2: 1, 3: 2, 4: 5, 5: 3, 6: 1}


def parse_replies(text: str) -> list[str]:
    """把透传应答文本拆成 PAS+ 令牌列表(去 & 与空白)。

    'PAS+PEQDOK&PAS+DPS60078&' -> ['PEQDOK', 'DPS60078']
    """
    tokens = []
    for part in text.split("PAS+"):
        part = part.strip().strip("&").strip()
        if part:
            tokens.append(part)
    return tokens


@dataclass
class DspCaps:
    """在线探测到的 DSP 能力。挂载原则: 查询应答有效才挂载, 参数值以应答为准。"""

    peq_bands: int | None = None      # PEQC: 12(110P)/10(215)/None=非 BP10 家族
    has_dpu: bool = False             # DPU 7 组(MaxxAudio), 由逐项查询结果决定
    has_diag: bool = False            # DPST 诊断(v3517+, 同时作为 DPEA 能力门控)
    family: int | None = None         # PKI: 1=110P 2=215
    profile: str = "linkplay_std"     # bp10 / linkplay_std
    params: dict[tuple[int, int], int] = field(default_factory=dict)
    # params: 逐项查询成功的 (组g, 项i) -> 当前值(0-255)。实体只为本字典中的参数挂载。


async def query_dpu_params(client: IeastClient) -> dict[tuple[int, int], int]:
    """逐组逐项查询 DPU 参数, 返回 {(g,i): 当前值}。

    原则: 查询应答有效的参数才收录 —— 上层只为本字典中的参数挂载实体,
    实体初值也取自本字典。组查询 DPQ<g>A& 一次返回多项; 应答不全/异常时
    对缺失项逐个 DPQ<g><i>& 补查(PEQC 已通过, 最坏 21 次查询)。
    """
    params: dict[tuple[int, int], int] = {}
    for group, expected in DPU_GROUP_ITEMS.items():
        try:
            text = await client.passthrough(f"DPQ{group}A")
        except IeastApiError as err:
            _LOGGER.debug("DPQ%dA 组查询失败, 视为本组不支持: %s", group, err)
            continue
        found: dict[int, int] = {}
        for token in parse_replies(text):
            m = re.fullmatch(rf"DPS{group}(\d)(\d{{3}})", token)
            if m and int(m.group(1)) < expected:
                found[int(m.group(1))] = int(m.group(2))
        if not found:
            if "DPSERR" in text:
                _LOGGER.debug("DPQ%dA 明确不支持, 跳过该组", group)
            continue
        # 组查询应答不全(如透传只回第一帧)时, 对缺失项逐个补查
        for item in range(expected):
            if item not in found:
                try:
                    found[item] = await dsp_param_get(client, group, item)
                except IeastApiError:
                    continue  # 该项本机不支持 -> 不挂载
        for item, value in found.items():
            params[(group, item)] = value
    return params


async def probe_dsp(client: IeastClient) -> DspCaps:
    """能力探测: 先确认 DSP 存在, 再逐项查询参数; 查不到的绝不挂载。"""
    caps = DspCaps()
    try:
        text = await client.passthrough("PEQC")
        m = re.search(r"PEQC(\d{2})", text)
        if m:
            caps.peq_bands = int(m.group(1))
    except IeastApiError as err:
        _LOGGER.debug("PEQC 探测失败(非 BP10 家族?): %s", err)
        return caps
    if caps.peq_bands is None:
        # PEQC 应答但无段号(如 PEQERR): 视为非 BP10 家族
        return caps
    caps.profile = "bp10"

    caps.params = await query_dpu_params(client)
    caps.has_dpu = bool(caps.params)

    try:
        text = await client.passthrough("DPST")
        if re.search(r"DPST\d", text):
            caps.has_diag = True
    except IeastApiError:
        pass

    try:
        text = await client.passthrough("PKI")
        m = re.search(r"PKI(\d)", text)
        if m:
            caps.family = int(m.group(1))
    except IeastApiError:
        pass

    return caps


# ------------------------------------------------------------------ DPU 组

async def dsp_param_set(client: IeastClient, group: int, item: int, value: int) -> int:
    """DPS<g><i><vvv>& 设参, 返回生效值(0-255)。"""
    value = max(0, min(255, int(value)))
    text = await client.passthrough(f"DPS{group}{item}{value:03d}")
    if "DPSERR" in text:
        raise IeastApiError(f"DSP 设置被拒绝(g{group}i{item}={value}): {text.strip()}")
    return value


async def dsp_param_get(client: IeastClient, group: int, item: int) -> int:
    """DPQ<g><i>& 查参, 返回 0-255。"""
    text = await client.passthrough(f"DPQ{group}{item}")
    m = re.search(rf"DPS{group}{item}(\d{{3}})", text)
    if not m:
        raise IeastApiError(f"DSP 查询失败(g{group}i{item}): {text.strip()}")
    return int(m.group(1))


async def dsp_group_reset(client: IeastClient, group: int) -> None:
    """DPR<g>& 复位整组为默认。"""
    text = await client.passthrough(f"DPR{group}")
    if "DPSERR" in text or "ERR" in text.replace("DPSERR", ""):
        raise IeastApiError(f"DSP 组复位失败(g{group}): {text.strip()}")


async def dsp_maxx_enable(client: IeastClient, enable: bool) -> int:
    """DPEA Maxx 算法总开关, 返回设备端状态(0/1)。"""
    text = await client.passthrough(f"DPEA{1 if enable else 0}")
    m = re.search(r"DPEA(\d)", text)
    if not m:
        raise IeastApiError(f"DPEA 失败: {text.strip()}")
    return int(m.group(1))


async def dsp_diag(client: IeastClient) -> dict[str, Any]:
    """DPST 状态诊断, 解析失败时保留原文。"""
    text = await client.passthrough("DPST")
    clean = text.strip().rstrip("&")
    result: dict[str, Any] = {"raw": clean}
    m = re.search(r"DPST(\d{2})(\d)(\d)(\d+?)(\d)$", clean)
    if m:
        result.update(
            {
                "state": int(m.group(1)),        # 14=Run
                "dpu_loaded": int(m.group(2)),
                "dpu_dirty": int(m.group(3)),
                "dpu_active_sec": int(m.group(4)),
                "last_error": int(m.group(5)),
            }
        )
    return result


# -------------------------------------------------------------------- PEQ

def _peq_err(text: str, what: str) -> None:
    if "PEQERR" in text:
        raise IeastApiError(f"PEQ {what} 被拒绝: {text.strip()}")


async def peq_set(
    client: IeastClient,
    band: int,
    *,
    freq: int | None = None,
    gain: float | None = None,
    q: float | None = None,
    ptype: int | None = None,
) -> list[str]:
    """设置一段 PEQ 的任意子集, 返回每步应答。

    freq: 16~24000 Hz; gain: ±12.0 dB; q: 0.40~3.00; ptype: 0/1/2。
    """
    if not 1 <= band <= 12:
        raise IeastApiError(f"PEQ 段号越界: {band}")
    replies: list[str] = []
    if freq is not None:
        freq = max(16, min(24000, int(freq)))
        text = await client.passthrough(f"PEQF{band:02d}{freq:05d}")
        _peq_err(text, f"频点({freq}Hz)")
        replies.append(text.strip())
    if q is not None:
        q100 = max(40, min(300, round(q * 100)))
        text = await client.passthrough(f"PEQQ{band:02d}{q100:03d}")
        _peq_err(text, f"Q值({q})")
        replies.append(text.strip())
    if gain is not None:
        gain = max(-12.0, min(12.0, float(gain)))
        sign = "+" if gain >= 0 else "-"
        g = abs(int(round(gain * 10)))
        text = await client.passthrough(f"PEQG{band:02d}{sign}{g:03d}")
        _peq_err(text, f"增益({gain}dB)")
        replies.append(text.strip())
    if ptype is not None:
        if ptype not in PEQ_TYPES:
            raise IeastApiError(f"PEQ 类型非法: {ptype}")
        text = await client.passthrough(f"PEQT{band:02d}{ptype}")
        _peq_err(text, "滤波器类型")
        replies.append(text.strip())
    return replies


async def peq_get(client: IeastClient, band: int) -> dict[str, Any]:
    """PEQRnn& 查询单段: {band,type,type_name,freq,q,gain}。"""
    if not 1 <= band <= 12:
        raise IeastApiError(f"PEQ 段号越界: {band}")
    text = await client.passthrough(f"PEQR{band:02d}")
    m = re.search(rf"PEQR{band:02d}(\d)(\d{{5}})(\d{{3}})([+-])(\d{{3}})", text)
    if not m:
        raise IeastApiError(f"PEQ 查询失败(段{band}): {text.strip()}")
    ptype = int(m.group(1))
    return {
        "band": band,
        "type": ptype,
        "type_name": PEQ_TYPES.get(ptype, str(ptype)),
        "freq": int(m.group(2)),
        "q": int(m.group(3)) / 100,
        "gain": (1 if m.group(4) == "+" else -1) * int(m.group(5)) / 10,
    }


async def peq_reset(client: IeastClient) -> None:
    """PEQD& 恢复当前音箱类型默认 PEQ。"""
    text = await client.passthrough("PEQD")
    if "PEQDOK" not in text:
        raise IeastApiError(f"PEQ 恢复默认失败: {text.strip()}")


# ---------------------------------------------------------------- 音效方案库
# SCH: 9 音箱 × 9 槽位, 每方案 128B (PEQ 48B + DPU 56B + DPE 3B + 校验)。
# 注意: 删除必须用 DPX (DPD 前缀会被 A31 透传篡改, 协议 v1.1 §6.5 警告)。

SCH_SCHEME_SIZE = 128
SCH_PIECES = 13
SCH_PIECE_DATA = 10
SCH_EMPTY_SLOT = "----"


def split_scheme(data: bytes) -> list[bytes]:
    """128B 方案数据切为 13 片: 前 12 片各 10B, 第 13 片 8B。"""
    if len(data) != SCH_SCHEME_SIZE:
        raise IeastApiError(f"方案数据必须为 {SCH_SCHEME_SIZE}B, 实际 {len(data)}B")
    return [data[i * SCH_PIECE_DATA : (i + 1) * SCH_PIECE_DATA] for i in range(12)] + [
        data[120:]
    ]


async def scheme_upload(client: IeastClient, speaker: int, slot: int, data: bytes) -> None:
    """上传方案: DPB 开始 -> 13 片 DPU -> DPC 提交。"""
    pieces = split_scheme(data)
    text = await client.passthrough(f"DPB{speaker}{slot}")
    if "DPB1" not in text:
        raise IeastApiError(f"方案上传启动失败: {text.strip()}")
    for nn, piece in enumerate(pieces):
        text = await client.passthrough(f"DPU{speaker}{slot}{nn:02d}{piece.hex().upper()}")
        if "DPB1" not in text:
            raise IeastApiError(f"分片 {nn:02d} 上传失败: {text.strip()}")
    text = await client.passthrough(f"DPC{speaker}{slot}")
    if "DPC1" not in text:
        raise IeastApiError(f"方案提交失败(需 13 片齐全): {text.strip()}")


async def scheme_apply(client: IeastClient, speaker: int, slot: int) -> None:
    """应用方案: DPA<s><e>&, 应答 DPA<s><e>。"""
    text = await client.passthrough(f"DPA{speaker}{slot}")
    if f"DPA{speaker}{slot}" not in text:
        raise IeastApiError(f"方案应用失败: {text.strip()}")


async def scheme_delete(client: IeastClient, speaker: int, slot: int) -> None:
    """删除方案: DPX<s><e>& (禁用 DPD!)。"""
    text = await client.passthrough(f"DPX{speaker}{slot}")
    if "DPX1" not in text:
        raise IeastApiError(f"方案删除失败: {text.strip()}")


def parse_scheme_list(text: str, speaker: int) -> list[str]:
    """DPL<s>& 应答解析: DPL<s><id×9>, 每槽 4 字符, '----'=空。"""
    m = re.search(rf"DPL{speaker}(.{{36}})", text)
    if not m:
        raise IeastApiError(f"方案槽位应答解析失败: {text.strip()}")
    return [
        "" if chunk == SCH_EMPTY_SLOT else chunk
        for chunk in re.findall(r".{4}", m.group(1))
    ]


# ---------------------------------------------------- 方案捕获(当前状态合成)


def encode_peq_band(*, freq: int, gain: float, q: float, ptype: int) -> bytes:
    """物理值 -> BP10 DSP 4B 编码 [type, freq_code, q_code, gain_code]。

    编码公式 [BP10_PEQ_接口协议_v1.0 §6]:
      freq_code = round(12 × log2(f/16))
      q_code    = round((Q-0.4)/0.0441)
      gain_code = 64 + round(dB/0.28125)
    """
    freq_code = max(0, min(255, round(12 * math.log2(max(freq, 1) / 16))))
    q_code = max(0, min(255, round((q - 0.4) / 0.0441)))
    gain_code = max(0, min(255, 64 + round(gain / 0.28125)))
    return bytes([ptype & 0xFF, freq_code, q_code, gain_code])


async def scheme_capture(
    client: IeastClient,
    peq_bands: int,
    dpu_params: dict[tuple[int, int], int],
    maxx_enable: bool | None = None,
) -> str:
    """读取当前 PEQ+DPU+DPE 状态, 合成 128B 方案, 返回 hex(256 字符)。

    布局: [PEQ 48B: 12 段 × (type,freq_code,q_code,gain_code)]
          [DPU 56B: 7 组 × 8 项原始寄存器值]
          [DPE 3B: DPEA + 2 保留]
          [累加和 1B][0x00 × 20]
    ⚠️ 第 107 字节之后的布局/校验算法协议文档未给出, 未经实机核对;
    本函数只返回 hex 不上传, 上传前请先与 ProConsole 导出内容比对验证。
    """
    peq = bytearray()
    for band in range(1, 13):
        if band <= peq_bands:
            info = await peq_get(client, band)
            peq += encode_peq_band(
                freq=info["freq"], gain=info["gain"], q=info["q"], ptype=info["type"]
            )
        else:
            peq += b"\x00\x00\x00\x00"
    dpu = bytearray()
    for group in range(7):
        for item in range(8):
            dpu.append(dpu_params.get((group, item), 0) & 0xFF)
    dpe = bytearray([0x01 if maxx_enable else 0x00, 0x00, 0x00])
    body = bytes(peq + dpu + dpe)  # 107B
    checksum = sum(body) & 0xFF
    data = body + bytes([checksum]) + b"\x00" * 20
    assert len(data) == SCH_SCHEME_SIZE
    return data.hex().upper()
