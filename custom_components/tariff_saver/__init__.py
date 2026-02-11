"""Tariff Saver integration."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.util import dt as dt_util

from .api import EkzTariffApi
from .const import DOMAIN, CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME
from .coordinator import TariffSaverCoordinator

PLATFORMS: list[str] = ["sensor"]

RETRY_INTERVAL = timedelta(minutes=30)


def _parse_hhmm(value: str) -> tuple[int, int]:
    """Parse 'HH:MM' -> (hour, minute). Fallback to default on bad input."""
    try:
        hh, mm = value.strip().split(":")
        h = int(hh)
        m = int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return 18, 15


def _has_valid_prices(coordinator: TariffSaverCoordinator) -> bool:
    """True if coordinator has at least one non-zero electricity price slot."""
    data = coordinator.data or {}
    active = data.get("active") if isinstance(data, dict) else None
    if not isinstance(active, list) or not active:
        return False

    for slot in active:
        # PriceSlot has electricity_chf_per_kwh; older versions used price_chf_per_kwh
        v = getattr(slot, "electricity_chf_per_kwh", None)
        if v is None:
            v = getattr(slot, "price_chf_per_kwh", None)
        if isinstance(v, (int, float)) and float(v) > 0:
            return True
    return False


def _next_local_midnight(now_local: datetime) -> datetime:
    """Return next local midnight (start of next day)."""
    tomorrow = (now_local + timedelta(days=1)).date()
    return dt_util.as_local(dt_util.as_utc(datetime.combine(tomorrow, time(0, 0), tzinfo=now_local.tzinfo)))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tariff Saver from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # IMPORTANT: merge data + options into a single config dict
    config = dict(entry.data)
    config.update(dict(entry.options))

    session = async_get_clientsession(hass)

    # ------------------------------------------------------------------
    # OAuth2Session (myEKZ)
    # ------------------------------------------------------------------
    oauth_session = None
    if config.get("mode") == "myekz":
        auth_impl = entry.data.get("auth_implementation")
        if not auth_impl:
            raise ValueError(
                "myEKZ mode selected but OAuth2 was not completed "
                "(missing entry.data['auth_implementation']). "
                "Please remove the integration and add it again, then complete the login."
            )

        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
        oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    api = EkzTariffApi(session, oauth_session=oauth_session)

    coordinator = TariffSaverCoordinator(hass, api, config=config)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    publish_time = config.get(CONF_PUBLISH_TIME, DEFAULT_PUBLISH_TIME)
    hour, minute = _parse_hhmm(publish_time)

    # Retry window state (per entry)
    retry_state_key = f"{entry.entry_id}_retry_until"
    hass.data[DOMAIN][retry_state_key] = None  # datetime | None

    async def _force_refresh() -> None:
        # Coordinator caches per day; for retries we must force a refetch.
        try:
            setattr(coordinator, "_last_fetch_date", None)
        except Exception:
            pass
        await coordinator.async_request_refresh()

    async def _daily_refresh(now) -> None:  # noqa: ANN001
        await _force_refresh()

        # If still no valid prices, enable retry until midnight (local)
        if not _has_valid_prices(coordinator):
            now_local = dt_util.now()
            hass.data[DOMAIN][retry_state_key] = _next_local_midnight(now_local)
        else:
            hass.data[DOMAIN][retry_state_key] = None

    async def _retry_tick(now) -> None:  # noqa: ANN001
        until = hass.data[DOMAIN].get(retry_state_key)
        if not isinstance(until, datetime):
            return

        now_local = dt_util.now()
        # Stop after midnight
        if now_local >= until:
            hass.data[DOMAIN][retry_state_key] = None
            return

        # Only retry if still missing/invalid
        if not _has_valid_prices(coordinator):
            await _force_refresh()
        else:
            hass.data[DOMAIN][retry_state_key] = None

    # Daily trigger at publish_time
    unsub_daily = async_track_time_change(hass, _daily_refresh, hour=hour, minute=minute, second=0)
    hass.data[DOMAIN][entry.entry_id + "_unsub_daily"] = unsub_daily

    # Retry tick every 30 minutes (only active when retry_until is set)
    unsub_retry = async_track_time_interval(hass, _retry_tick, RETRY_INTERVAL)
    hass.data[DOMAIN][entry.entry_id + "_unsub_retry"] = unsub_retry

    # First refresh immediately so entities are populated
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Tariff Saver config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    unsub = hass.data.get(DOMAIN, {}).pop(entry.entry_id + "_unsub_daily", None)
    if unsub:
        unsub()

    unsub = hass.data.get(DOMAIN, {}).pop(entry.entry_id + "_unsub_retry", None)
    if unsub:
        unsub()

    hass.data.get(DOMAIN, {}).pop(f"{entry.entry_id}_retry_until", None)

    if unload_ok and DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
