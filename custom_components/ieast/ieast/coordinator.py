"""iEAST 设备数据协调器。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IeastApiError, IeastClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class IeastData:
    """单台设备的聚合状态。"""

    status_ex: dict[str, Any] = field(default_factory=dict)
    player: dict[str, Any] = field(default_factory=dict)
    eq_list: list[str] = field(default_factory=list)
    slaves: list[dict[str, Any]] = field(default_factory=list)
    shutdown_sec: int = 0
    push_meta: dict[str, str] = field(default_factory=dict)  # 8819 推送的实时元数据

    @property
    def uuid(self) -> str:
        return str(self.status_ex.get("uuid", ""))

    @property
    def device_name(self) -> str:
        return str(self.status_ex.get("DeviceName") or self.status_ex.get("ssid") or "iEAST")

    @property
    def is_grouped_master(self) -> bool:
        """本机是主机且组内有从机。"""
        return self.slaves and not self.player.get("type") == "1"

    @property
    def is_slave(self) -> bool:
        return str(self.status_ex.get("group", "0")) == "1" or str(self.player.get("type")) == "1"


class IeastCoordinator(DataUpdateCoordinator[IeastData]):
    """轮询 getPlayerStatus, 周期性补充 getStatusEx / 分组 / 定时信息。"""

    def __init__(
        self,
        hass: HomeAssistant,
        client: IeastClient,
        poll_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"ieast_{client.host}",
            update_interval=timedelta(seconds=poll_interval),
            # 控制命令后的刷新走 1.5s 防抖, 让音量/播放状态变化更快回显
            refresh_debouncer=Debouncer(hass, _LOGGER, cooldown=1.5, immediate=False),
        )
        self.client = client
        self._counter = 0
        self._force_full = False

    def request_full_refresh(self) -> None:
        """下次刷新强制全量(设备信息/分组), 用于入组/退组后立即生效。"""
        self._force_full = True
        self.async_request_refresh()

    def handle_push(self, msg: dict[str, Any]) -> None:
        """8819 推送回调: 合并元数据/播放状态并即时刷新实体(不等待轮询)。"""
        data = self.data
        if data is None:
            return
        if msg.get("kind") == "meta":
            meta = dict(data.push_meta)
            for key in ("title", "artist", "album", "station"):
                if msg.get(key):
                    meta[key] = msg[key]
            self.async_set_updated_data(replace(data, push_meta=meta))
        elif msg.get("kind") == "state":
            player = dict(data.player)
            player["status"] = "play" if msg.get("playing") else "stop"
            self.async_set_updated_data(replace(data, player=player))

    async def _async_update_data(self) -> IeastData:
        counter = self._counter
        self._counter = (counter + 1) % 100000
        full = self._force_full or counter % 5 == 0
        self._force_full = False

        data = self.data or IeastData()
        try:
            player = await self.client.get_player_status()
            status_ex = data.status_ex
            eq_list = data.eq_list
            slaves = data.slaves
            shutdown_sec = data.shutdown_sec
            if full or not status_ex:
                status_ex = await self.client.get_status_ex()
            if full or not eq_list:
                eq_list = await self.client.get_eq_list()
            if full:
                try:
                    slaves = await self.client.get_group()
                except IeastApiError:
                    slaves = []
                try:
                    shutdown_sec = await self.client.get_shutdown()
                except IeastApiError:
                    shutdown_sec = 0
        except IeastApiError as err:
            raise UpdateFailed(str(err)) from err

        return IeastData(
            status_ex=status_ex,
            player=player,
            eq_list=eq_list,
            slaves=slaves,
            shutdown_sec=shutdown_sec,
        )
