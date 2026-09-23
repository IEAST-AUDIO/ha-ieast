"""协议层冒烟测试: 对照 TCP API V1.2 文档样例与 ProConsole 帧算法验证。"""
import importlib.util
import re
import sys
import types
import zlib

PKG = "ieast_pkg"
ROOT = r"G:\BP10_code\ha-ieast\custom_components\ieast"

# stub aiohttp (环境里没装, api.py 仅 import)
aiohttp = types.ModuleType("aiohttp")
client_exceptions = types.ModuleType("aiohttp.client_exceptions")


class ClientError(Exception):
    pass


client_exceptions.ClientError = ClientError
aiohttp.client_exceptions = client_exceptions
sys.modules["aiohttp"] = aiohttp
sys.modules["aiohttp.client_exceptions"] = client_exceptions

pkg = types.ModuleType(PKG)
pkg.__path__ = [ROOT]
sys.modules[PKG] = pkg


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = load(f"{PKG}.const", rf"{ROOT}\const.py")
api = load(f"{PKG}.api", rf"{ROOT}\api.py")
dsp = load(f"{PKG}.dsp", rf"{ROOT}\dsp.py")
pack = load(f"{PKG}.pack", rf"{ROOT}\pack.py")
push = load(f"{PKG}.push", rf"{ROOT}\push.py")

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(name)


# 1. 文档样例: MUTE = MCU+PAS+MTE&
payload = b"MCU+PAS+MTE&"
frame = api.frame_doc(payload, 0)
expect = bytes.fromhex(
    "18961820" "0c000000" "2b030000" "0000000000000000" "4d43552b5041532b4d544526"
)
check("frame_doc(MUTE) 与文档样例一致", frame == expect, frame.hex())

# 2. 文档样例: STOP = MCU+PLY-STP (TCP API V1.2 文档原文字节)
payload = b"MCU+PLY-STP"
frame = api.frame_doc(payload, 0)
expect = bytes.fromhex("189618200b0000002903000000000000000000004d43552b504c592d535450")
check("frame_doc(STOP) 与文档样例一致", frame == expect, frame.hex())

# 3. token 帧 = dev8899.py 同款 (MAGIC+len<LE>+seq<LE>+TOKEN+payload)
frame = api.frame_token(b"MCU+PAS+VBG&", 0x101)
expect = (
    bytes.fromhex("18961820")
    + (12).to_bytes(4, "little")
    + (0x101).to_bytes(4, "little")
    + bytes.fromhex("b0c6ef008187d725")
    + b"MCU+PAS+VBG&"
)
check("frame_token 与 ProConsole 算法一致", frame == expect, frame.hex())

# 4. parse_frames: 拆应答帧 + 半截帧头残料保留
resp = api.frame_doc(b"AXX+PAS+SLF&", 0) + api.frame_doc(b"MCU+PAS+SLN&", 0) + b"\x18\x96"
payloads, rest = api.parse_frames(resp)
check("parse_frames 解出 2 帧", payloads == ["AXX+PAS+SLF&", "MCU+PAS+SLN&"], str(payloads))
check("parse_frames 保留半截 MAGIC", rest == b"\x18\x96", rest.hex())

# 5. hex 编解码
check(
    "hex_encode",
    api.hex_encode("IEAST eAMP 2").upper() == "49454153542065414D502032",
    api.hex_encode("IEAST eAMP 2"),
)
check("hex_decode 往返", api.hex_decode(api.hex_encode("客厅 iEAST")) == "客厅 iEAST")
check("hex_decode 非hex返回None", api.hex_decode("播放音乐") is None)
check("decode_meta hex", api.decode_meta("E5AEA2E58E85") == "客厅")
check("decode_meta 明文", api.decode_meta("Flat") == "Flat")

# 6. ConnectMasterAp 组包
master = {
    "ssid": "IEAST eAMP 2 8FA2",
    "uuid": "FF970015A6FE22C1660AB4D8",
    "eth0": "0.0.0.0",
    "apcli0": "192.168.4.10",
    "WifiChannel": "0",
}


