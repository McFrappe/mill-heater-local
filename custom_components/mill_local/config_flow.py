"""Config flow: DHCP discovery (hostname HF-LPT120, MAC 34:EA:E7) or manual IP.

Unique ID = module MAC, so a new DHCP address updates the existing entry.
Discovery only offers devices that answer the Mill protocol on TCP 8899
(other products also use HF-LPT120 modules).
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .protocol import MillError, get_mac, probe

DOMAIN = "mill_local"


def _title(host: str) -> str:
    return f"Mill heater {host}"


class MillLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None

    def _abort_if_configured(self, host: str) -> None:
        """Abort if this heater is already set up, moving it to `host`.

        HA's helper updates the entry data (and reloads it) but not the title,
        which would keep showing the old address.
        """
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, self.unique_id
        )
        if entry is not None and entry.title != _title(host):
            self.hass.config_entries.async_update_entry(entry, title=_title(host))
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        host = discovery_info.ip
        await self.async_set_unique_id(format_mac(discovery_info.macaddress))
        self._abort_if_configured(host)
        try:
            await probe(host)
        except MillError:
            return self.async_abort(reason="not_mill_heater")
        self._host = host
        self.context["title_placeholders"] = {"host": host}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=_title(self._host), data={CONF_HOST: self._host}
            )
        return self.async_show_form(
            step_id="confirm", description_placeholders={"host": self._host}
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                mac = await get_mac(host)
                await probe(host)
            except MillError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(format_mac(mac))
                self._abort_if_configured(host)
                return self.async_create_entry(title=_title(host), data={CONF_HOST: host})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )
