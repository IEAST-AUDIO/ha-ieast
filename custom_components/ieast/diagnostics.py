"""iEAST 诊断数据下载支持(开发者排障用)。

设置 -> 设备 -> 下载诊断: 生成包含设备信息/播放状态/DSP能力/分组/选项的 JSON。
已做脱敏: 隐藏 MAC/BSSID/ip/temp_uuid 等字段, 保留排障所需的结构化信息。
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

REDACT_KEYS = {
    "MAC",
    "BT_MAC",
    "AP_MAC",
    "ETH_MAC",
    "BSSID",
    "apcli0",
    "eth0",
    "ra0",
    "ip",
    "temp_uuid",
    "essid",
    "upnp_uuid",
    "uuid",
}

TO_REDACT_STATUS = {
    "uuid", "MAC", "BT_MAC", "AP_MAC", "ETH_MAC", "BSSID",
    "apcli0", "eth0", "ra0", "temp_uuid", "upnp_uuid", "essid",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """返回配置条目级诊断数据。"""
    entry_data = hass.data[DOMAIN]["entries"][entry.entry_id]
    coordinator = entry_data["coordinator"]
    caps = entry_data.get("dsp")
    data = coordinator.data

    return {
        "entry": {
            "entry_id": entry.entry_id,
            "host": entry.data.get("host"),
            "options": dict(entry.options),
        },
        "dsp_caps": {
            "profile": caps.profile if caps else None,
            "peq_bands": caps.peq_bands if caps else None,
            "has_dpu": caps.has_dpu if caps else None,
            "has_diag": caps.has_diag if caps else None,
            "family": caps.family if caps else None,
            "params": (
                {f"g{g}i{i}": v for (g, i), v in sorted(caps.params.items())}
                if caps
                else None
            ),
        },
        "status_ex": async_redact_data(data.status_ex if data else {}, TO_REDACT_STATUS),
        "player": data.player if data else {},
        "group_slaves": [
            async_redact_data(s, {"uuid", "ip"}) for s in (data.slaves if data else [])
        ],
        "eq_list": data.eq_list if data else [],
        "shutdown_sec": data.shutdown_sec if data else None,
        "last_update_success": coordinator.last_update_success,
        "last_exception": (
            str(coordinator.last_exception) if coordinator.last_exception else None
        ),
    }
