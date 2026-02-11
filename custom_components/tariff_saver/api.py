"""API client for EKZ tariffs (Public + myEKZ OAuth2).

Public:
- GET /v1/tariffs -> {"publication_timestamp":..., "prices":[...]}

Protected (OAuth2 Bearer):
- GET /v1/emsLinkStatus
- GET /v1/customerTariffs

Key fix:
- EKZ component fields (e.g. "electricity") are lists like:
    "electricity": [{"unit":"CHF_m","value":3.0},{"unit":"CHF_kWh","value":0.11933}]
  We must pick unit == "CHF_kWh".
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Final

import aiohttp
from aiohttp import ClientError

from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

_LOGGER = logging.getLogger(__name__)


class EkzTariffApiError(RuntimeError):
    """Raised when EKZ API calls fail."""


class EkzTariffAuthError(EkzTariffApiError):
    """Raised for OAuth/token problems."""


class EkzTariffApi:
    BASE_URL: Final[str] = "https://api.tariffs.ekz.ch/v1"

    # Units we treat as CHF/kWh
    CHF_PER_KWH_UNITS: Final[set[str]] = {"CHF_kWh", "CHF/kWh"}

    def __init__(self, session: aiohttp.ClientSession, oauth_session: OAuth2Session | None = None) -> None:
        self._session = session
        self._oauth_session = oauth_session

    async def fetch_prices(
        self,
        tariff_name: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"tariff_name": tariff_name}
        if start is not None and end is not None:
            params["start_timestamp"] = start.isoformat()
            params["end_timestamp"] = end.isoformat()

        url = f"{self.BASE_URL}/tariffs"
        _LOGGER.debug("Fetching EKZ tariffs: %s", params)

        async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()

        prices = payload.get("prices")
        if not isinstance(prices, list):
            raise ValueError(f"Unexpected EKZ payload shape, missing 'prices': {payload!r}")
        return prices

    # ------------------------- component parsing -------------------------
    @classmethod
    def _sum_list_unit(cls, val: Any) -> float | None:
        """Sum entries with unit CHF_kWh in a list like [{"unit":..,"value":..}, ...]."""
        if not isinstance(val, list):
            return None
        total = 0.0
        found = False
        for entry in val:
            if not isinstance(entry, dict):
                continue
            unit = entry.get("unit")
            if not isinstance(unit, str) or unit not in cls.CHF_PER_KWH_UNITS:
                continue
            v = entry.get("value")
            if isinstance(v, (int, float)):
                total += float(v)
                found = True
        return total if found else None

    @classmethod
    def parse_components_chf_per_kwh(cls, price_item: dict[str, Any]) -> dict[str, float]:
        """Return per-component CHF/kWh for all fields that carry CHF_kWh values."""
        out: dict[str, float] = {}
        for key, val in price_item.items():
            if key in ("start_timestamp", "end_timestamp", "publication_timestamp"):
                continue

            # Most components are list-form (as in your sample payload)
            s = cls._sum_list_unit(val)
            if isinstance(s, (int, float)) and s != 0.0:
                out[str(key)] = float(s)
                continue

            # Some APIs may return dict-form {"unit":..,"value":..}
            if isinstance(val, dict):
                unit = val.get("unit")
                if isinstance(unit, str) and unit in cls.CHF_PER_KWH_UNITS:
                    v = val.get("value")
                    if isinstance(v, (int, float)) and float(v) != 0.0:
                        out[str(key)] = float(v)
                        continue

            # Or raw float
            if isinstance(val, (int, float)) and float(val) != 0.0:
                out[str(key)] = float(val)
        return out

    @classmethod
    def electricity_chf_per_kwh(cls, price_item: dict[str, Any]) -> float:
        comps = cls.parse_components_chf_per_kwh(price_item)
        v = comps.get("electricity")
        return float(v) if isinstance(v, (int, float)) else 0.0

    @classmethod
    def sum_chf_per_kwh(cls, price_item: dict[str, Any]) -> float:
        """Backwards-compatible: return electricity CHF/kWh (what your old sensors used)."""
        return cls.electricity_chf_per_kwh(price_item)

    # ------------------------- Protected (myEKZ) -------------------------
    async def _async_get_access_token(self) -> str:
        if not self._oauth_session:
            raise EkzTariffAuthError("No OAuth session available (myEKZ not configured)")
        try:
            await self._oauth_session.async_ensure_token_valid()
        except Exception as err:
            raise EkzTariffAuthError(f"OAuth token invalid/refresh failed: {err}") from err

        token = self._oauth_session.token or {}
        access_token = token.get("access_token")
        if not access_token:
            raise EkzTariffAuthError("OAuth token missing access_token")
        return access_token

    async def fetch_ems_link_status(self, *, ems_instance_id: str, redirect_uri: str) -> dict[str, Any]:
        access_token = await self._async_get_access_token()
        url = f"{self.BASE_URL}/emsLinkStatus"
        params = {"ems_instance_id": ems_instance_id, "redirect_uri": redirect_uri}
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        try:
            async with self._session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                if resp.status == 401:
                    raise EkzTariffAuthError(f"401 Unauthorized: {text}")
                if resp.status >= 400:
                    raise EkzTariffApiError(f"EKZ API error {resp.status}: {text}")
                data: Any = await resp.json()
        except ClientError as err:
            raise EkzTariffApiError(f"HTTP error calling emsLinkStatus: {err}") from err

        if not isinstance(data, dict):
            raise EkzTariffApiError(f"Unexpected emsLinkStatus payload: {data!r}")
        return data

    async def fetch_customer_tariffs(
        self,
        *,
        ems_instance_id: str,
        tariff_type: str | None = None,
        start_timestamp: str | None = None,
        end_timestamp: str | None = None,
    ) -> list[dict[str, Any]]:
        access_token = await self._async_get_access_token()
        url = f"{self.BASE_URL}/customerTariffs"
        params: dict[str, str] = {"ems_instance_id": ems_instance_id}
        if tariff_type:
            params["tariff_type"] = tariff_type
        if start_timestamp:
            params["start_timestamp"] = start_timestamp
        if end_timestamp:
            params["end_timestamp"] = end_timestamp

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        try:
            async with self._session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                if resp.status == 401:
                    raise EkzTariffAuthError(f"401 Unauthorized: {text}")
                if resp.status >= 400:
                    raise EkzTariffApiError(f"EKZ API error {resp.status}: {text}")
                data: Any = await resp.json()
        except ClientError as err:
            raise EkzTariffApiError(f"HTTP error calling customerTariffs: {err}") from err

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            tariffs = data.get("tariffs")
            if isinstance(tariffs, list):
                return tariffs
        raise EkzTariffApiError(f"Unexpected customerTariffs payload: {data!r}")
