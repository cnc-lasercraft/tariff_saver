"""Lightweight persistent storage for Tariff Saver.

Persists:
- price slots (UTC 15-min): dynamic + optional baseline CHF/kWh
- energy samples (UTC timestamps of cumulative kWh)
- booked 15-min slots (UTC start) with kWh + dyn/base CHF + savings CHF
- last API success timestamp

IMPORTANT:
- No entity renames.
- Backwards compatible: if older storage is missing fields, defaults are used.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import Any, Dict, Optional, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util


@dataclass
class BookedSlot:
    """A finalized 15-min slot with consumption and costs."""
    start: datetime  # UTC, timezone-aware (slot start)
    kwh: float
    dyn_chf: float
    base_chf: float
    savings_chf: float
    status: str  # "ok" | "unpriced" | "invalid" | "missing_samples"


class TariffSaverStore:
    """Persists recent energy samples, price slots and finalized 15-min slots."""

    STORAGE_VERSION = 3
    STORAGE_KEY = "tariff_saver"

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store = Store(hass, self.STORAGE_VERSION, f"{self.STORAGE_KEY}.{entry_id}")

        # in-memory
        self.price_slots: dict[str, dict[str, float | None]] = {}  # iso -> {"dyn": float, "base": float|None}
        self.samples: list[dict[str, float]] = []  # [{"ts": epoch, "kwh": float}]
        self.booked: list[dict[str, Any]] = []  # list of dicts for persistence
        self.last_api_success_utc: datetime | None = None

        self.dirty: bool = False

    # -------------------------
    # Persistence
    # -------------------------
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}

        self.price_slots = dict(data.get("price_slots") or {})
        self.samples = list(data.get("samples") or [])
        self.booked = list(data.get("booked") or [])

        ts = data.get("last_api_success_utc")
        if isinstance(ts, str):
            dt = dt_util.parse_datetime(ts)
            self.last_api_success_utc = dt_util.as_utc(dt) if dt else None
        else:
            self.last_api_success_utc = None

        self.dirty = False

    async def async_save(self) -> None:
        await self._store.async_save(self._as_dict())
        self.dirty = False

    def _as_dict(self) -> dict[str, Any]:
        return {
            "price_slots": self.price_slots,
            "samples": self.samples,
            "booked": self.booked,
            "last_api_success_utc": self.last_api_success_utc.isoformat() if self.last_api_success_utc else None,
        }

    # -------------------------
    # API success timestamp
    # -------------------------
    def set_last_api_success(self, when_utc: datetime) -> None:
        self.last_api_success_utc = dt_util.as_utc(when_utc)
        self.dirty = True

    # -------------------------
    # Price slots (UTC 15-min)
    # -------------------------
    def set_price_slot(self, start_utc: datetime, dyn_chf_per_kwh: float, base_chf_per_kwh: float | None) -> None:
        start_utc = dt_util.as_utc(start_utc)
        key = start_utc.isoformat()

        # keep newest values
        self.price_slots[key] = {"dyn": float(dyn_chf_per_kwh), "base": float(base_chf_per_kwh) if base_chf_per_kwh is not None else None}
        self.dirty = True

    def get_price_slot(self, start_utc: datetime) -> tuple[float | None, float | None]:
        key = dt_util.as_utc(start_utc).isoformat()
        slot = self.price_slots.get(key)
        if not slot:
            return None, None
        dyn = slot.get("dyn")
        base = slot.get("base")
        return (float(dyn) if isinstance(dyn, (int, float)) else None,
                float(base) if isinstance(base, (int, float)) else None)

    def trim_price_slots(self, keep_days: int = 7) -> None:
        cutoff = dt_util.utcnow() - timedelta(days=keep_days)
        cutoff_iso = cutoff.isoformat()
        before = len(self.price_slots)
        self.price_slots = {k: v for k, v in self.price_slots.items() if k >= cutoff_iso}
        if len(self.price_slots) != before:
            self.dirty = True

    # -------------------------
    # Samples (cumulative kWh)
    # -------------------------
    def add_sample(self, ts_utc: datetime, kwh_total: float) -> bool:
        """Add a new cumulative kWh sample. Returns True if stored."""
        ts_utc = dt_util.as_utc(ts_utc)
        if not isinstance(kwh_total, (int, float)):
            return False
        kwh_total = float(kwh_total)

        # ignore duplicates (same second)
        epoch = ts_utc.timestamp()
        if self.samples and abs(self.samples[-1]["ts"] - epoch) < 1e-6:
            return False

        self.samples.append({"ts": epoch, "kwh": kwh_total})
        # keep a rolling window
        self._trim_samples(keep_days=14)
        self.dirty = True
        return True

    def _trim_samples(self, keep_days: int = 14) -> None:
        cutoff = (dt_util.utcnow() - timedelta(days=keep_days)).timestamp()
        self.samples = [s for s in self.samples if float(s.get("ts", 0)) >= cutoff]

    # -------------------------
    # Booking (15-min)
    # -------------------------
    @staticmethod
    def _slot_start_utc(ts_utc: datetime) -> datetime:
        """Floor to 15-min slot start in UTC."""
        ts_utc = dt_util.as_utc(ts_utc)
        minute = (ts_utc.minute // 15) * 15
        return ts_utc.replace(minute=minute, second=0, microsecond=0)

    def finalize_due_slots(self, now_utc: datetime) -> int:
        """Finalize any complete 15-min slots up to (now - 1min). Returns count newly booked."""
        now_utc = dt_util.as_utc(now_utc)
        cutoff = now_utc - timedelta(minutes=1)

        # Need at least 2 samples to compute delta
        if len(self.samples) < 2:
            return 0

        # Determine last booked slot start
        last_booked_start: datetime | None = None
        if self.booked:
            try:
                last_booked_start = dt_util.parse_datetime(self.booked[-1]["start"])
                if last_booked_start:
                    last_booked_start = dt_util.as_utc(last_booked_start)
            except Exception:
                last_booked_start = None

        # Start from the slot that contains the second-last sample, or after last booked
        # We'll sweep slot by slot and compute deltas between samples around the boundary.
        # Simplicity: compute deltas per slot from nearest samples around slot end.
        newly = 0
        end_slot = self._slot_start_utc(cutoff)  # slot start of current (possibly incomplete) slot
        # We can finalize slots strictly before end_slot if enough time passed
        cursor = self._slot_start_utc(dt_util.as_utc(dt_util.parse_datetime(datetime.fromtimestamp(self.samples[0]["ts"]).isoformat()) or dt_util.utcnow()))
        if last_booked_start:
            cursor = last_booked_start + timedelta(minutes=15)

        # Build sample list as (dt, kwh)
        sample_points: list[tuple[datetime, float]] = []
        for s in self.samples:
            try:
                dtp = dt_util.as_utc(datetime.fromtimestamp(float(s["ts"])))
                sample_points.append((dtp, float(s["kwh"])))
            except Exception:
                continue
        sample_points.sort(key=lambda x: x[0])

        # Helper to interpolate cumulative kWh at a given time using last known <= t
        def kwh_at(t: datetime) -> float | None:
            prev = None
            for dtp, kwh in sample_points:
                if dtp <= t:
                    prev = kwh
                else:
                    break
            return prev

        while cursor < end_slot:
            slot_end = cursor + timedelta(minutes=15)
            if slot_end > cutoff:
                break

            kwh_start = kwh_at(cursor)
            kwh_end = kwh_at(slot_end)
            if kwh_start is None or kwh_end is None:
                # missing samples
                self._append_booked(cursor, 0.0, 0.0, 0.0, 0.0, "missing_samples")
                newly += 1
                cursor += timedelta(minutes=15)
                continue

            delta = float(kwh_end - kwh_start)
            if delta < 0:
                self._append_booked(cursor, 0.0, 0.0, 0.0, 0.0, "invalid")
                newly += 1
                cursor += timedelta(minutes=15)
                continue

            dyn_p, base_p = self.get_price_slot(cursor)
            if dyn_p is None or dyn_p <= 0:
                # no price -> cannot compute
                self._append_booked(cursor, delta, 0.0, 0.0, 0.0, "unpriced")
                newly += 1
                cursor += timedelta(minutes=15)
                continue

            dyn_chf = delta * float(dyn_p)
            base_chf = delta * float(base_p) if base_p is not None and base_p > 0 else 0.0
            sav = base_chf - dyn_chf if base_chf > 0 else 0.0

            self._append_booked(cursor, delta, dyn_chf, base_chf, sav, "ok")
            newly += 1
            cursor += timedelta(minutes=15)

        # trim booked history (keep 400 days ~ plenty, but bounded)
        self._trim_booked(keep_days=400)

        if newly:
            self.dirty = True
        return newly

    def _append_booked(self, start_utc: datetime, kwh: float, dyn_chf: float, base_chf: float, sav: float, status: str) -> None:
        self.booked.append(
            {
                "start": dt_util.as_utc(start_utc).isoformat(),
                "kwh": float(kwh),
                "dyn_chf": float(dyn_chf),
                "base_chf": float(base_chf),
                "savings_chf": float(sav),
                "status": str(status),
            }
        )

    def _trim_booked(self, keep_days: int = 400) -> None:
        cutoff = dt_util.utcnow() - timedelta(days=keep_days)
        out = []
        for b in self.booked:
            dtp = dt_util.parse_datetime(str(b.get("start", "")))
            if dtp is None:
                continue
            if dt_util.as_utc(dtp) >= cutoff:
                out.append(b)
        self.booked = out

    # -------------------------
    # Totals (today/week/month/year)
    # -------------------------
    def _sum_between(self, start_local: datetime, end_local: datetime) -> tuple[float, float, float]:
        """Sum dyn/base/savings for booked slots whose start is within [start, end) in local time."""
        start_utc = dt_util.as_utc(start_local)
        end_utc = dt_util.as_utc(end_local)

        dyn = base = sav = 0.0
        for b in self.booked:
            dtp = dt_util.parse_datetime(str(b.get("start", "")))
            if dtp is None:
                continue
            s_utc = dt_util.as_utc(dtp)
            if not (start_utc <= s_utc < end_utc):
                continue
            # only count "ok" and "unpriced" with dyn/base values (unpriced is 0 anyway)
            try:
                dyn += float(b.get("dyn_chf", 0.0))
                base += float(b.get("base_chf", 0.0))
                sav += float(b.get("savings_chf", 0.0))
            except Exception:
                continue
        return dyn, base, sav

    def compute_today_totals(self) -> tuple[float, float, float]:
        now = dt_util.now()  # local
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return self._sum_between(start, end)

    def compute_week_totals(self) -> tuple[float, float, float]:
        now = dt_util.now()
        # Monday as week start (ISO)
        start = (now - timedelta(days=now.isoweekday() - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        return self._sum_between(start, end)

    def compute_month_totals(self) -> tuple[float, float, float]:
        now = dt_util.now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # next month
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return self._sum_between(start, end)

    def compute_year_totals(self) -> tuple[float, float, float]:
        now = dt_util.now()
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
        return self._sum_between(start, end)
