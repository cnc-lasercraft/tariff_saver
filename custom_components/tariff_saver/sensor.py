"""Sensor platform for Tariff Saver."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, date, datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import TariffSaverCoordinator, PriceSlot


CONF_CONSUMPTION_ENERGY_ENTITY = "consumption_energy_entity"


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _to_float(state: Any) -> float | None:
    try:
        if state in (None, "unknown", "unavailable", ""):
            return None
        return float(state)
    except Exception:
        return None


def _floor_15min_utc(ts: datetime) -> datetime:
    ts = dt_util.as_utc(ts)
    minute = (ts.minute // 15) * 15
    return ts.replace(minute=minute, second=0, microsecond=0)


# -------------------------------------------------------------------
# Cost tracking
# -------------------------------------------------------------------
@dataclass
class _CostTracker:
    last_energy_kwh: float | None = None
    active_cost_chf_today: float = 0.0
    baseline_cost_chf_today: float = 0.0
    has_baseline: bool = False
    day: date | None = None


def _tracker_key(entry: ConfigEntry) -> str:
    return f"{entry.entry_id}_cost_tracker"


def _get_tracker(hass: HomeAssistant, entry: ConfigEntry) -> _CostTracker:
    domain = hass.data.setdefault(DOMAIN, {})
    key = _tracker_key(entry)
    if key not in domain:
        domain[key] = _CostTracker()
    return domain[key]


def _reset_if_new_day(tracker: _CostTracker) -> None:
    today = dt_util.now().date()
    if tracker.day != today:
        tracker.day = today
        tracker.active_cost_chf_today = 0.0
        tracker.baseline_cost_chf_today = 0.0
        tracker.has_baseline = False


# -------------------------------------------------------------------
# Setup
# -------------------------------------------------------------------
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TariffSaverCoordinator = hass.data[DOMAIN][entry.entry_id]
    tracker = _get_tracker(hass, entry)

    energy_entity = entry.options.get(CONF_CONSUMPTION_ENERGY_ENTITY) or entry.data.get(CONF_CONSUMPTION_ENERGY_ENTITY)

    if isinstance(energy_entity, str) and energy_entity:

        @callback
        def _on_energy_change(event: Event) -> None:
            new_state = event.data.get("new_state")
            old_state = event.data.get("old_state")
            if new_state is None:
                return

            new_val = _to_float(new_state.state)
            old_val = _to_float(old_state.state) if old_state else None
            if new_val is None:
                return

            _reset_if_new_day(tracker)

            if tracker.last_energy_kwh is None:
                tracker.last_energy_kwh = old_val if old_val is not None else new_val
                return

            delta = new_val - tracker.last_energy_kwh
            if delta <= 0:
                tracker.last_energy_kwh = new_val
                return

            now_utc = dt_util.utcnow()
            slot_start = _floor_15min_utc(now_utc)

            price = coordinator.store.get_price_slot(slot_start)
            if not price:
                tracker.last_energy_kwh = new_val
                return

            dyn = price["dyn"]
            base = price["base"]

            tracker.active_cost_chf_today += delta * dyn
            tracker.baseline_cost_chf_today += delta * base
            tracker.has_baseline = True

            tracker.last_energy_kwh = new_val

            hass.async_create_task(
                hass.helpers.entity_component.async_update_entity("sensor.actual_cost_today")
            )
            hass.async_create_task(
                hass.helpers.entity_component.async_update_entity("sensor.baseline_cost_today")
            )
            hass.async_create_task(
                hass.helpers.entity_component.async_update_entity("sensor.actual_savings_today")
            )

        async_track_state_change_event(hass, [energy_entity], _on_energy_change)

    async_add_entities(
        [
            TariffSaverActualCostTodaySensor(coordinator, entry),
            TariffSaverBaselineCostTodaySensor(coordinator, entry),
            TariffSaverActualSavingsTodaySensor(coordinator, entry),
        ],
        update_before_add=True,
    )


# -------------------------------------------------------------------
# Cost Sensors
# -------------------------------------------------------------------
class _BaseTodayCostSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity, RestoreEntity):
    _attr_native_unit_of_measurement = "CHF"
    _attr_icon = "mdi:currency-chf"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return

        tracker = _get_tracker(self.hass, self.entry)
        _reset_if_new_day(tracker)

        try:
            v = float(last.state)
        except Exception:
            return

        self._restore_value(tracker, v)

    def _restore_value(self, tracker: _CostTracker, v: float) -> None:
        return


class TariffSaverActualCostTodaySensor(_BaseTodayCostSensor):
    _attr_has_entity_name = True
    _attr_name = "Actual cost today"
    _attr_state_class = "total"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_actual_cost_today"

    @property
    def native_value(self) -> float:
        tracker = _get_tracker(self.hass, self.entry)
        _reset_if_new_day(tracker)
        return round(tracker.active_cost_chf_today, 2)

    def _restore_value(self, tracker: _CostTracker, v: float) -> None:
        tracker.active_cost_chf_today = v


class TariffSaverBaselineCostTodaySensor(_BaseTodayCostSensor):
    _attr_has_entity_name = True
    _attr_name = "Baseline cost today"
    _attr_state_class = "total"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_baseline_cost_today"

    @property
    def native_value(self) -> float | None:
        tracker = _get_tracker(self.hass, self.entry)
        _reset_if_new_day(tracker)
        if not tracker.has_baseline:
            return None
        return round(tracker.baseline_cost_chf_today, 2)

    def _restore_value(self, tracker: _CostTracker, v: float) -> None:
        tracker.baseline_cost_chf_today = v
        tracker.has_baseline = True


class TariffSaverActualSavingsTodaySensor(_BaseTodayCostSensor):
    _attr_has_entity_name = True
    _attr_name = "Actual savings today"
    _attr_state_class = "measurement"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_actual_savings_today"

    @property
    def native_value(self) -> float | None:
        tracker = _get_tracker(self.hass, self.entry)
        _reset_if_new_day(tracker)
        if not tracker.has_baseline:
            return None
        return round(tracker.baseline_cost_chf_today - tracker.active_cost_chf_today, 2)
