"""iEAST media_player 平台。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import IeastApiError, decode_meta
from .const import LOOP_SHUFFLE_REPEAT, MODE_INFO, SOURCE_COMMANDS
from .coordinator import IeastCoordinator
from .entity import IeastEntity

_LOGGER = logging.getLogger(__name__)

_FEATURES = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.SELECT_SOUND_MODE
    | MediaPlayerEntityFeature.GROUPING
)

_STATE_MAP = {
    "play": MediaPlayerState.PLAYING,
    "loading": MediaPlayerState.BUFFERING,
    "pause": MediaPlayerState.PAUSED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data["ieast"]["entries"][entry.entry_id]["coordinator"]
    async_add_entities([IeastMediaPlayer(coordinator, entry.entry_id)])


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class IeastMediaPlayer(IeastEntity, MediaPlayerEntity):
    """iEAST 播放器实体。"""

    _attr_name = None
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = _FEATURES

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{self.uuid or coordinator.client.host}_player"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        entities = self.hass.data.setdefault("ieast", {}).setdefault("entities", {})
        entities[self.entity_id] = self._entry_id

    async def async_will_remove_from_hass(self) -> None:
        entities = self.hass.data.get("ieast", {}).get("entities", {})
        entities.pop(self.entity_id, None)
        await super().async_will_remove_from_hass()

    # ------------------------------------------------------------ 状态属性

    @property
    def is_slave(self) -> bool:
        return self.coordinator.data.is_slave

    @property
    def state(self) -> MediaPlayerState | None:
        status = str(self.player.get("status", "stop"))
        if status == "stop":
            if self.is_slave and _int(self.player.get("mode")) == 99:
                return MediaPlayerState.PLAYING
            return MediaPlayerState.IDLE
        return _STATE_MAP.get(status, MediaPlayerState.IDLE)

    @property
    def volume_level(self) -> float | None:
        vol = _int(self.player.get("vol"), -1)
        return None if vol < 0 else vol / 100

    @property
    def is_volume_muted(self) -> bool | None:
        mute = self.player.get("mute")
        return None if mute is None else str(mute) == "1"

    @property
    def media_title(self) -> str | None:
        push = self.coordinator.data.push_meta
        title = (
            decode_meta(self.player.get("Title"))
            or push.get("title")
            or decode_meta(self.player.get("station"))
            or push.get("station")
        )
        if self.is_slave:
            group_name = self.device.get("GroupName")
            own_name = self.device.get("DeviceName")
            if group_name and group_name != own_name:
                return f"同步自 {group_name}" + (f" · {title}" if title else "")
        return title or None

    @property
    def media_artist(self) -> str | None:
        if self.is_slave:
            return None
        return decode_meta(self.player.get("Artist")) or self.coordinator.data.push_meta.get(
            "artist"
        )

    @property
    def media_album_name(self) -> str | None:
        if self.is_slave:
            return None
        return decode_meta(self.player.get("Album")) or self.coordinator.data.push_meta.get(
            "album"
        )

    @property
    def media_duration(self) -> int | None:
        totlen = _int(self.player.get("totlen"), -1)
        return None if totlen <= 0 else totlen // 1000

    @property
    def media_position(self) -> int | None:
        if self.is_slave:
            return None
        curpos = _int(self.player.get("curpos"), -1)
        return None if curpos < 0 else curpos // 1000

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self.coordinator.last_update_success_time

    @property
    def shuffle(self) -> bool | None:
        mapping = LOOP_SHUFFLE_REPEAT.get(_int(self.player.get("loop"), -1))
        return mapping[0] if mapping else None

    @property
    def repeat(self) -> RepeatMode | None:
        mapping = LOOP_SHUFFLE_REPEAT.get(_int(self.player.get("loop"), -1))
        if not mapping:
            return None
        return RepeatMode(mapping[1])

    @property
    def source(self) -> str | None:
        mode = _int(self.player.get("mode"))
        info = MODE_INFO.get(mode)
        return info[1] if info else str(mode)

    @property
    def source_list(self) -> list[str] | None:
        return list(SOURCE_COMMANDS)

    @property
    def sound_mode(self) -> str | None:
        eq_list = self.coordinator.data.eq_list
        eq = _int(self.player.get("eq"), -1)
        if 0 <= eq < len(eq_list):
            return eq_list[eq]
        return None

    @property
    def sound_mode_list(self) -> list[str] | None:
        return self.coordinator.data.eq_list or None

    @property
    def app_name(self) -> str | None:
        info = MODE_INFO.get(_int(self.player.get("mode")))
        return info[1] if info else None

    @property
    def group_members(self) -> list[str] | None:
        """主机的组员列表; 从机不展示(信息在主机侧)。"""
        if self.is_slave or self.entity_id is None:
            return None
        store = self.hass.data.get("ieast", {})
        entity_map = store.get("entities", {})
        entries = store.get("entries", {})
        slave_uuids = {
            str(s.get("uuid", "")).upper() for s in self.coordinator.data.slaves
        }
        members = [
            eid
            for eid, entry_id in entity_map.items()
            if (cfg := entries.get(entry_id))
            and cfg["coordinator"].data
            and cfg["coordinator"].data.uuid.upper() in slave_uuids
        ]
        return [self.entity_id] + members

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs: dict[str, Any] = {
            "group_name": self.device.get("GroupName"),
            "group_state": (
                "master" if self.coordinator.data.slaves else "slave" if self.is_slave else "standalone"
            ),
        }
        if self.coordinator.data.slaves:
            attrs["group_slaves"] = [
                {
                    "name": s.get("name"),
                    "ip": s.get("ip"),
                    "uuid": s.get("uuid"),
                    "volume": s.get("volume"),
                    "mute": s.get("mute"),
                    "channel": s.get("channel"),
                }
                for s in self.coordinator.data.slaves
            ]
        pairs = self.hass.data.get("ieast", {}).get("stereo_pairs", {})
        if pairs.get(self.entity_id):
            attrs["stereo_role"] = "left"
        elif any(p.get("right") == self.entity_id for p in pairs.values()):
            attrs["stereo_role"] = "right"
        return attrs

    # ------------------------------------------------------------ 控制命令

    @property
    def client(self):
        return self.coordinator.client

    async def _call(self, method, *args):
        try:
            result = await method(*args)
        except IeastApiError as err:
            raise HomeAssistantError(f"{self.device.get('DeviceName')}: {err}") from err
        self.coordinator.async_request_refresh()
        return result

    async def async_media_play(self) -> None:
        await self._call(self.client.resume)

    async def async_media_pause(self) -> None:
        await self._call(self.client.pause)

    async def async_media_stop(self) -> None:
        await self._call(self.client.stop)

    async def async_media_next_track(self) -> None:
        await self._call(self.client.next_track)

    async def async_media_previous_track(self) -> None:
        await self._call(self.client.prev_track)

    async def async_media_seek(self, position: float) -> None:
        await self._call(self.client.seek, int(position))

    async def async_set_volume_level(self, volume: float) -> None:
        await self._call(self.client.set_volume, round(volume * 100))

    async def async_volume_up(self) -> None:
        try:
            await self.client.volume_up()
        except IeastApiError:
            # 旧固件无 Vol++ 指令, 退回绝对音量
            current = round((self.volume_level or 0) * 100)
            await self._call(self.client.set_volume, min(100, current + 5))
        self.coordinator.async_request_refresh()

    async def async_volume_down(self) -> None:
        try:
            await self.client.volume_down()
        except IeastApiError:
            current = round((self.volume_level or 0) * 100)
            await self._call(self.client.set_volume, max(0, current - 5))
        self.coordinator.async_request_refresh()

    async def async_mute_volume(self, mute: bool) -> None:
        await self._call(self.client.set_mute, mute)

    async def async_set_shuffle(self, shuffle: bool) -> None:
        loop = _int(self.player.get("loop"), 4)
        if shuffle:
            mode = 2 if loop in (0, 2, -1) else 3
        else:
            mode = 0 if loop in (0, 2, -1) else 4
        await self._call(self.client.set_loop_mode, mode)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        loop = _int(self.player.get("loop"), 4)
        if repeat == RepeatMode.ONE:
            mode = 1
        elif repeat == RepeatMode.ALL:
            mode = 2 if loop in (2, 3) else 0
        else:
            mode = 3 if loop in (2, 3) else 4
        await self._call(self.client.set_loop_mode, mode)

    async def async_select_source(self, source: str) -> None:
        command = SOURCE_COMMANDS.get(source)
        if command is None:
            raise HomeAssistantError(f"未知音源: {source}")
        await self._call(self.client.switch_mode, command)

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        await self._call(self.client.eq_load, sound_mode)

    async def async_play_media(
        self, media_content_type: str, media_content_id: str, **kwargs: Any
    ) -> None:
        if media_content_id.startswith("media-source://"):
            # TTS / 本地媒体等 HA 内部 URI -> 解析为设备可拉取的绝对 URL
            from homeassistant.components import media_source
            from homeassistant.helpers.network import get_url

            try:
                resolved = media_source.async_resolve_media(
                    self.hass, media_content_id, self.entity_id
                )
            except ValueError as err:
                raise HomeAssistantError(f"媒体源解析失败: {err}") from err
            media_content_id = resolved.url
            if media_content_id.startswith("/"):
                # 解析出相对路径时(HA 未配 base_url 等), 补上本实例可达地址
                try:
                    media_content_id = get_url(self.hass, prefer_internal=True) + media_content_id
                except ValueError as err:
                    raise HomeAssistantError(
                        f"无法生成设备可访问的 URL, 请在 HA 中配置内网/外网地址: {err}"
                    ) from err
        if media_content_type == MediaType.PLAYLIST:
            index = int(kwargs.get("index") or 0)
            await self._call(self.client.play_playlist, media_content_id, index)
        else:
            await self._call(self.client.play_url, media_content_id)

    # ------------------------------------------------------------ 多房间

    async def async_join_players(self, group_members: list[str]) -> None:
        """把 group_members 中的播放器加入本机分组(本机为主机)。"""
        if self.is_slave:
            raise HomeAssistantError("从机不能作为主机, 请先让其退出当前分组")
        master_status = self.coordinator.data.status_ex
        store = self.hass.data["ieast"]
        password = store["entries"][self._entry_id]["options"].get("group_password", "")
        for member_eid in group_members:
            entry_id = store.get("entities", {}).get(member_eid)
            member_entry = store["entries"].get(entry_id) if entry_id else None
            if member_entry is None:
                raise HomeAssistantError(f"{member_eid} 不是 iEAST 播放器")
            try:
                await member_entry["client"].join_group(master_status, password)
            except IeastApiError as err:
                raise HomeAssistantError(f"{member_eid} 加入分组失败: {err}") from err
            await asyncio.sleep(1.5)
        self.coordinator.request_full_refresh()

    async def async_unjoin_player(self) -> None:
        """本机退出分组; 若本机为主机则解散整组。"""
        try:
            await self.client.ungroup()
        except IeastApiError as err:
            raise HomeAssistantError(f"退出分组失败: {err}") from err
        self.coordinator.request_full_refresh()
