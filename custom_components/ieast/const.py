"""iEAST Audio 集成常量。"""

from __future__ import annotations

DOMAIN = "ieast"
MANUFACTURER = "iEAST"

CONF_POLL_INTERVAL = "poll_interval"
CONF_USE_HTTPS = "use_https"
CONF_TCP_EXTRAS = "tcp_extras"
CONF_TCP_FRAME_MODE = "tcp_frame_mode"
CONF_GROUP_PASSWORD = "group_password"
CONF_PUSH_LISTEN = "push_listen"

DEFAULT_POLL_INTERVAL = 6
DEFAULT_USE_HTTPS = False
DEFAULT_TCP_EXTRAS = True
DEFAULT_TCP_FRAME_MODE = "auto"
DEFAULT_GROUP_PASSWORD = ""
DEFAULT_PUSH_LISTEN = True

FRAME_MODE_AUTO = "auto"
FRAME_MODE_TOKEN = "token"
FRAME_MODE_DOC = "doc"
FRAME_MODES = [FRAME_MODE_AUTO, FRAME_MODE_TOKEN, FRAME_MODE_DOC]

# getPlayerStatus.mode -> (内部名, 显示名)
MODE_INFO: dict[int, tuple[str, str]] = {
    0: ("none", "无源"),
    1: ("airplay", "AirPlay"),
    2: ("dlna", "DLNA"),
    10: ("wifi", "iEAST 流媒体"),
    11: ("usb", "USB 媒体库"),
    16: ("tf", "TF 卡"),
    20: ("reserved20", "预留"),
    31: ("spotify", "Spotify Connect"),
    32: ("tidal", "TIDAL Connect"),
    40: ("aux", "AUX 输入"),
    41: ("bluetooth", "蓝牙"),
    42: ("storage", "外部存储"),
    43: ("optical", "光纤输入"),
    50: ("mirror", "投屏"),
    60: ("voicemail", "语音留言"),
    99: ("slave", "多房间同步"),
}

# select_source 显示名 -> setPlayerCmd:switchmode 参数
SOURCE_COMMANDS: dict[str, str] = {
    "流媒体 (WiFi)": "Wifi",
    "蓝牙": "Bluetooth",
    "AUX 输入": "line-in",
    "光纤输入": "optical",
    "USB 磁盘": "udisk",
}

# getPlayerStatus.loop -> (shuffle, repeat)
# 0 循环全部 / 1 单曲循环 / 2 随机循环 / 3 随机不循环 / 4 顺序不循环
LOOP_SHUFFLE_REPEAT: dict[int, tuple[bool, str]] = {
    0: (False, "all"),
    1: (False, "one"),
    2: (True, "all"),
    3: (True, "off"),
    4: (False, "off"),
}

SIGNAL_STRENGTH = "signal_strength"

# mDNS 名称里视为 iEAST 家族设备的前缀(不区分大小写)
# 实测机型名: IEAST AMP80_01B1 / AMP-i50Bv3_0383 / AMP-i30_MJ_350F / IEAST eAMP 2 / IEAST OLIO
IEAST_NAME_PREFIXES = (
    "ieast",
    "audiocast",
    "olio",
    "eamp",
    "eplay",
    "i50",
    "i30",
    "amp80",
    "m30",
    "m50",
)
