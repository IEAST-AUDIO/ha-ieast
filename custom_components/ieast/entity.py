"""iEAST 实体公共基类。"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import IeastCoordinator


class IeastEntity(CoordinatorEntity[IeastCoordinator]):
    """基于协调器的 iEAST 实体基类。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: IeastCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = None  # 由子类覆盖

    @property
    def device(self) -> dict[str, Any]:
        return self.coordinator.data.status_ex if self.coordinator.data else {}

    @property
    def player(self) -> dict[str, Any]:
        return self.coordinator.data.player if self.coordinator.data else {}

    @property
    def uuid(self) -> str:
        return self.coordinator.data.uuid if self.coordinator.data else ""

    @property
    def device_info(self) -> DeviceInfo:
        dev = self.device
        model = str(dev.get("project") or dev.get("ssid") or "iEAST Stream")
        return DeviceInfo(
            identifiers={(DOMAIN, self.uuid or self.coordinator.client.host)},
            name=dev.get("DeviceName") or dev.get("ssid") or "iEAST",
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=str(dev.get("firmware", "")) or None,
            serial=str(dev.get("uuid", "")) or None,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and bool(self.coordinator.data)


