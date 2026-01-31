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


def _active_slots(coordinator: TariffSaverCoordinator) -> list[PriceSlot]:
    data = coordinator.data or {}
    return data.get("active", []) if isinstance(data, dict) else []


def _baseline_slots(coordinator: TariffSaverCoordinator) -> list[PriceSlot]:
    data = coordinator.data or {}
    return data.get("baseline", []) if isinstance(data, dict) else []


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
            TariffSaverSavingsNext24hSensor(coordinator, entry),
        ],
        update_before_add=True,
    )


class TariffSaverPriceCurveSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Exposes the full 15-min active price curve as attributes (time series source)."""

    _attr_has_entity_name = True
    _attr_name = "Price curve"
    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = None  # attribute-only sensor

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_curve"

    @property
    def native_value(self) -> int | None:
        slots = _active_slots(self.coordinator)
        return len(slots) if slots else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = _active_slots(self.coordinator)
        slots_attr = [
            {
                "start": s.start.isoformat(),
                "price_chf_per_kwh": round(s.price_chf_per_kwh, 6),
            }
            for s in active
        ]
        return {
            "tariff_name": self.coordinator.tariff_name,
            "baseline_tariff_name": self.coordinator.baseline_tariff_name,
            "slot_count": len(slots_attr),
            "slots": slots_attr,
            "updated_at": dt_util.utcnow().isoformat(),
        }


class TariffSaverPriceNowSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Shows the current electricity price (CHF/kWh) for the active tariff."""

    _attr_has_entity_name = True
    _attr_name = "Price now"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:currency-chf"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_now"

    @property
    def native_value(self) -> float | None:
        slots = _active_slots(self.coordinator)
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
            return round(slots[0].price_chf_per_kwh, 6)

        return round(current.price_chf_per_kwh, 6)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slots = _active_slots(self.coordinator)
        now = dt_util.utcnow()

        next_slot: PriceSlot | None = None
        for s in slots:
            if s.start > now:
                next_slot = s
                break

        return {
            "tariff_name": self.coordinator.tariff_name,
            "baseline_tariff_name": self.coordinator.baseline_tariff_name,
            "next_start": next_slot.start.isoformat() if next_slot else None,
            "next_price_chf_per_kwh": round(next_slot.price_chf_per_kwh, 6)
            if next_slot
            else None,
            "updated_at": dt_util.utcnow().isoformat(),
        }


class TariffSaverNextPriceSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Shows the next electricity price (CHF/kWh) for the active tariff."""

    _attr_has_entity_name = True
    _attr_name = "Next price"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_next"

    @property
    def native_value(self) -> float | None:
        slots = _active_slots(self.coordinator)
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
            "baseline_tariff_name": self.coordinator.baseline_tariff_name,
            "updated_at": dt_util.utcnow().isoformat(),
        }


class TariffSaverSavingsNext24hSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Estimated savings for the next 24h vs baseline (CHF), assuming 1 kW constant load."""

    _attr_has_entity_name = True
    _attr_name = "Savings next 24h"
    _attr_native_unit_of_measurement = "CHF"
    _attr_icon = "mdi:piggy-bank-outline"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_savings_next24h"

    @property
    def native_value(self) -> float | None:
        active = _active_slots(self.coordinator)
        baseline = _baseline_slots(self.coordinator)

        if not active or not baseline:
            return None

        # Index baseline by slot start for alignment
        baseline_map = {s.start: s.price_chf_per_kwh for s in baseline}

        # Assume constant 1 kW load → 0.25 kWh per 15-min slot
        kwh_per_slot = 0.25

        savings = 0.0
        matched = 0

        for s in active:
            base_price = baseline_map.get(s.start)
            if base_price is None:
                continue

            matched += 1
            savings += (base_price - s.price_chf_per_kwh) * kwh_per_slot

        if matched == 0:
            return None

        # Positive = cheaper than baseline
        return round(savings, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "tariff_name": self.coordinator.tariff_name,
            "baseline_tariff_name": self.coordinator.baseline_tariff_name,
            "assumption": "1 kW constant load (0.25 kWh per 15 min)",
        }
