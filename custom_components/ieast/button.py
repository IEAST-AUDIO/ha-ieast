"""iEAST button 平台: 重启 / 时间同步 / 播放预设 / 播放上次列表。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import IeastApiError, IeastTcpError
from .const import CONF_TCP_EXTRAS, CONF_TCP_FRAME_MODE, DOMAIN
from .coordinator import IeastCoordinator
from .entity import IeastEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN]["entries"][entry.entry_id]
    coordinator: IeastCoordinator = entry_data["coordinator"]
    options = entry_data["options"]

    entities: list[ButtonEntity] = [
        IeastRestartButton(coordinator, entry.entry_id),
        IeastTimeSyncButton(coordinator, entry.entry_id),
    ]
    if options.get(CONF_TCP_EXTRAS, True):
        preset_count = _int_of(coordinator.data.status_ex.get("preset_key") if coordinator.data else 0)
        for idx in range(1, min(max(preset_count, 6), 10) + 1):
            entities.append(IeastPresetButton(coordinator, entry.entry_id, idx))
        entities.append(IeastReplayButton(coordinator, entry.entry_id))
    async_add_entities(entities)


def _int_of(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class IeastButtonBase(IeastEntity, ButtonEntity):
    """iEAST 按钮基类。"""

    def __init__(self, coordinator: IeastCoordinator, entry_id: str, key: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{self.uuid or coordinator.client.host}-{key}"

    def _option(self, key: str, default=None):
        return self.hass.data[DOMAIN]["entries"][self._entry_id]["options"].get(key, default)

    async def _http(self, method, *args) -> None:
        try:
            await method(*args)
        except IeastApiError as err:
            raise HomeAssistantError(f"{self.device.get('DeviceName')}: {err}") from err

    async def _tcp(self, command: str) -> list[str]:
        try:
            return await self.coordinator.client.tcp_command(
                command, self._option(CONF_TCP_FRAME_MODE, "auto")
            )
        except IeastTcpError as err:
            raise HomeAssistantError(f"{self.device.get('DeviceName')}: {err}") from err


class IeastRestartButton(IeastButtonBase):
    """重启设备 (HTTP: reboot)。"""

    _attr_name = "重启设备"
    _attr_icon = "mdi:restart"
    _attr_entity_category = "diagnostic"

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "restart")

    async def async_press(self) -> None:
        await self._http(self.coordinator.client.reboot)


class IeastTimeSyncButton(IeastButtonBase):
    """把 HA 当前 UTC 时间同步给设备(设备闹钟依赖)。"""

    _attr_name = "时间同步"
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_category = "diagnostic"

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "time-sync")

    async def async_press(self) -> None:
        await self._http(self.coordinator.client.time_sync, datetime.now(timezone.utc))


class IeastPresetButton(IeastButtonBase):
    """播放设备端预设 N (TCP: MCU+KEY+xNN&)。"""

    _attr_icon = "mdi:pound-box-outline"

    def __init__(self, coordinator: IeastCoordinator, entry_id: str, index: int) -> None:
        super().__init__(coordinator, entry_id, f"preset-{index}")
        self._index = index
        self._attr_name = f"预设 {index}"

    async def async_press(self) -> None:
        await self._tcp(f"MCU+KEY+x{self._index:02d}&")


class IeastReplayButton(IeastButtonBase):
    """播放上一次的播放列表 (TCP: MCU+PLY+PUQ)。"""

    _attr_name = "播放上次列表"
    _attr_icon = "mdi:playlist-play"

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "replay-last")

    async def async_press(self) -> None:
        await self._tcp("MCU+PLY+PUQ")