class FakeClient:
    join_group = api.IeastClient.join_group

    def __init__(self):
        self.commands = []

    async def get_json(self, command):
        self.commands.append(command)
        return "OK"


fake = FakeClient()
import asyncio

asyncio.run(fake.join_group(master, ""))
cmd = fake.commands[0]
check(
    "ConnectMasterAp 组包含 ssid hex/uuid/IP/JoinGroupMaster",
    cmd.upper().startswith("CONNECTMASTERAP:SSID=49454153542065414D502032")
    and ":CH=0:" in cmd.upper()
    and ":AUTH=OPEN:ENCRY=NONE:PWD=:" in cmd.upper()
    and "JOINGROUPMASTER:ETH0.0.0.0:WIFI192.168.4.10:UUIDFF970015A6FE22C1660AB4D8" in cmd.upper(),
    cmd,
)

# 7. DSP 透传应答解析 (BP10_PEQ_v1.0 / DSP全参数_v1.1 应答格式)

# 7.1 透传组包: TCP 8899 优先, HTTP 兜底
class FakePassClient:
    """无 8899 通道的设备: tcp_command 抛错, 落到 HTTP _request。"""

    def __init__(self):
        self.urls = []

    async def tcp_command(self, payload, frame_mode="auto", wait=1.2):
        raise api.IeastTcpError("no 8899")

    async def _request(self, command):
        self.urls.append(command)
        return "PAS+PEQC12&"


import asyncio as _asyncio

fake2 = FakePassClient()
_asyncio.run(api.IeastClient.passthrough(fake2, "PEQC"))
check("passthrough HTTP 兜底组包 MCU+PAS+PEQC&", fake2.urls[-1] == "MCU+PAS+PEQC&", fake2.urls[-1])
_asyncio.run(api.IeastClient.passthrough(fake2, "MCU+PAS+DPST&"))
check("passthrough 剥离已有前缀", fake2.urls[-1] == "MCU+PAS+DPST&", fake2.urls[-1])


class FakeTcpFirst:
    """量产固件实机形态: 8899 有应答, 不走 HTTP。"""

    def __init__(self):
        self.tcp_cmds = []
        self.http_cmds = []

    async def tcp_command(self, payload, frame_mode="auto", wait=1.2):
        self.tcp_cmds.append(payload)
        return ["PAS+PEQC12&"]

    async def _request(self, command):
        self.http_cmds.append(command)
        return "PAS+HTTP&"


ft = FakeTcpFirst()
reply = _asyncio.run(api.IeastClient.passthrough(ft, "PEQC"))
check(
    "passthrough TCP 优先且不走 HTTP",
    reply == "PAS+PEQC12&" and ft.tcp_cmds == ["MCU+PAS+PEQC&"] and ft.http_cmds == [],
    f"reply={reply}",
)

# 7.2 parse_replies
check(
    "parse_replies 拆分多应答",
    dsp.parse_replies("PAS+PEQDOK&PAS+DPS60078&")
    == ["PEQDOK", "DPS60078"],
    str(dsp.parse_replies("PAS+PEQDOK&PAS+DPS60078&")),
)

# 7.3 PEQR 查询应答: PAS+PEQR05 1 01000 070 + 045
class FakePeq:
    async def passthrough(self, cmd):
        return "PAS+PEQR05101000070+045&"


peq = _asyncio.run(dsp.peq_get(FakePeq(), 5))
check(
    "peq_get 解析段5 (LowShelf 1000Hz Q0.70 +4.5dB)",
    peq == {"band": 5, "type": 1, "type_name": "LowShelf", "freq": 1000, "q": 0.7, "gain": 4.5},
    str(peq),
)

# 7.4 DPS 参数应答 (vvv 为 3 位十进制: Balance 默认 0x78=120 -> "120")
class FakeDps:
    async def passthrough(self, cmd):
        self.cmd = cmd
        return "PAS+DPS60120&"


