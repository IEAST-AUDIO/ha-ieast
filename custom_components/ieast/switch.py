"""iEAST switch 平台。

- 触发输入 / 勿扰模式 / 指示灯: TCP 8899 透传, 乐观状态(尽力而为)
"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .button import IeastButtonBase
from .const import CONF_TCP_EXTRAS, DOMAIN
from .coordinator import IeastCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN]["entries"][entry.entry_id]
    options = entry_data["options"]
    coordinator = entry_data["coordinator"]
    entities: list[SwitchEntity] = []
    if options.get(CONF_TCP_EXTRAS, True):
        entities += [
            IeastTriggerSwitch(coordinator, entry.entry_id),
            IeastDndSwitch(coordinator, entry.entry_id),
            IeastLedSwitch(coordinator, entry.entry_id),
        ]
    async_add_entities(entities)


class IeastTcpSwitch(IeastButtonBase, SwitchEntity):
    """TCP 开关基类: 乐观状态。"""

    def __init__(self, coordinator: IeastCoordinator, entry_id: str, key: str) -> None:
        super().__init__(coordinator, entry_id, key)
        self._optimistic_state = False

    @property
    def is_on(self) -> bool:
        return self._optimistic_state

    async def _send_and_set(self, command: str, state: bool) -> None:
        await self._tcp(command)
        self._optimistic_state = state
        self.async_write_ha_state()


class IeastTriggerSwitch(IeastTcpSwitch):
    """TRIGGER IN: 高电平开机/低电平待机联动。"""

    _attr_name = "触发输入联动"
    _attr_icon = "mdi:toggle-switch-outline"

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "trigger-in")

    async def async_turn_on(self) -> None:
        await self._send_and_set("MCU+PAS+TIN&", True)

    async def async_turn_off(self) -> None:
        await self._send_and_set("MCU+PAS+TIF&", False)


class IeastDndSwitch(IeastTcpSwitch):
    """全局勿扰模式。"""

    _attr_name = "勿扰模式"
    _attr_icon = "mdi:bell-off-outline"

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "dnd")

    async def async_turn_on(self) -> None:
        await self._send_and_set("MCU+PAS+DBO&", True)

    async def async_turn_off(self) -> None:
        await self._send_and_set("MCU+PAS+DBF&", False)


class IeastLedSwitch(IeastTcpSwitch):
    """显示背光/指示灯。"""

    _attr_name = "指示灯"
    _attr_icon = "mdi:led-on"

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "led")

    async def async_turn_on(self) -> None:
        await self._send_and_set("MCU+PAS+SLCOF&", True)

    async def async_turn_off(self) -> None:
        await self._send_and_set("MCU+PAS+SLCON&", False)


