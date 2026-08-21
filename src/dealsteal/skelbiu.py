"""Client for the user's local browserless Skelbiu API.

The API is intentionally treated as a separate trust boundary: search results
are discovery only, while the individual listing endpoint is authoritative for
whether a comparison listing is still active.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import requests

from .models import Money, ResaleListing, ScannerConfig


class SkelbiuApiError(RuntimeError):
    """The local API could not provide a trustworthy response."""


@dataclass(frozen=True)
class SkelbiuSearchResult:
    listings: tuple[ResaleListing, ...]
    searched: int
    active: int
    rejected_inactive: int


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def parse_skelbiu_price(value: Any, currency: str = "EUR") -> Money | None:
    """Parse API prices such as ``159 €`` and ``1 234,50 EUR``."""
    if value is None or value == "":
        return None
    raw = str(value).replace("\u00a0", " ").strip()
    if not raw:
        return None
    number = re.sub(r"[^0-9,\.\-]", "", raw)
    if not number:
        return None
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number:
        tail = number.rsplit(",", 1)[1]
        number = number.replace(",", "." if len(tail) <= 2 else "")
    elif number.count(".") > 1:
        number = number.replace(".", "")
    try:
        return Money(Decimal(number), currency)
    except (ArithmeticError, ValueError):
        return None


class SkelbiuClient:
    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "dealsteal/0.1 (+local Skelbiu API client)",
            }
        )
        self.requests = 0

    def close(self) -> None:
        self.session.close()

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        self.requests += 1
        try:
            response = self.session.get(
                f"{self.config.skelbiu_api_base_url}{path}",
                params=params,
                timeout=self.config.skelbiu_api_timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as error:
            raise SkelbiuApiError(
                f"Skelbiu API request failed for {path}: {error}"
            ) from error
        if not isinstance(body, dict):
            raise SkelbiuApiError(f"Skelbiu API returned a non-object for {path}")
        return body

    @staticmethod
    def _summary_to_listing(item: dict[str, Any]) -> tuple[str, str, str, Money | None]:
        item_id = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        price = parse_skelbiu_price(item.get("price"), str(item.get("currency", "EUR")))
        return item_id, title, url, price

    def search_active(
        self,
        query: str,
        *,
        search_limit: int | None = None,
        detail_limit: int | None = None,
    ) -> SkelbiuSearchResult:
        body = self._get(
            "/v1/listings",
            {
                "q": query,
                "limit": str(search_limit or self.config.skelbiu_search_limit),
            },
        )
        raw_items = body.get("items")
        if not isinstance(raw_items, list):
            raise SkelbiuApiError("Skelbiu API response has no items list")

        listings: list[ResaleListing] = []
        seen: set[str] = set()
        rejected_inactive = 0
        examined = 0
        max_details = detail_limit or self.config.skelbiu_detail_limit
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            (
                item_id,
                summary_title,
                summary_url,
                summary_price,
            ) = self._summary_to_listing(raw)
            if not item_id or item_id in seen or examined >= max_details:
                continue
            seen.add(item_id)
            examined += 1
            try:
                detail = self._get(f"/v1/listings/{item_id}")
            except SkelbiuApiError:
                continue
            status = str(detail.get("status", "unknown")).strip().casefold()
            if status != "active":
                rejected_inactive += 1
                continue
            title = str(detail.get("title") or summary_title).strip()
            url = str(detail.get("url") or summary_url).strip()
            currency = str(detail.get("currency") or "EUR")
            price = parse_skelbiu_price(detail.get("price"), currency) or summary_price
            if not price or price.amount <= 0:
                continue
            listings.append(
                ResaleListing(
                    item_id=item_id,
                    title=title,
                    url=url,
                    price=price,
                    status=status,
                    location=(
                        str(detail["location"]) if detail.get("location") else None
                    ),
                    updated_at=_parse_datetime(detail.get("updated_at")),
                    fetched_at=datetime.now(UTC),
                )
            )
        return SkelbiuSearchResult(
            listings=tuple(listings),
            searched=len(raw_items),
            active=len(listings),
            rejected_inactive=rejected_inactive,
        )