fd = FakeDps()
val = _asyncio.run(dsp.dsp_param_get(fd, 6, 0))
check("dsp_param_get 解析 Balance=120", val == 120 and fd.cmd == "DPQ60", f"{val} {fd.cmd}")

# 7.5 DPST 诊断应答 (ss=14 l=1 d=0 c=3661 err=0)
class FakeDpst:
    async def passthrough(self, cmd):
        return "PAS+DPST1410036610&"


diag = _asyncio.run(dsp.dsp_diag(FakeDpst()))
check(
    "dsp_diag 解析 Run/Loaded/Clean/3661s/err0",
    diag.get("state") == 14 and diag.get("dpu_loaded") == 1
    and diag.get("dpu_dirty") == 0 and diag.get("dpu_active_sec") == 3661
    and diag.get("last_error") == 0,
    str(diag),
)

# 7.6 DPU 参数逐项查询(组查询聚合 / 部分聚合补查 / 明确不支持跳过)


class FakeDpuFull:
    """组查询一次回齐全组(理想聚合)。"""

    def __init__(self):
        self.cmds = []

    async def passthrough(self, cmd):
        self.cmds.append(cmd)
        m = re.fullmatch(r"DPQ(\d)A", cmd)
        if m:
            g = int(m.group(1))
            if g == 2:
                return "PAS+DPSERR&"  # 该组不支持
            return "&".join(f"PAS+DPS{g}{i}{100 + i:03d}&" for i in range(dsp.DPU_GROUP_ITEMS[g]))
        return ""


fdpu = FakeDpuFull()
params = _asyncio.run(dsp.query_dpu_params(fdpu))
check(
    "query_dpu_params 聚合应答且跳过不支持组(g2)",
    (0, 0) in params and (1, 3) in params and (6, 0) in params
    and not any(g == 2 for g, _ in params)
    and len(params) == 21 - 1,
    f"{len(params)} 项, g2={[(g,i) for g,i in params if g==2]}",
)


class FakeDpuPartial:
    """组查询只回第一帧(透传聚合差), 其余靠逐项补查。"""

    def __init__(self):
        self.item_queries = 0

    async def passthrough(self, cmd):
        m = re.fullmatch(r"DPQ(\d)A", cmd)
        if m:
            g = int(m.group(1))
            return f"PAS+DPS{g}0{100:03d}&"  # 只有 i=0, 值 100
        if re.fullmatch(r"DPQ(\d)(\d)", cmd):
            self.item_queries += 1
            g, i = int(cmd[3]), int(cmd[4])
            return f"PAS+DPS{g}{i}{200:03d}&"
        return ""


fpart = FakeDpuPartial()
params2 = _asyncio.run(dsp.query_dpu_params(fpart))
check(
    "query_dpu_params 部分聚合时逐项补查",
    params2[(0, 2)] == 200 and params2[(6, 0)] == 100 and fpart.item_queries == 21 - 7,
    f"补查 {fpart.item_queries} 次, 共 {len(params2)} 项, (6,0)={params2[(6, 0)]}",
)


class FakeProbeA230D:
    """A230D(215) 形态: PEQC=10 段, DPU 组全部不支持, 无 DPST/PKI。"""

    async def passthrough(self, cmd):
        if cmd.startswith("PEQC"):
            return "PAS+PEQC10&"
        if cmd.startswith("DPQ") or cmd.startswith("DPST") or cmd.startswith("PKI"):
            return "PAS+DPSERR&"
        return ""


caps_215 = _asyncio.run(dsp.probe_dsp(FakeProbeA230D()))
check(
    "probe_dsp A230D 形态: 只挂 PEQ, 不挂 DPU 实体",
    caps_215.profile == "bp10" and caps_215.peq_bands == 10
    and not caps_215.has_dpu and not caps_215.params,
    str(caps_215),
)


