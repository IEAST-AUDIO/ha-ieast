"""iEAST 设备实机探测工具(纯标准库, 无第三方依赖)。

用法:
  python ieast_probe.py <ip>                 # 全量探测: 设备信息/播放状态/EQ/分组
  python ieast_probe.py <ip> getStatusEx     # 发送任意 httpapi 命令
  python ieast_probe.py <ip> --tcp "MCU+PAS+MTE&" "MCU+PINFGGET"
  python ieast_probe.py <ip> --frame doc --tcp "MCU+PAS+MTE&"   # 指定文档帧模式
"""
from __future__ import annotations

import json
import socket
import struct
import sys
import time
import urllib.request

MAGIC = bytes([0x18, 0x96, 0x18, 0x20])
TOKEN = bytes([0xB0, 0xC6, 0xEF, 0x00, 0x81, 0x87, 0xD7, 0x25])


def http_cmd(host: str, command: str, timeout: float = 6.0) -> str:
    for scheme in ("http", "https"):
        url = f"{scheme}://{host}/httpapi.asp?command={command}"
        try:
            ctx = __import__("ssl")._create_unverified_context()
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
                text = resp.read().decode("utf-8", "replace").strip()
            print(f"[{scheme}] {command} ->")
            print(text)
            return text
        except Exception as err:  # noqa: BLE001
            print(f"[{scheme}] {command} 失败: {err}")
    return ""


def tcp_cmd(host: str, payload: str, frame_mode: str = "auto", wait: float = 1.5) -> None:
    for mode in (["token", "doc"] if frame_mode == "auto" else [frame_mode]):
        try:
            sock = socket.create_connection((host, 8899), timeout=4)
        except OSError as err:
            print(f"[tcp:{mode}] 连接失败: {err}")
            continue
        try:
            raw = payload.encode()
            if mode == "token":
                frame = MAGIC + struct.pack("<II", len(raw), 0x100) + TOKEN + raw
            else:
                frame = MAGIC + struct.pack("<II", len(raw), sum(raw)) + b"\x00" * 8 + raw
            sock.sendall(frame)
            sock.settimeout(wait)
            buf = b""
            start = time.time()
            while time.time() - start < wait:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
            payloads = []
            while True:
                i = buf.find(MAGIC)
                if i < 0:
                    break
                if len(buf) < i + 20:
                    break
                plen = struct.unpack("<I", buf[i + 4 : i + 8])[0]
                if len(buf) < i + 20 + plen:
                    break
                payloads.append(buf[i + 20 : i + 20 + plen].decode("utf-8", "replace"))
                buf = buf[i + 20 + plen :]
            if payloads:
                print(f"[tcp:{mode}] {payload} -> {payloads}")
                return
            print(f"[tcp:{mode}] {payload} -> (无应答)")
        finally:
            sock.close()


def full_probe(host: str) -> None:
    print("=" * 60)
    print("1) getStatusEx 设备信息")
    status = http_cmd(host, "getStatusEx")
    try:
        data = json.loads(status)
        print(
            f"   设备: {data.get('DeviceName')}  型号: {data.get('project')}  "
            f"固件: {data.get('firmware')}  UUID: {data.get('uuid')}"
        )
        print(
            f"   分组角色: group={data.get('group')}  组名: {data.get('GroupName')}  "
            f"预设键数: {data.get('preset_key')}  RSSI: {data.get('RSSI')}"
        )
    except json.JSONDecodeError:
        pass
    print("=" * 60)
    print("2) getPlayerStatus 播放状态")
    http_cmd(host, "getPlayerStatus")
    print("=" * 60)
    print("3) EQGetList EQ 预设")
    http_cmd(host, "EQGetList")
    print("=" * 60)
    print("4) multiroom:getSlaveList 分组从机")
    http_cmd(host, "multiroom:getSlaveList")
    print("=" * 60)
    print("5) TCP 8899 通道 (MCU+PINFGGET)")
    tcp_cmd(host, "MCU+PINFGGET")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    host = sys.argv[1]
    if len(sys.argv) == 2:
        full_probe(host)
        return
    if sys.argv[2] == "--tcp":
        frame_mode = "auto"
        if len(sys.argv) > 4 and sys.argv[3] == "--frame":
            frame_mode = sys.argv[4]
            cmds = sys.argv[5:]
        else:
            cmds = sys.argv[3:]
        for c in cmds:
            tcp_cmd(host, c, frame_mode)
    else:
        for c in sys.argv[2:]:
            http_cmd(host, c)


if __name__ == "__main__":
    main()
