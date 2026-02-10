"""API client for EKZ tariffs (Public + myEKZ OAuth2).

This is a SAFE merge of your working public api.py plus the myEKZ protected calls.

Public:
- GET /v1/tariffs  (returns {prices:[...]})

Protected (OAuth2 Bearer):
- GET /v1/emsLinkStatus (requires ems_instance_id + redirect_uri)
- GET /v1/customerTariffs (requires ems_instance_id)

OAuth2:
- Uses Home Assistant OAuth2Session (Application Credentials)
- Client ID/Secret stay in HA, NOT in code.

IMPORTANT:
- No entities are defined here.
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
    """Client for the EKZ tariff API.

    Notes about response structure:
    - /v1/tariffs returns an object with a top-level `prices` list.
    - Each price item contains `start_timestamp` (and usually `end_timestamp`)
      plus multiple tariff component fields.
    - We sum all component entries where unit == "CHF_kWh".
    """

    BASE_URL: Final[str] = "https://api.tariffs.ekz.ch/v1"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        oauth_session: OAuth2Session | None = None,
    ) -> None:
        self._session = session
        self._oauth_session = oauth_session

    # ---------------------------------------------------------------------
    # Public (existing) method (UNCHANGED)
    # ---------------------------------------------------------------------
    async def fetch_prices(
        self,
        tariff_name: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch raw price items (entries from `prices`).

        If start/end are omitted, the API returns tariffs of the current date
        (00:00–24:00 local), per EKZ API documentation.
        """
        params: dict[str, Any] = {
            "tariff_name": tariff_name,
        }

        if start is not None and end is not None:
            params["start_timestamp"] = start.isoformat()
            params["end_timestamp"] = end.isoformat()

        url = f"{self.BASE_URL}/tariffs"

        _LOGGER.debug("Fetching EKZ tariffs: %s", params)

        async with self._session.get(
            url,
            params=params,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()

        prices = payload.get("prices")
        if not isinstance(prices, list):
            raise ValueError(
                f"Unexpected EKZ payload shape, missing 'prices': {payload!r}"
            )

        return prices

    # ---------------------------------------------------------------------
    # Price helper (UNCHANGED)
    # ---------------------------------------------------------------------
    @staticmethod
    def sum_chf_per_kwh(price_item: dict[str, Any]) -> float:
        """Sum all component values where unit == 'CHF_kWh' for a single 15-min slot.

        Returns a NET value (without VAT), as delivered by the API.
        """
        total = 0.0
        for _key, val in price_item.items():
            if isinstance(val, list):
                for entry in val:
                    if isinstance(entry, dict) and entry.get("unit") == "CHF_kWh":
                        v = entry.get("value")
                        if isinstance(v, (int, float)):
                            total += float(v)
        return total

    # ---------------------------------------------------------------------
    # Protected (myEKZ) helpers
    # ---------------------------------------------------------------------
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

    # ---------------------------------------------------------------------
    # Protected: /v1/emsLinkStatus
    # ---------------------------------------------------------------------
    async def fetch_ems_link_status(
        self,
        *,
        ems_instance_id: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        """Fetch current EMS link status (protected endpoint).

        GET /v1/emsLinkStatus?ems_instance_id=...&redirect_uri=...

        Returns:
          {
            "link_status": "link_required" | "link_established" | "link_unavailable",
            "linking_process_redirect_uri": "https://....",
            ...
          }
        """
        access_token = await self._async_get_access_token()

        url = f"{self.BASE_URL}/emsLinkStatus"
        params = {
            "ems_instance_id": ems_instance_id,
            "redirect_uri": redirect_uri,
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        try:
            async with self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
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

    # ---------------------------------------------------------------------
    # Protected: /v1/customerTariffs
    # ---------------------------------------------------------------------
    async def fetch_customer_tariffs(
        self,
        *,
        ems_instance_id: str,
        tariff_type: str | None = None,
        start_timestamp: str | None = None,
        end_timestamp: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch customer-specific tariffs (protected endpoint).

        GET /v1/customerTariffs?ems_instance_id=...

        NOTE:
        EKZ requires that linking is completed first (link_established).
        """
        access_token = await self._async_get_access_token()

        url = f"{self.BASE_URL}/customerTariffs"
        params: dict[str, str] = {"ems_instance_id": ems_instance_id}

        if tariff_type:
            params["tariff_type"] = tariff_type
        if start_timestamp:
            params["start_timestamp"] = start_timestamp
        if end_timestamp:
            params["end_timestamp"] = end_timestamp

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        try:
            async with self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                if resp.status == 401:
                    raise EkzTariffAuthError(f"401 Unauthorized: {text}")
                if resp.status >= 400:
                    raise EkzTariffApiError(f"EKZ API error {resp.status}: {text}")

                data: Any = await resp.json()
        except ClientError as err:
            raise EkzTariffApiError(f"HTTP error calling customerTariffs: {err}") from err

        # Some APIs return a plain list, others wrap it in an object.
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            tariffs = data.get("tariffs")
            if isinstance(tariffs, list):
                return tariffs

        raise EkzTariffApiError(f"Unexpected customerTariffs payload: {data!r}")