class FakeProbe110:
    """A450D(110P) 全能力形态。"""

    async def passthrough(self, cmd):
        if cmd.startswith("PEQC"):
            return "PAS+PEQC12&"
        if cmd.startswith("DPST"):
            return "PAS+DPST1410000000&"
        if cmd.startswith("PKI"):
            return "PAS+PKI1116001abcdef123"
        m = re.fullmatch(r"DPQ(\d)A", cmd)
        if m:
            g = int(m.group(1))
            return "&".join(f"PAS+DPS{g}{i}{120:03d}&" for i in range(dsp.DPU_GROUP_ITEMS[g]))
        return ""


caps = _asyncio.run(dsp.probe_dsp(FakeProbe110()))
check(
    "probe_dsp 识别 BP10/110P (12段+21项参数+diag+family1)",
    caps.profile == "bp10" and caps.peq_bands == 12 and caps.has_dpu
    and caps.has_diag and caps.family == 1 and caps.params[(6, 0)] == 120,
    f"{len(caps.params)} 项参数",
)


class FakeProbeNone:
    async def passthrough(self, cmd):
        return "PAS+PEQERR&"


caps2 = _asyncio.run(dsp.probe_dsp(FakeProbeNone()))
check("probe_dsp 非 BP10 机型回退 linkplay_std", caps2.profile == "linkplay_std" and caps2.peq_bands is None, str(caps2))


class FakeProbeEmpty:
    async def passthrough(self, cmd):
        raise dsp.IeastApiError("no bp10 mcu")


caps3 = _asyncio.run(dsp.probe_dsp(FakeProbeEmpty()))
check("probe_dsp 异常时回退 linkplay_std", caps3.profile == "linkplay_std" and not caps3.has_dpu, str(caps3))

# 7.7 友好参数解析
check("resolve_param bass_boost", dsp.resolve_param("bass_boost") == (0, 0))
check("resolve_param g3i1", dsp.resolve_param("G3I1") == (3, 1))
check("resolve_param 非法", dsp.resolve_param("volume") is None)

# 7.8 PEQ 设置组包
class FakeSetCapture:
    def __init__(self):
        self.cmds = []

    async def passthrough(self, cmd):
        self.cmds.append(cmd)
        return "PAS+OK"


fc = FakeSetCapture()
_asyncio.run(dsp.peq_set(fc, 3, freq=80, gain=-3.5, q=0.7, ptype=0))
check(
    "peq_set 组包 PEQF/PEQQ/PEQG/PEQT",
    fc.cmds == ["PEQF0300080", "PEQQ03070", "PEQG03-035", "PEQT030"],
    str(fc.cmds),
)

# ============================================================ 8. PK 参数包

def build_test_package(family=1):
    """合成参数包: head8 + 2 段段表 + 段数据。"""
    seg0 = b"\x01" * 30
    seg2 = b"\xAB" * 128
    head = b"PK" + bytes([1, family, 1, 2, 0, 0])

    def entry(seg_id, data):
        crc = zlib.crc32(data) & 0xFFFFFFFF
        return bytes([seg_id, len(data), len(data) >> 8, 0]) + crc.to_bytes(4, "little")

    return head + entry(0, seg0) + entry(2, seg2) + seg0 + seg2


class FakePkDevice:
    """PK 全流程假设备: PKI/PKR 导出 + PKB/PKW/PKC/PKF 导入应答。"""

    def __init__(self, blob, family=1):
        self.blob = blob
        self.family = family
        self.sent = []
        crc = f"{zlib.crc32(blob) & 0xFFFFFFFF:08X}"
        pages = (len(blob) + 39) // 40
        self.pki = f"PAS+PKI{family}11{pages:03d}{crc}&"

    async def passthrough(self, cmd):
        self.sent.append(cmd)
        if cmd == "PKI":
            return self.pki
        if cmd.startswith("PKR"):
            n = int(cmd[3:])
            data = self.blob[n * 40 : (n + 1) * 40]
            return f"PAS+PKR{n:03d}{data.hex().upper()}&"
        if cmd.startswith("PKB"):
            return "PAS+PKB1&"
        if cmd.startswith("PKW"):
            return "PAS+PKW1&"
        if cmd.startswith("PKC"):
            return "PAS+PKC1&"
        if cmd == "PKF":
            return "PAS+PKF1&"
        return ""


