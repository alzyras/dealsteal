"""Extract stable structured fields from public eBay item pages."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .locales import normalize_country
from .models import Money


@dataclass(frozen=True)
class DetailData:
    listing_type: str | None = None
    price: Money | None = None
    end_time: datetime | None = None
    origin_country: str | None = None
    ship_to_country: str | None = None
    ship_to_postal_code: str | None = None
    shipping: Money | None = None
    import_duty: Money | None = None
    import_vat: Money | None = None
    shipping_known: bool = False
    import_cost_known: bool = False


def _iso_time(html_text: str) -> datetime | None:
    match = re.search(
        r'"endTime".{0,700}?"value"\s*:\s*"([^" ]+Z)"', html_text, re.DOTALL
    )
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None


def _money(value: str, currency: str) -> Money | None:
    try:
        return Money(Decimal(value), currency)
    except (ArithmeticError, ValueError):
        return None


def _shipping_options(html_text: str) -> list[tuple[Money, str]]:
    pattern = re.compile(
        r'"shippingCost"\s*:\s*\{.*?"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
        r'.*?"currency"\s*:\s*"([A-Z]{3})".*?"shipToLocations"\s*:\s*\[([^\]]*)\]',
        re.DOTALL,
    )
    options: list[tuple[Money, str]] = []
    for match in pattern.finditer(html_text):
        money = _money(match.group(1), match.group(2))
        if money:
            options.append((money, match.group(3)))
    return options


def _item_price(html_text: str) -> Money | None:
    patterns = (
        r'"currentPrice"\s*:\s*\{.*?"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
        r'.*?"currency"\s*:\s*"([A-Z]{3})"',
        r'"price"\s*:\s*\{.*?"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
        r'.*?"currency"\s*:\s*"([A-Z]{3})"',
    )
    for pattern in patterns:
        match = re.search(pattern, html_text, re.DOTALL)
        if match:
            return _money(match.group(1), match.group(2))
    return None


def _detect_listing_type(html_text: str) -> str | None:
    upper = html_text.upper()
    for pattern in (
        r'"buyingFormat"\s*:\s*"([A-Z_]+)"',
        r'"listingType"\s*:\s*"([A-Z_]+)"',
    ):
        match = re.search(pattern, upper, re.IGNORECASE)
        if match:
            value = match.group(1)
            if "AUCTION" in value:
                return "auction"
            if "FIXED" in value or "BUY_IT_NOW" in value or "BIN" in value:
                return "buy_it_now"
    if re.search(
        r'"bid(?:Count|Summary|History)"|"auction"\s*:', html_text, re.IGNORECASE
    ):
        return "auction"
    if re.search(
        r'"buyItNow"\s*:|"fixedPrice"\s*:|"buy[_ ]?it[_ ]?now"',
        html_text,
        re.IGNORECASE,
    ):
        return "buy_it_now"
    return None


def _ship_to(html_text: str) -> tuple[str | None, str | None]:
    match = re.search(
        r'"shipToLocation"\s*:\s*\{.{0,500}?"country"\s*:\s*"([A-Z]{2})"'
        r'.{0,200}?"postalCode"\s*:\s*"([^"]*)"',
        html_text,
        re.DOTALL,
    )
    if match:
        return normalize_country(match.group(1)), match.group(2)
    return None, None


def _origin_country(html_text: str) -> str | None:
    patterns = (
        r'"itemLocation"\s*:\s*\{.{0,700}?"country"\s*:\s*"([A-Z]{2,3})"',
        r'"sellerLocation"\s*:\s*\{.{0,700}?"country"\s*:\s*"([A-Z]{2,3})"',
    )
    for pattern in patterns:
        match = re.search(pattern, html_text, re.DOTALL | re.IGNORECASE)
        if match:
            return normalize_country(match.group(1))
    return None


def _import_costs(html_text: str) -> tuple[Money | None, Money | None, bool]:
    duty = vat = None
    found = False
    for label, amount, currency in re.findall(
        r'"(importCharges|duty|vat|taxes)".{0,450}?"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
        r'.{0,100}?"currency"\s*:\s*"([A-Z]{3})"',
        html_text,
        re.DOTALL | re.IGNORECASE,
    ):
        value = _money(amount, currency)
        if value is None:
            continue
        found = True
        if label.lower() in {"duty", "importcharges"}:
            duty = value
        else:
            vat = value
    return duty, vat, found


def parse_detail_html(
    html_text: str, destination_country: str | None = None
) -> DetailData:
    """Parse structured data while tolerating escaped HTML and locale changes."""
    del destination_country  # The page's viewer ship-to block is authoritative.
    body = html.unescape(html_text)
    listing_type = _detect_listing_type(body)
    ship_country, postal_code = _ship_to(body)
    options = _shipping_options(body)
    shipping = options[0][0] if options else None
    duty, vat, import_known = _import_costs(body)
    return DetailData(
        listing_type=listing_type,
        price=_item_price(body),
        end_time=_iso_time(body),
        origin_country=_origin_country(body),
        ship_to_country=ship_country,
        ship_to_postal_code=postal_code,
        shipping=shipping,
        import_duty=duty,
        import_vat=vat,
        shipping_known=bool(options),
        import_cost_known=import_known,
    )
