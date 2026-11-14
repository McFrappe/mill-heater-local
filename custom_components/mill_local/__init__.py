"""Mill Gen 2 heater, local push via HF-LPT120 stock firmware (TCP 8899)."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .protocol import MillClient

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.CLIMATE]

type MillConfigEntry = ConfigEntry[MillCoordinator]


class MillCoordinator(DataUpdateCoordinator[dict]):
    """Holds pushed heater state. No polling: the client calls _on_update."""

    def __init__(self, hass: HomeAssistant, entry: MillConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"mill_local {entry.data[CONF_HOST]}",
            update_interval=None,
        )
        self.client = MillClient(entry.data[CONF_HOST], self._on_update)
        self.data = {}

    @callback
    def _on_update(self, state: dict | None) -> None:
        if state is None:
            self.async_set_update_error(UpdateFailed("connection to heater lost"))
        else:
            self.async_set_updated_data(state)


async def async_setup_entry(hass: HomeAssistant, entry: MillConfigEntry) -> bool:
    coordinator = MillCoordinator(hass, entry)
    entry.async_create_background_task(
        hass, coordinator.client.run(), f"mill_local {entry.data[CONF_HOST]}"
    )
    # Wait for the first status so entities start with real values.
    for _ in range(100):
        if coordinator.data.get("target_c") is not None:
            break
        await asyncio.sleep(0.1)
    else:
        raise ConfigEntryNotReady(f"no status from heater at {entry.data[CONF_HOST]}")
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MillConfigEntry) -> bool:
    # The background task is cancelled by Home Assistant on unload.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