test_blob = build_test_package(1)
dev = FakePkDevice(test_blob, family=1)
exported, info = _asyncio.run(pack.pk_export(dev))
check(
    "pk_export 逐页读取且整包 CRC 校验通过",
    exported == test_blob and info.family == 1 and info.pages == (len(test_blob) + 39) // 40,
    f"{len(exported)}B, {info.pages} 页",
)

pkg = pack.parse_package(exported)
check(
    "parse_package 解析段表/段数据/CRC",
    pkg["family"] == 1 and set(pkg["segments"]) == {0, 2}
    and pkg["segments"][0].data == b"\x01" * 30
    and pkg["segments"][2].name == "SCH",
    str({k: v.name for k, v in pkg["segments"].items()}),
)

dev2 = FakePkDevice(test_blob, family=1)
summary = _asyncio.run(pack.pk_import(dev2, test_blob))
kw_cmds = [c for c in dev2.sent if c.startswith("PKW")]
check(
    "pk_import 段0/段2 全流程(PKB/PKW/PKC/PKF)",
    summary["imported"] == ["SYS", "SCH"]
    and dev2.sent[1] == "PKB0"
    and len(kw_cmds) == 3 + 11
    and dev2.sent[-1] == "PKF",
    f"导入 {summary['imported']}, PKW x{len(kw_cmds)}",
)

dev3 = FakePkDevice(build_test_package(2), family=2)
summary3 = _asyncio.run(pack.pk_import(dev3, build_test_package(2)))
check(
    "pk_import 215 机型自动跳过段1/2",
    summary3["imported"] == ["SYS"] and summary3["skipped"] == ["SCH"]
    and not any(c.startswith("PKB2") for c in dev3.sent),
    str(summary3),
)

try:
    _asyncio.run(pack.pk_import(FakePkDevice(test_blob, family=2), test_blob))
    check("pk_import family 不匹配拒绝写入", False, "未抛异常")
except pack.PackError as err:
    check("pk_import family 不匹配拒绝写入", "family 不匹配" in str(err), str(err))

# ============================================================ 9. SCH 方案库

scheme = bytes(range(128))
pieces = dsp.split_scheme(scheme)
check(
    "split_scheme 13 片 (12x10B + 8B) 可还原",
    len(pieces) == 13 and [len(p) for p in pieces] == [10] * 12 + [8]
    and b"".join(pieces) == scheme,
)


class FakeScheme:
    def __init__(self):
        self.sent = []

    async def passthrough(self, cmd):
        self.sent.append(cmd)
        if cmd.startswith("DPU"):
            return "PAS+DPB1&"
        if cmd.startswith("DPB"):
            return "PAS+DPB1&"
        if cmd.startswith("DPC"):
            return "PAS+DPC1&"
        if cmd.startswith("DPX"):
            return "PAS+DPX1&"
        return ""


fs = FakeScheme()
_asyncio.run(dsp.scheme_upload(fs, 3, 1, scheme))
check(
    "scheme_upload 组包 DPB+13xDPU+DPC",
    fs.sent[0] == "DPB31"
    and fs.sent[1].startswith("DPU3100") and len(fs.sent[1]) == 7 + 20
    and fs.sent[13].startswith("DPU3112") and len(fs.sent[13]) == 7 + 16
    and fs.sent[-1] == "DPC31",
    f"{fs.sent[0]}..{fs.sent[-1]}",
)

_asyncio.run(dsp.scheme_delete(fs, 3, 1))
check("scheme_delete 使用 DPX(非 DPD)", fs.sent[-1] == "DPX31", fs.sent[-1])

slots_text = "PAS+DPL3" + "A001A002A003----A005A006A007A008A009" + "&"
slots = dsp.parse_scheme_list(slots_text, 3)
check(
    "parse_scheme_list 9 槽位含空槽",
    slots == ["A001", "A002", "A003", "", "A005", "A006", "A007", "A008", "A009"],
    str(slots),
)

# ============================================================ 10. 8819 推送解析

msg = push.parse_push_line('AXX+PLY+INF{"Title":"E5AEA2E58E85","Artist":"AB","vol":"30"}')
check(
    "parse_push_line INF 元数据(hex 解码)",
    msg is not None and msg["kind"] == "meta" and msg.get("title") == "客厅"
    and msg.get("artist") == "AB" and "vol" not in msg,
    str(msg),
)
msg2 = push.parse_push_line("AXX+PLY+001")
check("parse_push_line 播放状态 001", msg2 == {"kind": "state", "playing": True}, str(msg2))
msg3 = push.parse_push_line("AXX+PLY+000")
check("parse_push_line 停止状态 000", msg3 == {"kind": "state", "playing": False}, str(msg3))
check("parse_push_line 垃圾行返回 None", push.parse_push_line("hello world") is None)
check("parse_push_line 空行返回 None", push.parse_push_line("  ") is None)
check(
    "parse_push_line 坏 JSON 返回 None",
    push.parse_push_line('AXX+PLY+INF{bad json') is None,
)

# ============================================================ 11. PEQ 编码/方案捕获

# 文档 §6 编码公式: freq 1000Hz -> round(12*log2(62.5))=72; Q0.7 -> 7; +4.5dB -> 80
band = dsp.encode_peq_band(freq=1000, gain=4.5, q=0.7, ptype=1)
check(
    "encode_peq_band 1000Hz/+4.5dB/Q0.7/LowShelf",
    band == bytes([1, 72, 7, 80]),
    band.hex(),
)
band_lo = dsp.encode_peq_band(freq=16, gain=-12.0, q=0.4, ptype=0)
check("encode_peq_band 边界(16Hz/-12dB/Q0.4)", band_lo == bytes([0, 0, 0, 21]), band_lo.hex())


class FakeCapture:
    """PEQR x12 + DPEA 假设备, 供 scheme_capture 合成。"""

    def __init__(self):
        self.n = 0

    async def passthrough(self, cmd):
        if cmd.startswith("PEQR"):
            self.n += 1
            return f"PAS+PEQR{cmd[4:]}101000070+045&"
        if cmd == "DPEA":
            return "PAS+DPEA1&"
        return ""


cap_hex = _asyncio.run(dsp.scheme_capture(FakeCapture(), 12, {(0, 0): 120}, True))
cap = bytes.fromhex(cap_hex)
check(
    "scheme_capture 合成 128B(PEQ48+DPU56+DPE3+校验1+补位20)",
    len(cap) == 128 and cap[:4] == bytes([1, 72, 7, 80]) and cap[48] == 120
    and cap[104] == 1 and sum(cap[:107]) & 0xFF == cap[107],
    f"len={len(cap)} peq1={cap[:4].hex()} checksum_ok={sum(cap[:107]) & 0xFF == cap[107]}",
)

# ============================================================ 12. 闹钟组包


class FakeAlarm:
    def __init__(self):
        self.cmds = []

    async def _request(self, command):
        self.cmds.append(command)
        return "OK"


fa = FakeAlarm()
_asyncio.run(api.IeastClient.set_alarm(fa, 0, trigger=2, time_hhmmss="073000"))
_asyncio.run(api.IeastClient.set_alarm(fa, 1, trigger=4, time_hhmmss="080000", day="41"))
_asyncio.run(api.IeastClient.set_alarm(fa, 2, trigger=0))
check(
    "set_alarm 组包 每天/每周位图/取消",
    fa.cmds == ["setAlarmClock:0:2:1:073000", "setAlarmClock:1:4:1:080000:41", "setAlarmClock:2:0"],
    str(fa.cmds),
)

print()
print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
