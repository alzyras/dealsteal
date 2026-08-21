from decimal import Decimal

from dealsteal.ebay import EbayAuctionSearcher
from dealsteal.locales import normalize_country, resolve_marketplaces
from dealsteal.models import Money
from dealsteal.scanner import parse_card_price


def test_all_configured_marketplace_locales_resolve() -> None:
    marketplaces = resolve_marketplaces()
    assert len(marketplaces) >= 20
    assert {item.code for item in marketplaces} >= {
        "EBAY_DE",
        "EBAY_BE_NL",
        "EBAY_BE_FR",
    }


def test_localized_country_names_normalize_to_iso() -> None:
    assert normalize_country("Deutschland") == "DE"
    assert normalize_country("Lietuva") == "LT"
    assert normalize_country("Polska") == "PL"
    assert normalize_country("Österreich") == "AT"


def test_localized_card_amounts_are_decimal_safe() -> None:
    assert parse_card_price("1.234,56 €", "EUR") == Money(Decimal("1234.56"), "EUR")
    assert parse_card_price("1 234,56 PLN", "PLN") == Money(Decimal("1234.56"), "PLN")
    assert parse_card_price("1,234.56 USD", "USD") == Money(Decimal("1234.56"), "USD")


def test_legacy_searcher_keeps_public_credential_free_mode() -> None:
    ok, message = EbayAuctionSearcher().check_api_status()
    assert ok
    assert "no API token" in message
