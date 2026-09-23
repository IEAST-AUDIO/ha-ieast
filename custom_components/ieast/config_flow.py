"""iEAST Audio 配置流。"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import IeastApiError, IeastClient
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
    IEAST_NAME_PREFIXES,
)

_LOGGER = logging.getLogger(__name__)


async def probe_device(hass: HomeAssistant, host: str) -> dict[str, Any] | None:
    """探测主机是否为 iEAST 设备, 返回 getStatusEx 数据。"""
    session = async_get_clientsession(hass)
    client = IeastClient(host, session)
    try:
        return await client.get_status_ex()
    except (IeastApiError, aiohttp.ClientError, OSError):
        return None


def _looks_like_ieast(name: str | None) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return any(lowered.startswith(prefix) or prefix in lowered for prefix in IEAST_NAME_PREFIXES)


class IeastConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理 iEAST 发现与手动添加。"""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] | None = None
        self._discovered_host: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            status = await probe_device(self.hass, host)
            if status is None:
                errors["base"] = "cannot_connect"
            else:
                uuid = str(status.get("uuid", host))
                await self.async_set_unique_id(uuid)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=str(status.get("DeviceName") or host),
                    data={CONF_HOST: host},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> FlowResult:
        name = discovery_info.name or ""
        if not _looks_like_ieast(name):
            return self.async_abort(reason="not_ieast_device")
        host = discovery_info.host.removesuffix(".")
        status = await probe_device(self.hass, host)
        if status is None:
            return self.async_abort(reason="cannot_connect")
        uuid = str(status.get("uuid", ""))
        if not uuid:
            return self.async_abort(reason="not_ieast_device")
        await self.async_set_unique_id(uuid)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: host},
        )
        self._discovered = status
        self._discovered_host = host
        self.context["title_placeholders"] = {
            CONF_NAME: str(status.get("DeviceName") or name)
        }
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=str(self._discovered.get("DeviceName") or self._discovered_host),
                data={CONF_HOST: self._discovered_host},
            )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                CONF_NAME: str(self._discovered.get("DeviceName") or self._discovered_host),
                "host": self._discovered_host or "",
                "model": str(self._discovered.get("project") or ""),
                "firmware": str(self._discovered.get("firmware") or ""),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> IeastOptionsFlow:
        return IeastOptionsFlow()


class IeastOptionsFlow(config_entries.OptionsFlow):
    """iEAST 选项流。"""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=3, max=60)),
                    vol.Optional(
                        CONF_USE_HTTPS,
                        default=options.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS),
                    ): bool,
                    vol.Optional(
                        CONF_TCP_EXTRAS,
                        default=options.get(CONF_TCP_EXTRAS, DEFAULT_TCP_EXTRAS),
                    ): bool,
                    vol.Optional(
                        CONF_TCP_FRAME_MODE,
                        default=options.get(CONF_TCP_FRAME_MODE, DEFAULT_TCP_FRAME_MODE),
                    ): vol.In(["auto", "token", "doc"]),
                    vol.Optional(
                        CONF_GROUP_PASSWORD,
                        default=options.get(CONF_GROUP_PASSWORD, DEFAULT_GROUP_PASSWORD),
                    ): str,
                    vol.Optional(
                        CONF_PUSH_LISTEN,
                        default=options.get(CONF_PUSH_LISTEN, DEFAULT_PUSH_LISTEN),
                    ): bool,
                }
            ),
        )
