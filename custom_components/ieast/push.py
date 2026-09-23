"""iEAST 8819 状态推送订阅(实时元数据)。

设备在 8819 端口向已连接的控制器推送播放事件:
  AXX+PLY+INF{...}  —— JSON 元数据, Title/Artist/Album 为 hex 编码
  AXX+PLY+001 / AXX+PLY+000 —— 播放开始/停止
本模块解析为干净的字典回调给协调器; 断线自动重连(5-60s 退避)。
解析函数保持无状态, 便于离线单测。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from .api import decode_meta

_LOGGER = logging.getLogger(__name__)

INF_RE = re.compile(r"AXX\+PLY\+INF(.*)")
STATE_RE = re.compile(r"AXX\+PLY\+(\d{3})")

READ_TIMEOUT = 90  # 无推送的静默期属正常, 保持连接


def parse_push_line(line: str) -> dict | None:
    """解析一行推送: {'kind':'meta', title/artist/album/station} 或 {'kind':'state', playing}。"""
    line = line.strip()
    if not line:
        return None
    m = INF_RE.search(line)
    if m:
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        meta: dict = {"kind": "meta"}
        for src, dst in (
            ("Title", "title"),
            ("Artist", "artist"),
            ("Album", "album"),
            ("station", "station"),
        ):
            value = decode_meta(payload.get(src))
            if value:
                meta[dst] = value
        return meta
    m = STATE_RE.fullmatch(line)
    if m:
        return {"kind": "state", "playing": m.group(1) == "001"}
    return None


class IeastPushListener:
    """每台设备一个 8819 订阅器, 断线自动重连。"""

    def __init__(self, host: str, port: int, on_message) -> None:
        self._host = host
        self._port = port
        self._on_message = on_message
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = 5
        while not self._stopping:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port), timeout=5
                )
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.debug("8819 连接失败 %s: %s", self._host, err)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue

            backoff = 5
            buffer = b""
            try:
                while not self._stopping:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=READ_TIMEOUT)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        msg = parse_push_line(raw.decode("utf-8", "replace"))
                        if msg:
                            try:
                                self._on_message(msg)
                            except Exception:  # noqa: BLE001 回调异常不能断订阅
                                _LOGGER.exception("推送回调异常")
            except asyncio.TimeoutError:
                continue  # 静默期, 保持连接
            except (OSError, ConnectionError) as err:
                _LOGGER.debug("8819 连接断开 %s: %s", self._host, err)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, ConnectionError):
                    pass

            if not self._stopping:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
