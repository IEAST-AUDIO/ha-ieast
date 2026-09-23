"""iEAST number 平台: 睡眠关机定时 + DSP 友好参数(机型探测挂载)。"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import IeastApiError
from .const import DOMAIN
from .coordinator import IeastCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN]["entries"][entry.entry_id]
    coordinator = entry_data["coordinator"]
    entities: list[NumberEntity] = [
        IeastSleepTimerNumber(coordinator, entry.entry_id)
    ]
    async_add_entities(entities)


class IeastSleepTimerNumber(IeastEntity, NumberEntity):
    """睡眠定时(分钟)。0 = 取消定时。

    设备端 setShutdown:秒, 0 为立即关机, -1 为取消。
    为避免误触立即关机, 本实体把 0 定义为"取消定时"。
    """

    _attr_name = "睡眠定时"
    _attr_icon = "mdi:sleep"
    _attr_native_min_value = 0
    _attr_native_max_value = 720
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.SLIDE

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{self.uuid or coordinator.client.host}-sleep-timer"

    @property
    def native_value(self) -> float | None:
        return round(self.coordinator.data.shutdown_sec / 60)

    async def async_set_native_value(self, value: float) -> None:
        minutes = int(value)
        seconds = -1 if minutes <= 0 else minutes * 60
        try:
            await self.coordinator.client.set_shutdown(seconds)
        except IeastApiError as err:
            raise HomeAssistantError(f"{self.device.get('DeviceName')}: {err}") from err
        self.coordinator.request_full_refresh()


