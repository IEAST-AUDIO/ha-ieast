"""iEAST sensor 平台: WiFi 信号强度。"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import IeastCoordinator
from .entity import IeastEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN]["entries"][entry.entry_id]["coordinator"]
    async_add_entities([IeastRssiSensor(coordinator, entry.entry_id)])


class IeastRssiSensor(IeastEntity, SensorEntity):
    """WiFi RSSI (诊断)。"""

    _attr_name = "WiFi 信号"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{self.uuid or coordinator.client.host}-rssi"

    @property
    def native_value(self) -> int | None:
        rssi: Any = self.device.get("RSSI")
        try:
            return int(rssi)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self):
        dev = self.device
        return {
            "ssid": dev.get("essid"),
            "ip": dev.get("apcli0"),
            "eth": dev.get("eth0"),
            "internet": dev.get("internet"),
            "wmrm_version": dev.get("wmrm_version"),
        }
