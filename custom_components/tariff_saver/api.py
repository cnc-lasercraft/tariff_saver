"""API client for EKZ tariffs."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class EkzTariffApi:
    """Client for the EKZ tariff API.

    Notes about response structure:
    - /v1/tariffs returns an object with a top-level `prices` list.
    - Each price item contains `start_timestamp` (and usually `end_timestamp`)
      plus multiple tariff component fields.
    - We sum all component entries where unit == "CHF_kWh".
    """

    BASE_URL = "https://api.tariffs.ekz.ch/v1"

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

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

    async def fetch_customer_tariffs(self, access_token: str) -> list[dict[str, Any]]:
        """Fetch customer-specific tariffs using OAuth access token."""
        url = f"{self.BASE_URL}/customerTariffs"
        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        _LOGGER.debug("Fetching customer tariffs")

        async with self._session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            payload: Any = await resp.json()

        # Some APIs return a plain list, others wrap it in an object.
        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            tariffs = payload.get("tariffs")
            if isinstance(tariffs, list):
                return tariffs

        raise ValueError(f"Unexpected customerTariffs payload: {payload!r}")

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
