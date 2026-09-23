"""iEAST Audio 多房间系统集成。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IeastApiError, IeastClient, IeastTcpError
from .const import (
    CONF_GROUP_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_PUSH_LISTEN,
    CONF_TCP_EXTRAS,
    CONF_TCP_FRAME_MODE,
    CONF_USE_HTTPS,
    DEFAULT_GROUP_PASSWORD,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PUSH_LISTEN,
    DEFAULT_TCP_EXTRAS,
    DEFAULT_TCP_FRAME_MODE,
    DEFAULT_USE_HTTPS,
    DOMAIN,
)
from .coordinator import IeastCoordinator
from .push import IeastPushListener

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.MEDIA_PLAYER,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

SNAPSHOT_FIELDS = ("vol", "mute", "mode", "status")


def _store(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {})


def _entries(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    return _store(hass).setdefault("entries", {})


def _entry_by_entity_id(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    """把 media_player 实体 ID 解析回对应配置条目数据。"""
    entity_map = _store(hass).get("entities", {})
    entry_id = entity_map.get(entity_id)
    if entry_id is None:
        return None
    return _entries(hass).get(entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """配置入口加载。"""
    host = entry.data[CONF_HOST]
    session = async_get_clientsession(hass)
    client = IeastClient(
        host,
        session,
        use_https=entry.options.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS),
    )

    try:
        await client.get_status_ex()
    except (IeastApiError, aiohttp.ClientError) as err:
        raise ConfigEntryNotReady(f"{host} 不可达: {err}") from err

    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    coordinator = IeastCoordinator(hass, client, poll_interval)
    await coordinator.async_config_entry_first_refresh()

    _entries(hass)[entry.entry_id] = {
        "entry_id": entry.entry_id,
        "client": client,
        "coordinator": coordinator,
        "options": {
            CONF_TCP_EXTRAS: entry.options.get(CONF_TCP_EXTRAS, DEFAULT_TCP_EXTRAS),
            CONF_TCP_FRAME_MODE: entry.options.get(
                CONF_TCP_FRAME_MODE, DEFAULT_TCP_FRAME_MODE
            ),
            CONF_GROUP_PASSWORD: entry.options.get(
                CONF_GROUP_PASSWORD, DEFAULT_GROUP_PASSWORD
            ),
        },
        "snapshot": None,
    }
    _store(hass).setdefault("entities", {})

    # 8819 状态推送订阅(实时元数据), 断线自动重连, 随条目卸载自动取消
    if entry.options.get(CONF_PUSH_LISTEN, DEFAULT_PUSH_LISTEN):
        listener = IeastPushListener(host, 8819, coordinator.handle_push)
        _entries(hass)[entry.entry_id]["push_listener"] = listener
        entry.async_create_background_task(
            hass, listener.start(), f"ieast_push_{host}", eager_start=True
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        _entries(hass).pop(entry.entry_id, None)
        entity_map = _store(hass).get("entities", {})
        for eid in [k for k, v in entity_map.items() if v == entry.entry_id]:
            del entity_map[eid]
        if not _entries(hass):
            for service in (
                "party_mode",
                "ungroup_all",
                "save_snapshot",
                "restore_snapshot",
                "announce",
                "page",
                "page_end",
                "alarm_set",
                "alarm_get",
                "alarm_stop",
                "group_volume",
                "stereo_pair_create",
                "stereo_pair_remove",
                "send_http_command",
                "send_tcp_command",
            ):
                hass.services.async_remove(DOMAIN, service)
    return unload_ok


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "party_mode"):
        return

    # ------------------------------------------------------------- 共享辅助

    async def _async_save_snapshots(entity_ids: list[str]) -> None:
        for entity_id in entity_ids:
            entry = _entry_by_entity_id(hass, entity_id)
            if entry is None:
                continue
            player = entry["coordinator"].data.player
            entry["snapshot"] = {key: player.get(key) for key in SNAPSHOT_FIELDS}

    async def _async_restore_snapshots(entity_ids: list[str]) -> None:
        for entity_id in entity_ids:
            entry = _entry_by_entity_id(hass, entity_id)
            if entry is None or not entry.get("snapshot"):
                continue
            client: IeastClient = entry["client"]
            snap = entry["snapshot"]
            try:
                if snap.get("vol") is not None:
                    await client.set_volume(int(snap["vol"]))
                if snap.get("mute") is not None:
                    await client.set_mute(str(snap["mute"]) == "1")
                mode = snap.get("mode")
                status = snap.get("status")
                if mode == "40":
                    await client.switch_mode("line-in")
                elif mode == "41":
                    await client.switch_mode("Bluetooth")
                elif mode == "43":
                    await client.switch_mode("optical")
                if status == "play":
                    await client.resume()
                elif status in ("pause", "stop"):
                    await client.pause()
            except IeastApiError as err:
                _LOGGER.error("恢复 %s 快照失败: %s", entity_id, err)

    async def _async_join_to_master(master_eid: str, member_eids: list[str]) -> None:
        master = _entry_by_entity_id(hass, master_eid)
        if master is None:
            raise HomeAssistantError(f"{master_eid} 不是 iEAST 播放器")
        master_status = master["coordinator"].data.status_ex
        password = master["options"][CONF_GROUP_PASSWORD]
        for member_eid in member_eids:
            member = _entry_by_entity_id(hass, member_eid)
            if member is None:
                _LOGGER.warning("跳过无效成员 %s", member_eid)
                continue
            try:
                await member["client"].join_group(master_status, password)
            except IeastApiError as err:
                _LOGGER.error("%s 加入分组失败: %s", member_eid, err)
                continue
            await asyncio.sleep(1.5)
        master["coordinator"].request_full_refresh()

    # ------------------------------------------------------------- 分组

    async def party_mode(call: ServiceCall) -> None:
        """全屋齐播: 成员设备全部加入主机分组。"""
        members = call.data.get("members")
        if not members:
            master = _entry_by_entity_id(hass, call.data["master"])
            if master is None:
                raise HomeAssistantError(f"{call.data['master']} 不是 iEAST 播放器")
            members = [
                eid
                for eid, entry_id in _store(hass).get("entities", {}).items()
                if entry_id != master["entry_id"]
            ]
        await _async_join_to_master(call.data["master"], members)

    async def ungroup_all(call: ServiceCall) -> None:
        """解散全部分组。"""
        for entry in _entries(hass).values():
            try:
                await entry["client"].ungroup()
            except IeastApiError as err:
                _LOGGER.debug("ungroup %s: %s", entry["client"].host, err)
            entry["coordinator"].request_full_refresh()

    # ------------------------------------------------------------- 快照

    async def save_snapshot(call: ServiceCall) -> None:
        """保存播放器快照（音量/静音/音源/播放状态）。"""
        await _async_save_snapshots(call.data["entity_id"])

    async def restore_snapshot(call: ServiceCall) -> None:
        """恢复播放器快照。"""
        await _async_restore_snapshots(call.data["entity_id"])

    # ------------------------------------------------------------- 呼叫

    async def announce(call: ServiceCall) -> None:
        """插播呼叫: 快照 -> (可选自动入组) -> 播报 -> 恢复快照。"""
        targets = list(call.data["targets"])
        if not targets:
            raise HomeAssistantError("announce 需要至少一个目标")
        media_url = call.data.get("media_url")
        message = call.data.get("message")
        if not media_url and message:
            tts_platform = call.data.get("tts_platform")
            if not tts_platform:
                raise HomeAssistantError(
                    "使用 message 时需同时提供 tts_platform(如 google_translate/cloud)"
                )
            media_url = f"media-source://tts/{tts_platform}?message={quote(message)}"
        if not media_url:
            raise HomeAssistantError("announce 需要 message(+tts_platform) 或 media_url")

        group = call.data.get("group", False)
        restore = call.data.get("restore", True)
        duration = call.data.get("duration", 5)

        await _async_save_snapshots(targets)
        grouped = group and len(targets) > 1
        play_targets = [targets[0]] if grouped else targets
        if grouped:
            await _async_join_to_master(targets[0], targets[1:])
        for entity_id in play_targets:
            hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": entity_id,
                    "media_content_type": "music",
                    "media_content_id": media_url,
                },
                blocking=False,
            )
            await asyncio.sleep(0.4)
        await asyncio.sleep(duration)
        if grouped:
            master = _entry_by_entity_id(hass, targets[0])
            if master is not None:
                try:
                    await master["client"].ungroup()
                except IeastApiError as err:
                    _LOGGER.debug("announce 解组失败: %s", err)
                master["coordinator"].request_full_refresh()
        if restore:
            await _async_restore_snapshots(targets)

    async def page(call: ServiceCall) -> None:
        """实时广播: 目标临时加入主机分组, 主机切 AUX(line-in) 转播。"""
        master_eid = call.data["master"]
        master = _entry_by_entity_id(hass, master_eid)
        if master is None:
            raise HomeAssistantError(f"{master_eid} 不是 iEAST 播放器")
        if master["coordinator"].data and master["coordinator"].data.is_slave:
            raise HomeAssistantError(
                f"{master_eid} 当前是多房间从机, 不能作为广播主机; 请先使其退出分组"
            )
        targets = call.data.get("targets")
        if not targets:
            targets = [
                eid
                for eid, entry_id in _store(hass).get("entities", {}).items()
                if entry_id != master["entry_id"]
            ]
        if not targets:
            raise HomeAssistantError("page 没有可用的目标房间")
        await _async_save_snapshots([master_eid] + targets)
        _store(hass)["page_state"] = {"master": master_eid, "members": list(targets)}
        await _async_join_to_master(master_eid, targets)
        try:
            await master["client"].switch_mode("line-in")
        except IeastApiError as err:
            raise HomeAssistantError(f"切换呼叫音源(line-in)失败: {err}") from err

    async def page_end(call: ServiceCall) -> None:
        """结束广播: 解组并恢复快照。"""
        state = _store(hass).get("page_state")
        if not state:
            raise HomeAssistantError("当前没有进行中的广播(先调用 ieast.page)")
        involved = [state["master"]] + state["members"]
        for entity_id in involved:
            entry = _entry_by_entity_id(hass, entity_id)
            if entry is None:
                continue
            try:
                await entry["client"].ungroup()
            except IeastApiError as err:
                _LOGGER.debug("page_end 解组 %s: %s", entity_id, err)
            entry["coordinator"].request_full_refresh()
        await _async_restore_snapshots(involved)
        _store(hass)["page_state"] = None

    # ------------------------------------------------------------- 闹钟/组音量

    async def alarm_set_action(call: ServiceCall) -> None:
        """设置/取消设备闹钟(设备侧 UTC 时间, 先执行'时间同步'按钮)。"""
        entry = _entry_by_entity_id(hass, call.data["entity_id"])
        if entry is None:
            raise HomeAssistantError(f"{call.data['entity_id']} 不是 iEAST 实体")
        index = call.data["index"]
        client: IeastClient = entry["client"]
        try:
            if not call.data.get("enable", True):
                await client.set_alarm(index, trigger=0)
                return
            trigger = call.data["trigger"]
            trig = {"once": 1, "daily": 2, "weekly": 4}[trigger]
            time_hhmmss = call.data["time"].replace(":", "") + "00"
            day = None
            if trig == 1:
                day = call.data.get("date")
                if not day:
                    raise HomeAssistantError("单次闹钟需提供日期(YYYYMMDD)")
            elif trig == 4:
                bits = 0
                for wd in call.data.get("weekdays") or []:
                    bits |= 1 << int(wd)
                day = f"{bits:02X}"
            await client.set_alarm(
                index,
                trigger=trig,
                operation=1,
                time_hhmmss=time_hhmmss,
                day=day,
                url=call.data.get("url"),
            )
        except IeastApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def alarm_get_action(call: ServiceCall) -> ServiceResponse:
        entry = _entry_by_entity_id(hass, call.data["entity_id"])
        if entry is None:
            raise HomeAssistantError(f"{call.data['entity_id']} 不是 iEAST 实体")
        try:
            return await entry["client"].get_alarm(call.data["index"])
        except IeastApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def alarm_stop_action(call: ServiceCall) -> None:
        entry = _entry_by_entity_id(hass, call.data["entity_id"])
        if entry is None:
            raise HomeAssistantError(f"{call.data['entity_id']} 不是 iEAST 实体")
        try:
            await entry["client"].stop_alarm()
        except IeastApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def group_volume_action(call: ServiceCall) -> ServiceResponse:
        """分组批量音量: 主机+全部从机一次到位(带响应报告每台结果)。"""
        entry = _entry_by_entity_id(hass, call.data["entity_id"])
        if entry is None:
            raise HomeAssistantError(f"{call.data['entity_id']} 不是 iEAST 实体")
        volume = call.data.get("volume")
        mute = call.data.get("mute")
        if volume is None and mute is None:
            raise HomeAssistantError("group_volume 需要提供 volume 或 mute 之一")
        client: IeastClient = entry["client"]
        results: dict[str, Any] = {}
        try:
            if volume is not None:
                await client.set_volume(volume)
            if mute is not None:
                await client.set_mute(mute)
            results["master"] = "ok"
        except IeastApiError as err:
            results["master"] = str(err)
        slaves = entry["coordinator"].data.slaves if entry["coordinator"].data else []
        for slave in slaves:
            ip = str(slave.get("ip") or "")
            if not ip:
                continue
            try:
                if volume is not None:
                    await client.set_slave_volume(ip, volume)
                if mute is not None:
                    await client.set_slave_mute(ip, mute)
                results[ip] = "ok"
            except IeastApiError as err:
                results[ip] = str(err)
        entry["coordinator"].request_full_refresh()
        return results

    # ------------------------------------------------------------- 立体声对

    def _device_ip(entry: dict[str, Any]) -> str | None:
        if entry["coordinator"].data is None:
            return None
        ex = entry["coordinator"].data.status_ex
        for key in ("apcli0", "eth0"):
            ip = str(ex.get(key) or "")
            if ip and ip != "0.0.0.0":
                return ip
        return None

    async def stereo_pair_create(call: ServiceCall) -> None:
        """立体声对: 右声道设备加入左声道(主机)分组, 左=左声道, 右=右声道。"""
        left_eid = call.data["left"]
        right_eid = call.data["right"]
        if left_eid == right_eid:
            raise HomeAssistantError("左右声道不能是同一台设备")
        left = _entry_by_entity_id(hass, left_eid)
        right = _entry_by_entity_id(hass, right_eid)
        if left is None or right is None:
            raise HomeAssistantError("left/right 必须是 iEAST 播放器")
        try:
            vol = int(left["coordinator"].data.player.get("vol") or 30)
            await right["client"].set_volume(vol)
            await right["client"].join_group(
                left["coordinator"].data.status_ex, left["options"][CONF_GROUP_PASSWORD]
            )
            await asyncio.sleep(1.5)
            right_ip = _device_ip(right)
            if not right_ip:
                raise HomeAssistantError("无法获取右声道设备 IP")
            await left["client"].set_channel(1)
            await left["client"].set_slave_channel(right_ip, 2)
        except IeastApiError as err:
            raise HomeAssistantError(f"立体声对创建失败: {err}") from err
        _store(hass).setdefault("stereo_pairs", {})[left_eid] = {"right": right_eid}
        left["coordinator"].request_full_refresh()
        right["coordinator"].request_full_refresh()

    async def stereo_pair_remove(call: ServiceCall) -> None:
        """拆散立体声对: 解组并恢复双声道。"""
        left_eid = call.data["left"]
        pairs = _store(hass).setdefault("stereo_pairs", {})
        pair = pairs.pop(left_eid, None)
        right_eid = (pair or {}).get("right") or call.data.get("right")
        left = _entry_by_entity_id(hass, left_eid)
        right = _entry_by_entity_id(hass, right_eid) if right_eid else None
        for entry in (left, right):
            if entry is None:
                continue
            try:
                await entry["client"].ungroup()
            except IeastApiError:
                pass
            try:
                await entry["client"].set_channel(0)
            except IeastApiError as err:
                _LOGGER.warning("恢复声道失败: %s", err)
            entry["coordinator"].request_full_refresh()

    # ------------------------------------------------------------- 调试

    async def send_http_command_action(call: ServiceCall) -> ServiceResponse:
        """直接发送 httpapi 命令（调试/扩展）。"""
        entry = _entry_by_entity_id(hass, call.data["entity_id"])
        if entry is None:
            raise HomeAssistantError(f"{call.data['entity_id']} 不是 iEAST 实体")
        try:
            result = await entry["client"].get_json(call.data["command"])
        except IeastApiError as err:
            raise HomeAssistantError(str(err)) from err
        return {"response": result}

    async def send_tcp_command_action(call: ServiceCall) -> ServiceResponse:
        """直接发送 TCP 8899 MCU 指令（调试/扩展）。"""
        entry = _entry_by_entity_id(hass, call.data["entity_id"])
        if entry is None:
            raise HomeAssistantError(f"{call.data['entity_id']} 不是 iEAST 实体")
        frame_mode = call.data.get("frame_mode") or entry["options"][CONF_TCP_FRAME_MODE]
        try:
            payloads: list[str] = await entry["client"].tcp_command(
                call.data["command"], frame_mode
            )
        except IeastTcpError as err:
            raise HomeAssistantError(str(err)) from err
        return {"response": payloads}

    # ------------------------------------------------------------- 注册

    hass.services.async_register(
        DOMAIN,
        "party_mode",
        party_mode,
        schema=vol.Schema(
            {
                vol.Required("master"): cv.entity_id,
                vol.Optional("members"): vol.All(cv.ensure_list, [cv.entity_id]),
            }
        ),
    )
    hass.services.async_register(DOMAIN, "ungroup_all", ungroup_all, schema=vol.Schema({}))
    hass.services.async_register(
        DOMAIN,
        "save_snapshot",
        save_snapshot,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_ids}),
    )
    hass.services.async_register(
        DOMAIN,
        "restore_snapshot",
        restore_snapshot,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_ids}),
    )
    hass.services.async_register(
        DOMAIN,
        "announce",
        announce,
        schema=vol.Schema(
            {
                vol.Required("targets"): vol.All(cv.ensure_list, [cv.entity_id]),
                vol.Optional("message"): str,
                vol.Optional("media_url"): str,
                vol.Optional("tts_platform"): str,
                vol.Optional("group", default=False): bool,
                vol.Optional("restore", default=True): bool,
                vol.Optional("duration", default=5): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=120)
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "page",
        page,
        schema=vol.Schema(
            {
                vol.Required("master"): cv.entity_id,
                vol.Optional("targets"): vol.All(cv.ensure_list, [cv.entity_id]),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "page_end",
        page_end,
        schema=vol.Schema({vol.Optional("master"): cv.entity_id}),
    )
    hass.services.async_register(
        DOMAIN,
        "alarm_set",
        alarm_set_action,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_id,
                vol.Required("index"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
                vol.Optional("enable", default=True): bool,
                vol.Optional("trigger"): vol.In(["once", "daily", "weekly"]),
                vol.Optional("time"): vol.Match(r"^\d{2}:\d{2}$"),
                vol.Optional("date"): vol.Match(r"^\d{8}$"),
                vol.Optional("weekdays"): vol.All(
                    cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=6))]
                ),
                vol.Optional("url"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "alarm_get",
        alarm_get_action,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_id,
                vol.Required("index"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
            }
        ),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        "alarm_stop",
        alarm_stop_action,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_id}),
    )
    hass.services.async_register(
        DOMAIN,
        "group_volume",
        group_volume_action,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_id,
                vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Optional("mute"): bool,
            }
        ),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        "stereo_pair_create",
        stereo_pair_create,
        schema=vol.Schema(
            {
                vol.Required("left"): cv.entity_id,
                vol.Required("right"): cv.entity_id,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "stereo_pair_remove",
        stereo_pair_remove,
        schema=vol.Schema(
            {
                vol.Required("left"): cv.entity_id,
                vol.Optional("right"): cv.entity_id,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "send_http_command",
        send_http_command_action,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_id,
                vol.Required("command"): str,
            }
        ),
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        "send_tcp_command",
        send_tcp_command_action,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_id,
                vol.Required("command"): str,
                vol.Optional("frame_mode"): vol.In(["auto", "token", "doc"]),
            }
        ),
        supports_response=True,
    )
