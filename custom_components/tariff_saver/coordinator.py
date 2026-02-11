"""Coordinator for Tariff Saver (Public + myEKZ linking)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EkzTariffApi
from .const import (
    DOMAIN,
    CONF_PUBLISH_TIME,
    DEFAULT_PUBLISH_TIME,
    CONF_MODE,
    MODE_MYEKZ,
    CONF_EMS_INSTANCE_ID,
    CONF_REDIRECT_URI,
)
from .storage import TariffSaverStore

_LOGGER = logging.getLogger(__name__)

COMPONENT_KEYS = [
    "electricity",
    "grid",
    "integrated",
    "regional_fees",
    "metering",
    "refund_storage",
    "feed_in",
]


@dataclass(frozen=True)
class PriceSlot:
    start: datetime  # UTC aware
    electricity_chf_per_kwh: float
    components_chf_per_kwh: dict[str, float]


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


class TariffSaverCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, api: EkzTariffApi, config: dict[str, Any]) -> None:
        self.hass = hass
        self.api = api

        self.tariff_name: str = config.get("tariff_name", "myEKZ")
        self.baseline_tariff_name: str | None = config.get("baseline_tariff_name")
        self.publish_time: str = config.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)

        self.mode: str = config.get(CONF_MODE, "public")
        self.ems_instance_id: str | None = config.get(CONF_EMS_INSTANCE_ID)
        self.redirect_uri: str | None = config.get(CONF_REDIRECT_URI)

        self._last_fetch_date: date | None = None
        self.store: TariffSaverStore | None = None

        super().__init__(hass, _LOGGER, name="Tariff Saver", update_interval=None)

    async def _async_update_data(self) -> dict[str, Any]:
        if self.store is None:
            for entry_id, coord in self.hass.data.get(DOMAIN, {}).items():
                if coord is self:
                    self.store = TariffSaverStore(self.hass, entry_id)
                    await self.store.async_load()
                    break

        today = dt_util.now().date()
        if self._last_fetch_date == today:
            return self.data or {"active": [], "baseline": [], "stats": {}, "myekz": {}}

        if self.mode == MODE_MYEKZ:
            if not self.ems_instance_id or not self.redirect_uri:
                raise UpdateFailed("myEKZ mode requires ems_instance_id and redirect_uri.")
            try:
                status = await self.api.fetch_ems_link_status(self.ems_instance_id, self.redirect_uri)
            except Exception as err:
                raise UpdateFailed(f"myEKZ emsLinkStatus failed: {err}") from err
            self._last_fetch_date = today
            return {"active": [], "baseline": [], "stats": {}, "myekz": {
                "link_status": status.get("link_status"),
                "linking_process_redirect_uri": status.get("linking_process_redirect_uri"),
                "raw": status,
            }}

        try:
            raw_active = await self.api.fetch_prices(self.tariff_name)
            active = self._parse_prices(raw_active)
            if not active:
                raise UpdateFailed(f"No data returned for active tariff '{self.tariff_name}'")
        except Exception as err:
            raise UpdateFailed(f"Active tariff update failed: {err}") from err

        if self.store is not None:
            self.store.set_last_api_success(dt_util.utcnow())

        baseline: list[PriceSlot] = []
        if self.baseline_tariff_name:
            try:
                raw_base = await self.api.fetch_prices(self.baseline_tariff_name)
                baseline = self._parse_prices(raw_base)
            except Exception as err:
                _LOGGER.warning("Failed to fetch baseline tariff '%s': %s", self.baseline_tariff_name, err)
                baseline = []

        if self.store is not None:
            active_map = {s.start: s.components_chf_per_kwh for s in active}
            base_map = {s.start: s.components_chf_per_kwh for s in baseline}

            for start_utc, dyn_comps in active_map.items():
                base_comps = base_map.get(start_utc)
                api_integrated = dyn_comps.get("integrated")
                self.store.set_price_slot(start_utc, dyn_comps, base_comps, api_integrated=api_integrated)

            self.store.trim_price_slots(keep_days=7)
            if self.store.dirty:
                await self.store.async_save()

        stats = self._compute_daily_stats(active, baseline)
        self._last_fetch_date = today
        return {"active": active, "baseline": baseline, "stats": stats, "myekz": {}}

    @staticmethod
    def _parse_prices(raw_prices: list[dict[str, Any]]) -> list[PriceSlot]:
        slots: list[PriceSlot] = []
        for item in raw_prices:
            start_ts = item.get("start_timestamp")
            if not isinstance(start_ts, str):
                continue
            dt_start = dt_util.parse_datetime(start_ts)
            if dt_start is None:
                continue
            start_utc = dt_util.as_utc(dt_start)

            comps: dict[str, float] = {}
            for k in COMPONENT_KEYS:
                v = item.get(k)
                if isinstance(v, (int, float)):
                    comps[k] = float(v)

            elec = float(comps.get("electricity", 0.0) or 0.0)
            if elec <= 0:
                integ = comps.get("integrated")
                if isinstance(integ, (int, float)) and float(integ) > 0:
                    elec = float(integ)

            slots.append(PriceSlot(start=start_utc, electricity_chf_per_kwh=elec, components_chf_per_kwh=comps))

        return list({s.start: s for s in sorted(slots, key=lambda s: s.start)}.values())

    @staticmethod
    def _compute_daily_stats(active: list[PriceSlot], baseline: list[PriceSlot]) -> dict[str, Any]:
        active_valid = [s for s in active if s.electricity_chf_per_kwh > 0]
        base_map = {s.start: s.electricity_chf_per_kwh for s in baseline if s.electricity_chf_per_kwh > 0}

        avg_active = _avg([s.electricity_chf_per_kwh for s in active_valid])
        avg_baseline = _avg([base_map[s.start] for s in active_valid if s.start in base_map]) if base_map else None

        dev_vs_avg: dict[str, float] = {}
        dev_vs_baseline: dict[str, float] = {}

        for s in active_valid:
            if avg_active and avg_active > 0:
                dev_vs_avg[s.start.isoformat()] = (s.electricity_chf_per_kwh / avg_active - 1.0) * 100.0
            base = base_map.get(s.start)
            if base and base > 0:
                dev_vs_baseline[s.start.isoformat()] = (s.electricity_chf_per_kwh / base - 1.0) * 100.0

        return {
            "calculated_at": dt_util.utcnow().isoformat(),
            "avg_active_chf_per_kwh": avg_active,
            "avg_baseline_chf_per_kwh": avg_baseline,
            "dev_vs_avg_percent": dev_vs_avg,
            "dev_vs_baseline_percent": dev_vs_baseline,
        }
