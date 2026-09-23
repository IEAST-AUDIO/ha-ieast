"""iEAST 本地网络 API 客户端。

两个通道:
- HTTP(S)  http://<ip>/httpapi.asp?command=...   —— 主通道，覆盖状态/播放/音量/EQ/多房间/设备控制
- TCP 8899 二进制帧 + MCU 透传指令              —— 扩展通道（预设/触发输入/勿扰/指示灯等）

TCP 帧格式（帧头 20 字节）:
  MAGIC(4) 18 96 18 20 | Length u32 LE | Checksum/Seq u32 LE | Reserved(8) | Payload
  - doc 帧模式: 第三字段为 payload 字节和, 保留区全零（TCP API V1.2 文档）
  - token 帧模式: 第三字段为递增序号, 保留区为固定 TOKEN（ProConsole/实机验证）
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from typing import Any

import aiohttp
from aiohttp.client_exceptions import ClientError

from .const import (
    FRAME_MODE_DOC,
    FRAME_MODE_TOKEN,
)

_LOGGER = logging.getLogger(__name__)

HTTP_API_PATH = "/httpapi.asp"
TCP_PORT = 8899
TCP_MAGIC = bytes([0x18, 0x96, 0x18, 0x20])
TCP_TOKEN = bytes([0xB0, 0xC6, 0xEF, 0x00, 0x81, 0x87, 0xD7, 0x25])
TCP_CMD_GAP = 0.25  # 协议要求两条指令间隔 >200ms

DEFAULT_TIMEOUT = 8


class IeastApiError(Exception):
    """iEAST API 调用失败。"""


class IeastTcpError(IeastApiError):
    """TCP 8899 通道失败。"""


def hex_encode(text: str) -> str:
    """UTF-8 文本 -> 大写十六进制（ConnectMasterAp 的 ssid 参数）。"""
    return text.encode("utf-8").hex().upper()


def hex_decode(text: str) -> str | None:
    """尝试把十六进制字符串还原为文本; 不是十六进制时返回 None。"""
    if not text or len(text) % 2:
        return None
    try:
        return bytes.fromhex(text).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def decode_meta(value: Any) -> str | None:
    """设备元数据字段可能是 hex 也可能是明文, 统一处理。"""
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    decoded = hex_decode(text)
    return decoded if decoded is not None else text


def frame_doc(payload: bytes, _seq: int = 0) -> bytes:
    """文档版帧: Checksum = payload 字节和, 保留区全零。"""
    return TCP_MAGIC + struct.pack("<II", len(payload), sum(payload)) + b"\x00" * 8 + payload


def frame_token(payload: bytes, seq: int) -> bytes:
    """实机版帧: 序号 + 固定 TOKEN（与 ProConsole 一致）。"""
    return TCP_MAGIC + struct.pack("<II", len(payload), seq) + TCP_TOKEN + payload


def parse_frames(buffer: bytes) -> tuple[list[str], bytes]:
    """从字节流中解出完整帧的 payload 文本, 返回 (文本列表, 剩余字节)。

    尾部不足一个完整帧头时, 保留末尾 3 字节作为可能的半截 MAGIC,
    避免帧头跨 TCP 分包被截断。
    """
    payloads: list[str] = []
    while True:
        start = buffer.find(TCP_MAGIC)
        if start < 0:
            buffer = buffer[-3:]
            break
        if len(buffer) < start + 20:
            buffer = buffer[start:]
            break
        plen = struct.unpack("<I", buffer[start + 4 : start + 8])[0]
        if plen > 4096:
            buffer = buffer[start + 4 :]
            continue
        if len(buffer) < start + 20 + plen:
            buffer = buffer[start:]
            break
        payload = buffer[start + 20 : start + 20 + plen]
        payloads.append(payload.decode("utf-8", "replace"))
        buffer = buffer[start + 20 + plen :]
    return payloads, buffer


class IeastClient:
    """单台 iEAST 设备的本地控制客户端。"""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        *,
        use_https: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.session = session
        self._prefer_https = use_https
        self._scheme_locked: str | None = None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._tcp_lock = asyncio.Lock()
        self._tcp_seq = 0x100
        self._last_tcp_at = 0.0

    # ------------------------------------------------------------------ HTTP

    def _url(self, command: str, scheme: str) -> str:
        # 与官方文档一致, command 原样拼接（含 play:url 的原始 URL）
        return f"{scheme}://{self.host}{HTTP_API_PATH}?command={command}"

    async def _request(self, command: str) -> str:
        """发送 httpapi 命令, 返回原始文本。自动在 http/https 间探测并锁定。"""
        schemes = (
            [self._scheme_locked]
            if self._scheme_locked
            else (["https", "http"] if self._prefer_https else ["http", "https"])
        )
        last_error: Exception | None = None
        for scheme in schemes:
            try:
                async with self.session.get(
                    self._url(command, scheme),
                    timeout=self._timeout,
                    ssl=False,
                ) as resp:
                    if resp.status != 200:
                        raise IeastApiError(f"{command}: HTTP {resp.status}")
                    text = (await resp.text()).strip()
                if not text:
                    raise IeastApiError(f"{command}: 空响应")
                self._scheme_locked = scheme
                return text
            except (ClientError, TimeoutError, asyncio.TimeoutError, OSError) as err:
                last_error = err
                _LOGGER.debug("iEAST %s 经 %s 失败: %s", self.host, scheme, err)
            except IeastApiError as err:
                # 如 http 返回 404/空响应(e 系列固件), 继续尝试另一协议
                last_error = err
                _LOGGER.debug("iEAST %s 经 %s 失败: %s", self.host, scheme, err)
        raise IeastApiError(f"{self.host} 命令 {command} 失败: {last_error}")

    async def get_json(self, command: str) -> Any:
        text = await self._request(command)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # ------------------------------------------------------------- 设备状态

    async def get_status_ex(self) -> dict[str, Any]:
        data = await self.get_json("getStatusEx")
        if not isinstance(data, dict):
            raise IeastApiError(f"{self.host}: getStatusEx 返回异常")
        return data

    async def get_player_status(self) -> dict[str, Any]:
        data = await self.get_json("getPlayerStatus")
        if not isinstance(data, dict):
            raise IeastApiError(f"{self.host}: getPlayerStatus 返回异常")
        return data

    async def get_slave_list(self) -> dict[str, Any]:
        data = await self.get_json("multiroom:getSlaveList")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------- 播放控制

    async def play_url(self, url: str) -> None:
        await self.get_json(f"setPlayerCmd:play:{url}")

    async def play_playlist(self, url: str, index: int = 0) -> None:
        if url.startswith(("http://", "https://")) and any(c in url for c in "?:&=%"):
            command = f"setPlayerCmd:hex_playlist:{hex_encode(url)}:{index}"
        else:
            command = f"setPlayerCmd:playlist:{url}:{index}"
        await self.get_json(command)

    async def play_usb_list(self, index: int = 0) -> None:
        await self.get_json(f"setPlayerCmd:playLocalList:{index}")

    async def pause(self) -> None:
        await self.get_json("setPlayerCmd:pause")

    async def resume(self) -> None:
        await self.get_json("setPlayerCmd:resume")

    async def toggle(self) -> None:
        await self.get_json("setPlayerCmd:onepause")

    async def stop(self) -> None:
        await self.get_json("setPlayerCmd:stop")

    async def next_track(self) -> None:
        await self.get_json("setPlayerCmd:next")

    async def prev_track(self) -> None:
        await self.get_json("setPlayerCmd:prev")

    async def seek(self, position_sec: int) -> None:
        await self.get_json(f"setPlayerCmd:seek:{int(position_sec)}")

    async def set_volume(self, volume: int) -> None:
        await self.get_json(f"setPlayerCmd:vol:{max(0, min(100, int(volume)))}")

    async def volume_up(self) -> None:
        await self.get_json("setPlayerCmd:Vol%2B%2Bn")

    async def volume_down(self) -> None:
        await self.get_json("setPlayerCmd:Vol--n")

    async def set_mute(self, mute: bool) -> None:
        await self.get_json(f"setPlayerCmd:mute:{1 if mute else 0}")

    async def set_loop_mode(self, mode: int) -> None:
        await self.get_json(f"setPlayerCmd:loopmode:{mode}")

    async def switch_mode(self, mode_name: str) -> None:
        await self.get_json(f"setPlayerCmd:switchmode:{mode_name}")

    # ------------------------------------------------------------------ EQ

    async def get_eq_list(self) -> list[str]:
        data = await self.get_json("EQGetList")
        if isinstance(data, list):
            return [str(item) for item in data]
        return []

    async def eq_on(self) -> None:
        await self.get_json("EQOn")

    async def eq_off(self) -> None:
        await self.get_json("EQOff")

    async def eq_get_stat(self) -> str:
        data = await self.get_json("EQGetStat")
        if isinstance(data, dict):
            return str(data.get("EQStat", ""))
        return ""

    async def eq_load(self, name: str) -> None:
        await self.get_json(f"EQLoad:{name}")

    # ------------------------------------------------------------- 多房间

    async def get_group(self) -> list[dict[str, Any]]:
        data = await self.get_slave_list()
        slaves = data.get("slave_list")
        if isinstance(slaves, list):
            return [s for s in slaves if isinstance(s, dict)]
        return []

    async def join_group(self, master: dict[str, Any], password: str = "") -> None:
        """本机作为从机加入 master 所在分组（指令发给本机/从机）。

        master 需含: ssid, uuid, apcli0(WiFi IP), eth0, 可选 WifiChannel/auth/encry。
        """
        ssid_hex = hex_encode(str(master.get("ssid", "")))
        channel = str(master.get("WifiChannel") or "0")
        auth = str(master.get("auth") or "OPEN")
        encry = str(master.get("encry") or "NONE")
        eth = str(master.get("eth0") or "0.0.0.0")
        wifi = str(master.get("apcli0") or "0.0.0.0")
        uuid = str(master.get("uuid", ""))
        command = (
            f"ConnectMasterAp:ssid={ssid_hex}:ch={channel}:auth={auth}:encry={encry}"
            f":pwd={password}:chext=0:JoinGroupMaster:eth{eth}:wifi{wifi}:uuid{uuid}"
        )
        await self.get_json(command)

    async def ungroup(self) -> None:
        await self.get_json("multiroom:Ungroup")

    async def kickout_slave(self, ip: str) -> None:
        await self.get_json(f"multiroom:SlaveKickout:{ip}")

    async def set_slave_volume(self, ip: str, volume: int) -> None:
        await self.get_json(f"multiroom:SlaveVolume:{ip}:{int(volume)}")

    async def set_slave_mute(self, ip: str, mute: bool) -> None:
        await self.get_json(f"multiroom:SlaveMute:{ip}:{1 if mute else 0}")

    async def set_slave_channel(self, ip: str, channel: int) -> None:
        await self.get_json(f"multiroom:SlaveChannel:{ip}:{int(channel)}")

    async def set_channel(self, channel: int) -> None:
        """设置本机(主机/独立)播放声道: 0立体 1左 2右。"""
        await self.get_json(f"setPlayerCmd:slave_channel:{int(channel)}")

    # ----------------------------------------------------------- 设备控制

    async def reboot(self) -> None:
        await self.get_json("reboot")

    async def set_shutdown(self, seconds: int) -> None:
        await self.get_json(f"setShutdown:{int(seconds)}")

    async def get_shutdown(self) -> int:
        text = await self._request("getShutdown")
        try:
            return int(text)
        except ValueError:
            return 0


    async def time_sync(self, utc_datetime) -> None:
        await self.get_json(f"timeSync:{utc_datetime.strftime('%Y%m%d%H%M%S')}")

    # ------------------------------------------------------------------ 闹钟

    async def get_alarm(self, index: int) -> Any:
        """getAlarmClock:n 查询闹钟(0~2)。"""
        return await self.get_json(f"getAlarmClock:{int(index)}")

    async def stop_alarm(self) -> None:
        """alarmStop 停止当前响铃。"""
        await self.get_json("alarmStop")

    async def set_alarm(
        self,
        index: int,
        *,
        trigger: int,
        operation: int = 1,
        time_hhmmss: str | None = None,
        day: str | None = None,
        url: str | None = None,
    ) -> str:
        """setAlarmClock:n:trig:op:time[:day][:url]。

        trigger: 0=取消 1=单次(day=YYYYMMDD) 2=每天 4=每周位图(day=2位hex);
        time 为设备本地 HHMMSS; 时间基准依赖 time_sync。
        """
        command = f"setAlarmClock:{int(index)}:{int(trigger)}"
        if int(trigger) != 0:
            if not time_hhmmss:
                raise IeastApiError("启用闹钟需要 time (HHMMSS)")
            command += f":{int(operation)}:{time_hhmmss}"
            if day is not None:
                command += f":{day}"
            if url is not None:
                command += f":{url}"
        return await self._request(command)

    # --------------------------------------------------------- TCP 8899 MCU

    async def tcp_command(
        self,
        payload: str,
        frame_mode: str = "auto",
        wait: float = 1.2,
    ) -> list[str]:
        """发送 MCU 透传指令, 返回应答帧文本列表。"""
        async with self._tcp_lock:
            if self._last_tcp_at:
                now = asyncio.get_running_loop().time()
                elapsed = now - self._last_tcp_at
                if elapsed < TCP_CMD_GAP:
                    await asyncio.sleep(TCP_CMD_GAP - elapsed)
            modes = [frame_mode] if frame_mode != "auto" else ["token", "doc"]
            last_error: Exception | None = None
            for mode in modes:
                try:
                    return await self._tcp_command_once(payload, mode, wait)
                except (IeastTcpError, OSError) as err:
                    last_error = err
                    _LOGGER.debug("iEAST %s TCP(%s) 失败: %s", self.host, mode, err)
            raise IeastTcpError(f"{self.host} TCP 指令 {payload} 失败: {last_error}")

    async def _tcp_command_once(self, payload: str, mode: str, wait: float) -> list[str]:
        reader_writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, TCP_PORT), timeout=4
        )
        try:
            self._tcp_seq = (self._tcp_seq + 1) & 0xFFFFFFFF
            raw = payload.encode("utf-8")
            if mode == FRAME_MODE_TOKEN:
                frame = frame_token(raw, self._tcp_seq)
            elif mode == FRAME_MODE_DOC:
                frame = frame_doc(raw, self._tcp_seq)
            else:
                raise IeastTcpError(f"未知帧模式: {mode}")
            reader_writer.write(frame)
            await reader_writer.drain()
            self._last_tcp_at = asyncio.get_running_loop().time()
            buffer = b""
            deadline = asyncio.get_running_loop().time() + wait
            while asyncio.get_running_loop().time() < deadline:
                timeout = max(0.05, deadline - asyncio.get_running_loop().time())
                try:
                    chunk = await asyncio.wait_for(reader_writer.read(4096), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                buffer += chunk
                payloads, buffer = parse_frames(buffer)
                if payloads:
                    return payloads
            return []
        finally:
            reader_writer.close()
            try:
                await reader_writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def tcp_ok(self) -> bool:
        """探测 8899 通道是否可用。"""
        try:
            await self.tcp_command("MCU+PINFGET", wait=0.6)
            return True
        except (IeastTcpError, OSError):
            return False
