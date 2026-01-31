"""Sensor platform for Tariff Saver."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import TariffSaverCoordinator, PriceSlot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: TariffSaverCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            TariffSaverPriceCurveSensor(coordinator, entry),
            TariffSaverPriceNowSensor(coordinator, entry),
            TariffSaverNextPriceSensor(coordinator, entry),
        ],
        update_before_add=True,
    )


class TariffSaverPriceCurveSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Exposes the full 15-min price curve as attributes (time series source)."""

    _attr_has_entity_name = True
    _attr_name = "Price curve"
    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = None  # attribute-only sensor

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_curve"

    @property
    def native_value(self) -> int | None:
        """Expose slot count as state (keeps sensor usable in UI)."""
        slots = self.coordinator.data or []
        return len(slots) if slots else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slots = self.coordinator.data or []
        slots_attr = [
            {
                "start": s.start.isoformat(),
                "price_chf_per_kwh": round(s.price_chf_per_kwh, 6),
            }
            for s in slots
        ]
        return {
            "tariff_name": self.coordinator.tariff_name,
            "slot_count": len(slots_attr),
            "slots": slots_attr,
            "updated_at": dt_util.utcnow().isoformat(),
        }


class TariffSaverPriceNowSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Shows the current electricity price (CHF/kWh)."""

    _attr_has_entity_name = True
    _attr_name = "Price now"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:currency-chf"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_now"

    @property
    def native_value(self) -> float | None:
        """Return the current price based on the latest slot <= now."""
        slots = self.coordinator.data or []
        if not slots:
            return None

        now = dt_util.utcnow()

        current: PriceSlot | None = None
        for s in slots:
            if s.start <= now:
                current = s
            else:
                break

        if current is None:
            # If all slots are in the future, show the first available slot as fallback
            return round(slots[0].price_chf_per_kwh, 6)

        return round(current.price_chf_per_kwh, 6)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Small helpful attributes (without duplicating full slot list)."""
        slots = self.coordinator.data or []
        now = dt_util.utcnow()

        next_slot: PriceSlot | None = None
        for s in slots:
            if s.start > now:
                next_slot = s
                break

        return {
            "tariff_name": self.coordinator.tariff_name,
            "next_start": next_slot.start.isoformat() if next_slot else None,
            "next_price_chf_per_kwh": round(next_slot.price_chf_per_kwh, 6) if next_slot else None,
            "updated_at": dt_util.utcnow().isoformat(),
        }


class TariffSaverNextPriceSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Shows the next electricity price (CHF/kWh)."""

    _attr_has_entity_name = True
    _attr_name = "Next price"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_next"

    @property
    def native_value(self) -> float | None:
        slots = self.coordinator.data or []
        if not slots:
            return None

        now = dt_util.utcnow()
        for s in slots:
            if s.start > now:
                return round(s.price_chf_per_kwh, 6)

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "tariff_name": self.coordinator.tariff_name,
            "updated_at": dt_util.utcnow().isoformat(),
        }
