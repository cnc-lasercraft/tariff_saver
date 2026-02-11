"""Sensor platform for Tariff Saver (prices + grading + costs).

Includes:
- Price curve (with components in attributes)
- Price now (electricity-only) + next price
- Price all-in now (sum of components) + per-component price now sensors
- Savings next 24h (vs baseline, 1kW assumption)
- Cheapest windows (30m/1h/2h/3h) + stars vs future avg
- Tariff grade + stars now/outlook (vs daily avg)
- Cost totals from booked slots: today/week/month/year (electricity + all-in + per-component)
  These update immediately when new slots are booked (dispatcher signal).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import TariffSaverCoordinator, PriceSlot
from .storage import IMPORT_ALLIN_COMPONENTS, TariffSaverStore

CONF_CONSUMPTION_ENERGY_ENTITY = "consumption_energy_entity"
SIGNAL_STORE_UPDATED = "tariff_saver_store_updated"

COMPONENT_KEYS = [
    "electricity",
    "grid",
    "regional_fees",
    "metering",
    "refund_storage",
    "integrated",
    "feed_in",
]


def _active_slots(coordinator: TariffSaverCoordinator) -> list[PriceSlot]:
    data = coordinator.data or {}
    return data.get("active", []) if isinstance(data, dict) else []


def _baseline_slots(coordinator: TariffSaverCoordinator) -> list[PriceSlot]:
    data = coordinator.data or {}
    return data.get("baseline", []) if isinstance(data, dict) else []


def _current_slot(slots: list[PriceSlot]) -> PriceSlot | None:
    """Return current slot; fallback to first slot if between slots."""
    if not slots:
        return None
    slots = sorted(slots, key=lambda s: s.start)
    now = dt_util.utcnow()
    current: PriceSlot | None = None
    for s in slots:
        if s.start <= now:
            current = s
        else:
            break
    return current or slots[0]


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _avg_future_from_now(slots: list[PriceSlot]) -> float | None:
    now = dt_util.utcnow()
    vals = [float(s.electricity_chf_per_kwh) for s in slots if s.electricity_chf_per_kwh > 0 and s.start >= now]
    return _avg(vals)


def _avg_day_from_stats(coordinator: TariffSaverCoordinator) -> float | None:
    data = coordinator.data or {}
    stats = data.get("stats") or {}
    v = stats.get("avg_active_chf_per_kwh")
    if isinstance(v, (int, float)) and float(v) > 0:
        return float(v)
    return None


def _grade_from_dev(dev_percent: float) -> int:
    """Deviation vs daily avg (percent) -> grade 1..5 (1 = very cheap)."""
    if dev_percent <= -20:
        return 1
    if dev_percent <= -10:
        return 2
    if dev_percent <= 10:
        return 3
    if dev_percent <= 25:
        return 4
    return 5


def _label_from_grade(grade: int) -> str:
    return {
        1: "sehr günstig",
        2: "günstig",
        3: "durchschnitt",
        4: "teuer",
        5: "sehr teuer",
    }.get(grade, "unbekannt")


def _stars_from_grade(grade: int | None) -> str:
    if grade is None or grade < 1 or grade > 5:
        return "—"
    return "⭐" * (6 - grade)


def _stars_for_horizon(coordinator: TariffSaverCoordinator, minutes: int) -> tuple[str | None, int | None, float | None]:
    avg_day = _avg_day_from_stats(coordinator)
    if not avg_day:
        return None, None, None

    now = dt_util.utcnow()
    end = now + timedelta(minutes=minutes)
    prices = [
        float(s.electricity_chf_per_kwh)
        for s in _active_slots(coordinator)
        if s.electricity_chf_per_kwh > 0 and now <= s.start < end
    ]
    if not prices:
        return None, None, None

    avg_window = sum(prices) / len(prices)
    dev = (avg_window / float(avg_day) - 1.0) * 100.0
    grade = _grade_from_dev(float(dev))
    return _stars_from_grade(grade), grade, float(dev)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: TariffSaverCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Track energy samples -> finalize 15-min slots -> persist -> notify cost sensors
    energy_entity = entry.options.get(CONF_CONSUMPTION_ENERGY_ENTITY) or entry.data.get(CONF_CONSUMPTION_ENERGY_ENTITY)
    if isinstance(energy_entity, str) and energy_entity:

        @callback
        def _on_energy_change(event: Event) -> None:
            new_state = event.data.get("new_state")
            if new_state is None:
                return

            try:
                kwh_total = float(new_state.state)
            except Exception:
                return

            store = getattr(coordinator, "store", None)
            if store is None:
                return

            now_utc = dt_util.utcnow()
            if not store.add_sample(now_utc, kwh_total):
                return

            newly = store.finalize_due_slots(now_utc)
            if store.dirty:
                hass.async_create_task(store.async_save())

            if newly > 0:
                async_dispatcher_send(hass, f"{SIGNAL_STORE_UPDATED}_{entry.entry_id}")

        unsub = async_track_state_change_event(hass, [energy_entity], _on_energy_change)
        hass.data[DOMAIN][f"{entry.entry_id}_unsub_energy_cost"] = unsub

    entities: list[SensorEntity] = []

    # Prices
    entities += [
        TariffSaverPriceCurveSensor(coordinator, entry),
        TariffSaverPriceNowSensor(coordinator, entry),
        TariffSaverNextPriceSensor(coordinator, entry),
        TariffSaverPriceAllInNowSensor(coordinator, entry),
    ]
    for comp in COMPONENT_KEYS:
        if comp == "electricity":
            continue
        entities.append(TariffSaverPriceComponentNowSensor(coordinator, entry, comp))

    # Grading + windows + savings (these were missing)
    entities += [
        TariffSaverSavingsNext24hSensor(coordinator, entry),
        TariffSaverCheapestWindowsSensor(coordinator, entry),
        TariffSaverTariffGradeSensor(coordinator, entry),
        TariffSaverTariffStarsNowSensor(coordinator, entry),
        TariffSaverTariffStarsOutlookSensor(coordinator, entry),
    ]

    # Core cost sensors (electricity-only) - keep stable entity ids you mentioned
    entities += [
        PeriodCostSensor(entry, coordinator, "today", "dyn", "electricity", "actual_cost_today", "Actual cost today", icon="mdi:cash"),
        PeriodCostSensor(entry, coordinator, "today", "base", "electricity", "baseline_cost_today", "Baseline cost today", icon="mdi:cash-multiple"),
        PeriodCostSensor(entry, coordinator, "today", "sav", "electricity", "actual_savings_today", "Actual savings today", icon="mdi:piggy-bank", state_class="measurement"),
        PeriodCostSensor(entry, coordinator, "week", "dyn", "electricity", "actual_cost_week", "Actual cost week", icon="mdi:cash"),
        PeriodCostSensor(entry, coordinator, "week", "base", "electricity", "baseline_cost_week", "Baseline cost week", icon="mdi:cash-multiple"),
        PeriodCostSensor(entry, coordinator, "week", "sav", "electricity", "actual_savings_week", "Actual savings week", icon="mdi:piggy-bank", state_class="measurement"),
        PeriodCostSensor(entry, coordinator, "month", "dyn", "electricity", "actual_cost_month", "Actual cost month", icon="mdi:cash"),
        PeriodCostSensor(entry, coordinator, "month", "base", "electricity", "baseline_cost_month", "Baseline cost month", icon="mdi:cash-multiple"),
        PeriodCostSensor(entry, coordinator, "month", "sav", "electricity", "actual_savings_month", "Actual savings month", icon="mdi:piggy-bank", state_class="measurement"),
        PeriodCostSensor(entry, coordinator, "year", "dyn", "electricity", "actual_cost_year", "Actual cost year", icon="mdi:cash"),
        PeriodCostSensor(entry, coordinator, "year", "base", "electricity", "baseline_cost_year", "Baseline cost year", icon="mdi:cash-multiple"),
        PeriodCostSensor(entry, coordinator, "year", "sav", "electricity", "actual_savings_year", "Actual savings year", icon="mdi:piggy-bank", state_class="measurement"),
    ]

    # All-in totals
    for period in ("today", "week", "month", "year"):
        entities += [
            PeriodCostSensor(entry, coordinator, period, "dyn", "__allin__", f"actual_cost_allin_{period}", f"Actual cost all-in {period}", icon="mdi:cash"),
            PeriodCostSensor(entry, coordinator, period, "base", "__allin__", f"baseline_cost_allin_{period}", f"Baseline cost all-in {period}", icon="mdi:cash-multiple"),
            PeriodCostSensor(entry, coordinator, period, "sav", "__allin__", f"actual_savings_allin_{period}", f"Actual savings all-in {period}", icon="mdi:piggy-bank", state_class="measurement"),
        ]

    # Component-wise costs (dyn/base/sav) - optional but requested
    for period in ("today", "week", "month", "year"):
        for comp in COMPONENT_KEYS:
            entities.append(PeriodCostSensor(entry, coordinator, period, "dyn", comp, f"dyn_{comp}_{period}", f"{comp} cost {period}", icon="mdi:cash"))
            entities.append(PeriodCostSensor(entry, coordinator, period, "base", comp, f"base_{comp}_{period}", f"{comp} baseline {period}", icon="mdi:cash-multiple"))
            entities.append(PeriodCostSensor(entry, coordinator, period, "sav", comp, f"sav_{comp}_{period}", f"{comp} savings {period}", icon="mdi:piggy-bank", state_class="measurement"))

    # Diagnostics
    entities += [TariffSaverLastApiSuccessSensor(coordinator, entry)]

    async_add_entities(entities, update_before_add=True)


# -------------------------
# Price sensors
# -------------------------
class TariffSaverPriceCurveSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Price curve"
    _attr_icon = "mdi:chart-line"

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
        baseline = _baseline_slots(self.coordinator)
        baseline_map = {s.start: s.components_chf_per_kwh for s in baseline} if baseline else {}

        return {
            "tariff_name": getattr(self.coordinator, "tariff_name", None),
            "baseline_tariff_name": getattr(self.coordinator, "baseline_tariff_name", None),
            "slot_count": len(active),
            "slots": [
                {
                    "start": s.start.isoformat(),
                    "price_chf_per_kwh": float(s.electricity_chf_per_kwh),
                    "baseline_chf_per_kwh": (baseline_map.get(s.start, {}) or {}).get("electricity"),
                    "components": s.components_chf_per_kwh,
                    "baseline_components": baseline_map.get(s.start),
                }
                for s in active
            ],
        }


class TariffSaverPriceNowSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Price now"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:currency-chf"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_now"

    @property
    def native_value(self) -> float | None:
        slot = _current_slot(_active_slots(self.coordinator))
        return float(slot.electricity_chf_per_kwh) if slot else None


class TariffSaverNextPriceSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Next price"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_next"

    @property
    def native_value(self) -> float | None:
        slots = sorted(_active_slots(self.coordinator), key=lambda s: s.start)
        if not slots:
            return None
        now = dt_util.utcnow()
        for s in slots:
            if s.start > now:
                return float(s.electricity_chf_per_kwh)
        return None


class TariffSaverPriceAllInNowSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Price all-in now"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:cash"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_price_allin_now"

    @property
    def native_value(self) -> float | None:
        slot = _current_slot(_active_slots(self.coordinator))
        if not slot:
            return None
        comps = slot.components_chf_per_kwh or {}
        total = sum(float(comps.get(c, 0.0) or 0.0) for c in IMPORT_ALLIN_COMPONENTS)
        return round(total, 6) if total else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slot = _current_slot(_active_slots(self.coordinator))
        if not slot:
            return {}
        comps = slot.components_chf_per_kwh or {}
        api_integrated = comps.get("integrated")
        summed = sum(float(comps.get(c, 0.0) or 0.0) for c in IMPORT_ALLIN_COMPONENTS)
        return {
            "slot_start_utc": slot.start.isoformat(),
            "sum_components": round(summed, 6),
            "api_integrated": float(api_integrated) if isinstance(api_integrated, (int, float)) else None,
            "components_used": list(IMPORT_ALLIN_COMPONENTS),
        }


class TariffSaverPriceComponentNowSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:currency-chf"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry, component: str) -> None:
        super().__init__(coordinator)
        self._component = component
        self._attr_name = f"Price now {component}"
        self._attr_unique_id = f"{entry.entry_id}_price_now_{component}"

    @property
    def native_value(self) -> float | None:
        slot = _current_slot(_active_slots(self.coordinator))
        if not slot:
            return None
        v = (slot.components_chf_per_kwh or {}).get(self._component)
        return float(v) if isinstance(v, (int, float)) else None


# -------------------------
# Savings + cheapest windows
# -------------------------
class TariffSaverSavingsNext24hSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Estimated savings next 24h vs baseline (CHF), assuming constant 1 kW load."""

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

        base_map = {s.start: float((s.components_chf_per_kwh or {}).get("electricity", 0.0) or 0.0) for s in baseline}
        kwh_per_slot = 0.25  # 1kW for 15 minutes

        savings = 0.0
        matched = 0
        for s in active:
            b = base_map.get(s.start)
            if not b or b <= 0:
                continue
            savings += (b - float(s.electricity_chf_per_kwh)) * kwh_per_slot
            matched += 1

        return round(savings, 2) if matched else None


class TariffSaverCheapestWindowsSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Cheapest windows for 30m / 1h / 2h / 3h, enriched with stars vs future average."""

    _attr_has_entity_name = True
    _attr_name = "Cheapest windows"
    _attr_native_unit_of_measurement = "CHF/kWh"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_cheapest_windows"

    @staticmethod
    def _best_window(slots: list[PriceSlot], window_slots: int) -> dict[str, Any] | None:
        slots = [s for s in slots if s.electricity_chf_per_kwh > 0]
        if len(slots) < window_slots:
            return None

        best_sum = float("inf")
        best_start: datetime | None = None
        best_end: datetime | None = None

        for i in range(len(slots) - window_slots + 1):
            window = slots[i : i + window_slots]
            window_sum = sum(float(x.electricity_chf_per_kwh) for x in window)
            if window_sum < best_sum:
                best_sum = window_sum
                best_start = window[0].start
                best_end = window[-1].start + timedelta(minutes=15)

        if best_start is None or best_end is None:
            return None

        avg_chf = best_sum / window_slots
        return {
            "start": best_start.isoformat(),
            "end": best_end.isoformat(),
            "avg_chf_per_kwh": round(avg_chf, 6),
            "avg_chf_per_kwh_raw": float(avg_chf),
        }

    @staticmethod
    def _decorate_with_stars(window: dict[str, Any] | None, ref_avg: float | None) -> dict[str, Any] | None:
        if not window or not ref_avg or ref_avg <= 0:
            return window
        p = window.get("avg_chf_per_kwh_raw")
        if not isinstance(p, (int, float)) or float(p) <= 0:
            return window

        dev = (float(p) / float(ref_avg) - 1.0) * 100.0
        grade = _grade_from_dev(float(dev))

        out = dict(window)
        out["dev_vs_ref_percent"] = round(float(dev), 2)
        out["grade"] = grade
        out["stars"] = _stars_from_grade(grade)
        return out

    @property
    def native_value(self) -> float | None:
        slots = sorted(_active_slots(self.coordinator), key=lambda s: s.start)
        best_1h = self._best_window(slots, 4)
        return float(best_1h["avg_chf_per_kwh"]) if best_1h else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slots = sorted(_active_slots(self.coordinator), key=lambda s: s.start)
        ref_avg = _avg_future_from_now(slots)

        best_30m = self._decorate_with_stars(self._best_window(slots, 2), ref_avg)
        best_1h = self._decorate_with_stars(self._best_window(slots, 4), ref_avg)
        best_2h = self._decorate_with_stars(self._best_window(slots, 8), ref_avg)
        best_3h = self._decorate_with_stars(self._best_window(slots, 12), ref_avg)

        return {
            "ref_scope": "future",
            "ref_avg_chf_per_kwh": round(ref_avg, 6) if ref_avg else None,
            "best_30m": best_30m,
            "best_1h": best_1h,
            "best_2h": best_2h,
            "best_3h": best_3h,
        }


# -------------------------
# Grade + stars
# -------------------------
class TariffSaverTariffGradeSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    """Numeric tariff grade now (1..5) based on deviation vs daily average."""

    _attr_has_entity_name = True
    _attr_name = "Tariff grade"
    _attr_icon = "mdi:school-outline"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_tariff_grade"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data or {}
        stats = data.get("stats") or {}
        dev_map = stats.get("dev_vs_avg_percent") or {}

        slot = _current_slot(_active_slots(self.coordinator))
        if not slot:
            return None

        dev = dev_map.get(slot.start.isoformat())
        if dev is None:
            return None

        return _grade_from_dev(float(dev))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        stats = data.get("stats") or {}
        dev_map = stats.get("dev_vs_avg_percent") or {}

        slot = _current_slot(_active_slots(self.coordinator))
        if not slot:
            return {}

        dev = dev_map.get(slot.start.isoformat())
        if dev is None:
            return {}

        grade = _grade_from_dev(float(dev))
        return {
            "slot_start_utc": slot.start.isoformat(),
            "dev_vs_avg_percent_now": round(float(dev), 2),
            "label_now": _label_from_grade(grade),
        }


class TariffSaverTariffStarsNowSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Tariff stars now"
    _attr_icon = "mdi:star-outline"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_tariff_stars_now"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        stats = data.get("stats") or {}
        dev_map = stats.get("dev_vs_avg_percent") or {}

        slot = _current_slot(_active_slots(self.coordinator))
        if not slot:
            return None

        dev = dev_map.get(slot.start.isoformat())
        if dev is None:
            return None

        grade = _grade_from_dev(float(dev))
        return _stars_from_grade(grade)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        stats = data.get("stats") or {}
        dev_map = stats.get("dev_vs_avg_percent") or {}
        slot = _current_slot(_active_slots(self.coordinator))
        if not slot:
            return {}
        dev = dev_map.get(slot.start.isoformat())
        if dev is None:
            return {}
        grade = _grade_from_dev(float(dev))
        return {
            "slot_start_utc": slot.start.isoformat(),
            "dev_vs_avg_percent": round(float(dev), 2),
            "grade": grade,
            "label": _label_from_grade(grade),
        }


class TariffSaverTariffStarsOutlookSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Tariff stars outlook"
    _attr_icon = "mdi:star-outline"

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_tariff_stars_outlook"

    @property
    def native_value(self) -> str | None:
        stars, _grade, _dev = _stars_for_horizon(self.coordinator, minutes=60)
        return stars

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for minutes, key in [
            (30, "next_30m"),
            (60, "next_1h"),
            (120, "next_2h"),
            (180, "next_3h"),
            (360, "next_6h"),
        ]:
            stars, grade, dev = _stars_for_horizon(self.coordinator, minutes=minutes)
            out[key] = stars
            out[f"{key}_grade"] = grade
            out[f"{key}_dev_vs_avg_percent"] = round(dev, 2) if isinstance(dev, (int, float)) else None
        return out


# -------------------------
# Period cost sensors (booked slots)
# -------------------------
class PeriodCostSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity, RestoreEntity):
    _attr_native_unit_of_measurement = "CHF"

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: TariffSaverCoordinator,
        period: str,
        flavor: str,   # dyn|base|sav
        key: str,      # component name or "__allin__"
        unique_suffix: str,
        name: str,
        icon: str = "mdi:cash",
        state_class: str = "total",
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.period = period
        self.flavor = flavor
        self.key = key
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_icon = icon
        self._attr_state_class = state_class
        self._attr_unique_id = unique_suffix
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_store_update() -> None:
            self.async_write_ha_state()

        self._unsub = async_dispatcher_connect(
            self.hass, f"{SIGNAL_STORE_UPDATED}_{self.entry.entry_id}", _on_store_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()

    def _get_breakdown(self) -> dict[str, dict[str, float]] | None:
        store: TariffSaverStore | None = getattr(self.coordinator, "store", None)
        if store is None:
            return None
        fn = getattr(store, f"compute_{self.period}_breakdown", None)
        if fn is None:
            return None
        try:
            out = fn()
            return out if isinstance(out, dict) else None
        except Exception:
            return None

    @property
    def native_value(self) -> float | None:
        bd = self._get_breakdown()
        if not bd:
            return None
        bucket = bd.get(self.flavor) or {}
        if not isinstance(bucket, dict):
            return None

        if self.key == "__allin__":
            total = TariffSaverStore.sum_components(bucket, IMPORT_ALLIN_COMPONENTS)
            return round(total, 2)

        v = bucket.get(self.key)
        if isinstance(v, (int, float)):
            return round(float(v), 2)
        return 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bd = self._get_breakdown()
        if not bd:
            return {}
        bucket = bd.get(self.flavor) or {}
        if not isinstance(bucket, dict):
            return {}
        return {"components": {k: round(float(v), 4) for k, v in bucket.items() if isinstance(v, (int, float))}}


# -------------------------
# Diagnostics
# -------------------------
class TariffSaverLastApiSuccessSensor(CoordinatorEntity[TariffSaverCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Last API success"
    _attr_icon = "mdi:cloud-check-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TariffSaverCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_last_api_success"

    @property
    def native_value(self) -> datetime | None:
        store = getattr(self.coordinator, "store", None)
        if store is None:
            return None
        ts = getattr(store, "last_api_success_utc", None)
        if isinstance(ts, datetime):
            return dt_util.as_utc(ts)
        return None
