"""Marketplace and locale registry for public eBay pages."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from babel import Locale


@dataclass(frozen=True)
class Marketplace:
    code: str
    country: str
    host: str
    locale: str
    currency: str
    global_id: str


# Kept as data so adding a marketplace does not require touching parsers.
MARKETPLACES: tuple[Marketplace, ...] = (
    Marketplace("EBAY_US", "US", "www.ebay.com", "en-US", "USD", "EBAY-US"),
    Marketplace("EBAY_AT", "AT", "www.ebay.at", "de-AT", "EUR", "EBAY-AT"),
    Marketplace("EBAY_AU", "AU", "www.ebay.com.au", "en-AU", "AUD", "EBAY-AU"),
    Marketplace("EBAY_BE_NL", "BE", "www.benl.ebay.be", "nl-BE", "EUR", "EBAY-NLBE"),
    Marketplace("EBAY_BE_FR", "BE", "www.befr.ebay.be", "fr-BE", "EUR", "EBAY-FRBE"),
    Marketplace("EBAY_CA", "CA", "www.ebay.ca", "en-CA", "CAD", "EBAY-ENCA"),
    Marketplace("EBAY_CA_FR", "CA", "www.cafr.ebay.ca", "fr-CA", "CAD", "EBAY-FRCA"),
    Marketplace("EBAY_CH", "CH", "www.ebay.ch", "de-CH", "CHF", "EBAY-CH"),
    Marketplace("EBAY_DE", "DE", "www.ebay.de", "de-DE", "EUR", "EBAY-DE"),
    Marketplace("EBAY_ES", "ES", "www.ebay.es", "es-ES", "EUR", "EBAY-ES"),
    Marketplace("EBAY_FR", "FR", "www.ebay.fr", "fr-FR", "EUR", "EBAY-FR"),
    Marketplace("EBAY_GB", "GB", "www.ebay.co.uk", "en-GB", "GBP", "EBAY-GB"),
    Marketplace("EBAY_HK", "HK", "www.ebay.com.hk", "zh-HK", "HKD", "EBAY-HK"),
    Marketplace("EBAY_IE", "IE", "www.ebay.ie", "en-IE", "EUR", "EBAY-IE"),
    Marketplace("EBAY_IN", "IN", "www.ebay.in", "en-IN", "INR", "EBAY-IN"),
    Marketplace("EBAY_IT", "IT", "www.ebay.it", "it-IT", "EUR", "EBAY-IT"),
    Marketplace("EBAY_MY", "MY", "www.ebay.com.my", "en-US", "MYR", "EBAY-MY"),
    Marketplace("EBAY_NL", "NL", "www.ebay.nl", "nl-NL", "EUR", "EBAY-NL"),
    Marketplace("EBAY_PH", "PH", "www.ebay.ph", "en-PH", "PHP", "EBAY-PH"),
    Marketplace("EBAY_PL", "PL", "www.ebay.pl", "pl-PL", "PLN", "EBAY-PL"),
    Marketplace("EBAY_SG", "SG", "www.ebay.com.sg", "en-SG", "SGD", "EBAY-SG"),
    Marketplace("EBAY_TW", "TW", "www.ebay.com.tw", "zh-TW", "TWD", "EBAY-TW"),
)

EU_COUNTRIES = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DE",
        "DK",
        "EE",
        "ES",
        "FI",
        "FR",
        "GR",
        "HU",
        "IE",
        "IT",
        "LT",
        "LU",
        "LV",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SE",
        "SI",
        "SK",
    }
)


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).strip()


_COUNTRY_ALIASES = {
    "austria": "AT",
    "osterreich": "AT",
    "autriche": "AT",
    "belgium": "BE",
    "belgique": "BE",
    "belgie": "BE",
    "belgien": "BE",
    "bulgaria": "BG",
    "croatia": "HR",
    "cyprus": "CY",
    "czechia": "CZ",
    "czech republic": "CZ",
    "tschechien": "CZ",
    "denmark": "DK",
    "danmark": "DK",
    "estonia": "EE",
    "finland": "FI",
    "france": "FR",
    "frankreich": "FR",
    "germany": "DE",
    "deutschland": "DE",
    "de": "DE",
    "greece": "GR",
    "hungary": "HU",
    "ireland": "IE",
    "italy": "IT",
    "italia": "IT",
    "latvia": "LV",
    "lithuania": "LT",
    "lietuva": "LT",
    "luxembourg": "LU",
    "malta": "MT",
    "netherlands": "NL",
    "nederland": "NL",
    "niederlande": "NL",
    "poland": "PL",
    "polska": "PL",
    "polen": "PL",
    "portugal": "PT",
    "romania": "RO",
    "slovakia": "SK",
    "slovenia": "SI",
    "spain": "ES",
    "espana": "ES",
    "spanien": "ES",
    "sweden": "SE",
    "schweden": "SE",
    "united kingdom": "GB",
    "great britain": "GB",
    "england": "GB",
    "grossbritannien": "GB",
    "vereinigtes konigreich": "GB",
    "royaume uni": "GB",
    "regno unito": "GB",
    "switzerland": "CH",
    "schweiz": "CH",
    "suisse": "CH",
    "norway": "NO",
    "norwegen": "NO",
    "united states": "US",
    "usa": "US",
    "canada": "CA",
    "china": "CN",
    "japan": "JP",
    "hong kong": "HK",
    "australia": "AU",
    "singapore": "SG",
    "india": "IN",
    "malaysia": "MY",
    "philippines": "PH",
    "taiwan": "TW",
}


def normalize_country(value: str | None) -> str | None:
    """Normalize common localized country labels to ISO-3166 alpha-2."""
    if not value:
        return None
    text = value.strip().upper()
    if len(text) == 2 and text.isalpha():
        return text
    if len(text) == 3 and text.isalpha():
        return {
            "DEU": "DE",
            "FRA": "FR",
            "GBR": "GB",
            "LTU": "LT",
            "USA": "US",
            "CAN": "CA",
            "POL": "PL",
            "ITA": "IT",
            "ESP": "ES",
            "NLD": "NL",
        }.get(text)
    key = _key(value)
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    for alias, code in sorted(_COUNTRY_ALIASES.items(), key=lambda pair: -len(pair[0])):
        if re.search(rf"\b{re.escape(alias)}\b", key):
            return code
    for locale_code in ("en", "de", "fr", "it", "es", "nl", "pl", "zh"):
        try:
            territories = Locale.parse(locale_code).territories
        except (ValueError, KeyError):
            continue
        for code, label in territories.items():
            if _key(label) == key and len(code) == 2:
                return code
    return None


def origin_zone(country: str | None) -> str | None:
    if not country:
        return None
    if country in EU_COUNTRIES:
        return "EU"
    return "NON_EU"


def resolve_marketplaces(
    codes: tuple[str, ...] | list[str] | None = None
) -> list[Marketplace]:
    selected = {str(value).upper() for value in (codes or ())}
    if not selected:
        return list(MARKETPLACES)
    result: list[Marketplace] = []
    for marketplace in MARKETPLACES:
        if marketplace.code in selected or marketplace.country in selected:
            result.append(marketplace)
    return result
