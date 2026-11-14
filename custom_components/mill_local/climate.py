"""Climate entity: HEAT/OFF, target temperature, room temperature.

OFF/HEAT use 0x47 (confirmed: LCD shows "OF", status[10]=00). The 0x44
frame from older captures is a different mode (8 C) and is not used.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, CONF_HOST, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MillConfigEntry, MillCoordinator
from .config_flow import DOMAIN
from .protocol import MAX_TEMP, MIN_TEMP, MillError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MillConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MillClimate(entry.runtime_data, entry)])


class MillClimate(CoordinatorEntity[MillCoordinator], ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP

    def __init__(self, coordinator: MillCoordinator, entry: MillConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = entry.unique_id or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            connections={(CONNECTION_NETWORK_MAC, self._attr_unique_id)},  # unique_id = MAC
            name="Mill heater",
            manufacturer="Mill",
            model="Gen 2 (HF-LPT120, stock firmware)",
            configuration_url=f"http://{entry.data[CONF_HOST]}/",
        )

    @property
    def native_current_temperature(self) -> float | None:
        # Only present after a pushed 0xc9 frame; the 0x50 poll reply lacks it.
        return self.coordinator.data.get("room_c")

    @property
    def native_target_temperature(self) -> float | None:
        return self.coordinator.data.get("target_c")

    @property
    def hvac_mode(self) -> HVACMode | None:
        heat_on = self.coordinator.data.get("heat_on")
        if heat_on is None:
            return None
        return HVACMode.HEAT if heat_on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.coordinator.data.get("heat_on") is False:
            return HVACAction.OFF
        heating = self.coordinator.data.get("heating")
        if heating is None:
            return None
        return HVACAction.HEATING if heating else HVACAction.IDLE

    async def _apply(self, call) -> None:
        # The heater pushes its new state after the command; no local guess.
        try:
            await call
        except MillError as err:
            raise HomeAssistantError(f"Mill heater not reachable: {err}") from err

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self._apply(self.coordinator.client.set_temperature(round(temp)))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in self._attr_hvac_modes:
            raise HomeAssistantError(f"Unsupported mode {hvac_mode}")
        await self._apply(self.coordinator.client.set_power(hvac_mode == HVACMode.HEAT))

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
