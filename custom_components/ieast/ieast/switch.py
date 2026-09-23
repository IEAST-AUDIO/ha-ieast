"""iEAST switch 平台。

- 触发输入 / 勿扰模式 / 指示灯: TCP 8899 透传, 乐观状态(尽力而为)
- Maxx3D / Maxx 总开关 / Leveler: BP10 家族 DSP(MCU+PAS HTTP 透传), 机型探测挂载
"""

from __future__ import annotations

import logging
import re

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import IeastApiError
from .button import IeastButtonBase
from .const import CONF_TCP_EXTRAS, DOMAIN
from .coordinator import IeastCoordinator
from .dsp import dsp_maxx_enable, dsp_param_set, resolve_param
from .entity import IeastDspEntity

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
    caps = entry_data.get("dsp")
    if caps is not None and caps.params:
        # 参数级挂载: 只挂查询应答有效的参数; DPEA 需 DPST(v3517+) 能力
        if (2, 0) in caps.params:
            entities.append(
                IeastDspParamSwitch(coordinator, entry.entry_id, "maxx3d",
                                    "环绕声 Maxx3D", "mdi:surround-sound",
                                    on_value=0x00, off_value=0x05,
                                    initial_value=caps.params[(2, 0)])
            )
        if (5, 0) in caps.params:
            entities.append(
                IeastDspParamSwitch(coordinator, entry.entry_id, "leveler",
                                    "自动电平", "mdi:volume-equal",
                                    on_value=1, off_value=0,
                                    initial_value=caps.params[(5, 0)])
            )
        # DPEA 需 DPST(v3517+); 215(family=2) 无 Maxx 算法, 即使有 DPST 也不挂
        if caps.has_diag and caps.family != 2:
            entities.append(IeastMaxxEnableSwitch(coordinator, entry.entry_id))
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


class IeastDspParamSwitch(IeastDspEntity, SwitchEntity):
    """DSP 参数型开关。仅当查询应答有效才挂载, 初态来自查询应答。"""

    def __init__(
        self,
        coordinator: IeastCoordinator,
        entry_id: str,
        param: str,
        name: str,
        icon: str,
        on_value: int,
        off_value: int,
        initial_value: int,
    ) -> None:
        super().__init__(coordinator, entry_id, f"sw-{param}")
        self._group, self._item = resolve_param(param) or (0, 0)
        self._attr_name = name
        self._attr_icon = icon
        self._on_value = on_value
        self._off_value = off_value
        self._state = initial_value == on_value

    @property
    def is_on(self) -> bool:
        return self._state

    async def _apply(self, value: int) -> None:
        try:
            await dsp_param_set(self.coordinator.client, self._group, self._item, value)
        except IeastApiError as err:
            raise HomeAssistantError(f"{self.device.get('DeviceName')}: {err}") from err
        self._state = value == self._on_value
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self._apply(self._on_value)

    async def async_turn_off(self) -> None:
        await self._apply(self._off_value)


class IeastMaxxEnableSwitch(IeastDspEntity, SwitchEntity):
    """Maxx 算法总开关(DPEA, 查询 DPEA& / 设置 DPEA<0|1>&)。"""

    _attr_name = "Maxx 音效总开关"
    _attr_icon = "mdi:audio-settings"

    def __init__(self, coordinator: IeastCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "sw-maxx-enable")
        self._state = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        try:
            text = await self.coordinator.client.passthrough("DPEA")
            m = re.search(r"DPEA(\d)", text)
            if m:
                self._state = m.group(1) == "1"
                self.async_write_ha_state()
        except IeastApiError as err:
            _LOGGER.debug("DPEA 回读失败: %s", err)

    @property
    def is_on(self) -> bool:
        return self._state

    async def _set(self, enable: bool) -> None:
        try:
            value = await dsp_maxx_enable(self.coordinator.client, enable)
        except IeastApiError as err:
            raise HomeAssistantError(f"{self.device.get('DeviceName')}: {err}") from err
        self._state = value == 1
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self._set(True)

    async def async_turn_off(self) -> None:
        await self._set(False)
