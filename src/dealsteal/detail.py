"""Extract stable structured fields from public eBay item pages."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from .locales import EU_COUNTRIES, normalize_country
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


_ISO_TIMESTAMP_RE = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?" r"(?:Z|[+-]\d{2}:?\d{2}))\b"
)


def _iso_time(html_text: str) -> datetime | None:
    """Extract an absolute timestamp, regardless of eBay's wrapper shape.

    eBay has emitted ``endTime`` as a string, ``{value: ...}``, and inside
    escaped JSON blobs.  We deliberately accept only ISO timestamps with an
    explicit timezone; localized countdown text is never converted here.
    """
    candidates: list[str] = []
    for match in re.finditer(r'"(?:endTime|endDate)"\s*:\s*', html_text, re.I):
        candidates.extend(
            _ISO_TIMESTAMP_RE.findall(html_text[match.end() : match.end() + 1200])
        )
    if not candidates:
        candidates = _ISO_TIMESTAMP_RE.findall(html_text)
    for value in candidates:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    return None


def _primary_buy_box(html_text: str) -> str:
    """Return the main item buy box, excluding related-item cards.

    Modern eBay item pages embed many related listings after the primary
    module.  Searching the whole document can therefore pick a related
    listing's price, format, or end date.  The first BUY_BOX module is the
    stable boundary for the item being viewed.
    """
    match = re.search(r'"BUY_BOX"\s*:\s*\{', html_text, re.IGNORECASE)
    if not match:
        return ""
    return html_text[match.start() : match.start() + 16000]


def _money(value: str, currency: str) -> Money | None:
    try:
        normalized = value.strip().replace(" ", "")
        if "," in normalized and "." in normalized:
            normalized = (
                normalized.replace(".", "").replace(",", ".")
                if normalized.rfind(",") > normalized.rfind(".")
                else normalized.replace(",", "")
            )
        elif "," in normalized:
            normalized = normalized.replace(",", ".")
        return Money(Decimal(normalized), currency)
    except (ArithmeticError, ValueError):
        return None


def _shipping_options(html_text: str) -> list[tuple[Money, str]]:
    pattern = re.compile(
        r'"shippingCost"\s*:\s*\{.*?'
        r'(?:"amount"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?|'
        r'"original"\s*:\s*\{.*?"amount"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?)'
        r'.*?"currency"\s*:\s*"([A-Z]{3})".*?'
        r'"shipToLocations"\s*:\s*\[([^\]]*)\]',
        re.DOTALL,
    )
    options: list[tuple[Money, str]] = []
    for match in pattern.finditer(html_text):
        money = _money(match.group(1) or match.group(2), match.group(3))
        if money:
            options.append((money, match.group(4)))
    if options:
        return options

    # JSON-LD uses OfferShippingDetails rather than the GraphQL shape above.
    for match in re.finditer(
        r'"shippingRate"\s*:\s*\{.*?"value"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?'
        r'.*?"currency"\s*:\s*"([A-Z]{3})".*?'
        r'"addressCountry"\s*:\s*"([A-Za-z]{2,3})"',
        html_text,
        re.DOTALL | re.IGNORECASE,
    ):
        money = _money(match.group(1), match.group(2))
        if money:
            options.append((money, match.group(3)))
    return options


def _shipping_for_destination(
    options: list[tuple[Money, str]], destination_country: str | None
) -> Money | None:
    if not options:
        return None
    country = normalize_country(destination_country)
    iso3 = {
        "AT": "ATU",
        "BE": "BEL",
        "DE": "DEU",
        "ES": "ESP",
        "FR": "FRA",
        "IE": "IRL",
        "IT": "ITA",
        "LT": "LTU",
        "NL": "NLD",
        "PL": "POL",
        "GB": "GBR",
        "CH": "CHE",
        "US": "USA",
        "CA": "CAN",
        "AU": "AUS",
    }.get(country or "")
    for money, destinations in options:
        destination_text = destinations.upper()
        if iso3 and iso3 in destination_text:
            return money
        if country and country in EU_COUNTRIES and "_EU" in destination_text:
            return money
        if "WORLDWIDE" in destination_text or "ALL" in destination_text:
            return money
    return None


def _item_price(html_text: str) -> Money | None:
    primary = _primary_buy_box(html_text)
    if primary:
        # Auctions expose the current bid value directly in the primary box;
        # fixed-price listings expose the BIN value in binModel.
        if re.search(r'"bidInfo"\s*:', primary, re.IGNORECASE):
            bid_value = re.search(
                r'"value"\s*:\s*\{\s*"value"\s*:\s*([0-9]+(?:[.,][0-9]+)?)'
                r'\s*,\s*"currency"\s*:\s*"([A-Z]{3})"',
                primary,
                re.DOTALL | re.IGNORECASE,
            )
            if bid_value:
                return _money(bid_value.group(1), bid_value.group(2))
        bin_value = re.search(
            r'"binModel".*?"price"\s*:\s*\{.*?"value"\s*:\s*\{\s*'
            r'"value"\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*,\s*'
            r'"currency"\s*:\s*"([A-Z]{3})"',
            primary,
            re.DOTALL | re.IGNORECASE,
        )
        if bin_value:
            return _money(bin_value.group(1), bin_value.group(2))

    # JSON-LD is intentionally after the primary buy box: related cards can
    # contain their own offers and are not authoritative for this item.
    json_ld = re.search(
        r'"priceCurrency"\s*:\s*"([A-Z]{3})"\s*,\s*"price"\s*:\s*"?'
        r'([0-9]+(?:[.,][0-9]+)?)"?',
        html_text,
        re.DOTALL | re.IGNORECASE,
    )
    if json_ld:
        return _money(json_ld.group(2), json_ld.group(1))

    patterns = (
        r'"currentPrice"\s*:\s*\{.*?"amount"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?'
        r'.*?"currency"\s*:\s*"([A-Z]{3})"',
        r'"price"\s*:\s*\{.*?"amount"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?'
        r'.*?"currency"\s*:\s*"([A-Z]{3})"',
    )
    for pattern in patterns:
        match = re.search(pattern, html_text, re.DOTALL)
        if match:
            return _money(match.group(1), match.group(2))
    return None


def _detect_listing_type(html_text: str) -> str | None:
    primary = _primary_buy_box(html_text)
    if primary:
        if re.search(
            r'"bidInfo"\s*:.*?"endTime"\s*:', primary, re.DOTALL | re.IGNORECASE
        ):
            return "auction"
        if re.search(r'"binModel"\s*:', primary, re.IGNORECASE):
            return "buy_it_now"

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
    for key in ("shipToLocation", "shipToAddress", "shippingAddress"):
        key_match = re.search(rf'"{key}"\s*:\s*', html_text, re.IGNORECASE)
        if not key_match:
            continue
        block = html_text[key_match.end() : key_match.end() + 1800]
        country_match = re.search(
            r'"(?:country|countryCode)"\s*:\s*"([A-Za-z]{2,3})"',
            block,
            re.IGNORECASE,
        )
        postal_match = re.search(
            r'"(?:postalCode|postal_code|zip)"\s*:\s*"([^"]*)"',
            block,
            re.IGNORECASE,
        )
        if country_match and postal_match:
            return (
                normalize_country(country_match.group(1)),
                postal_match.group(1).strip(),
            )
    return None, None


def _origin_country(html_text: str) -> str | None:
    # Prefer the localized item-page location.  Structured pages can also
    # contain marketplace/related-item locations, which are not the seller's
    # origin for the item being verified.
    labels = (
        "Located in",
        "from",
        "Ubicado en",
        "Standort",
        "Standort des Artikels",
        "Oggetto che si trova a",
        "Objet situé à",
        "Object located in",
    )
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = rf">(?:{label_pattern})\s*:?[ \t]*([^<]{{2,180}})<"
    for match in re.finditer(pattern, html_text, re.IGNORECASE):
        visible = " ".join(html.unescape(match.group(1)).split())
        if country := normalize_country(visible):
            return country

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
        r'"(importCharges|duty|vat|taxes)".{0,450}?"amount"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?'
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
    body = html.unescape(html_text).replace(r'\"', '"')
    listing_type = _detect_listing_type(body)
    ship_country, postal_code = _ship_to(body)
    primary = _primary_buy_box(body)
    options = _shipping_options(primary or body)
    shipping = _shipping_for_destination(options, destination_country)
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
        shipping_known=shipping is not None,
        import_cost_known=import_known,
    )
