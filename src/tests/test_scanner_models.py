from datetime import UTC
from decimal import Decimal
from pathlib import Path

from dealsteal.config import config_from_dict, load_config, profile_from_dict
from dealsteal.detail import parse_detail_html
from dealsteal.locales import normalize_country, resolve_marketplaces
from dealsteal.matching import explain_match, matching_tier
from dealsteal.models import Listing, Money
from dealsteal.scanner import (
    FetchedPage,
    MarketplaceScanner,
    RateLimitedHttpClient,
    ScanStats,
    parse_card_price,
)
from dealsteal.scoring import DealScorer, ExchangeRates

DETAIL_HTML = r'''
<script>
{"buyingFormat":"AUCTION","currentPrice":{"amount":100,"currency":"EUR"},
"endTime":{"endTime":{"value":"2030-08-22T12:20:54.000Z"}},
"itemLocation":{"city":"Berlin","country":"DE"},
"shipToLocation":{"country":"LT","postalCode":"01100"},
"shippingCost":{"amount":10.49,"currency":"EUR"},"shipToLocations":["DEU"],
"shippingCost":{"amount":40,"currency":"EUR"},"shipToLocations":["_EU"],
"importCharges":{"amount":0,"currency":"EUR"}}
</script>
'''


def test_detail_parser_prefers_absolute_time_and_destination_fields() -> None:
    detail = parse_detail_html(DETAIL_HTML, "LT")

    assert detail.listing_type == "auction"
    assert detail.price == Money(Decimal("100"), "EUR")
    assert detail.shipping == Money(Decimal("40"), "EUR")
    assert detail.shipping_known is True
    assert detail.import_cost_known is True
    assert detail.origin_country == "DE"
    assert detail.ship_to_country == "LT"
    assert detail.ship_to_postal_code == "01100"
    assert detail.end_time is not None
    assert detail.end_time.tzinfo == UTC


def test_locale_registry_and_country_normalization_cover_non_english_labels() -> None:
    assert normalize_country("Frankreich") == "FR"
    assert normalize_country("Vereinigtes Königreich") == "GB"
    assert {site.code for site in resolve_marketplaces(("BE",))} == {
        "EBAY_BE_NL",
        "EBAY_BE_FR",
    }
    assert len(resolve_marketplaces()) >= 20


def test_matching_requires_one_unambiguous_price_tier() -> None:
    profile = profile_from_dict(
        {
            "id": "m920s",
            "search_terms": ["ThinkCentre"],
            "required": [["m920s"], ["i7-8700", "i7 8700"]],
            "excluded": ["micro", "parts"],
            "tiers": [
                {"id": "used", "reference_price": {"amount": 300, "currency": "EUR"}},
            ],
        }
    )

    assert matching_tier(profile, "Lenovo M920s SFF i7-8700", "Used") is not None
    assert (
        explain_match(profile, "Lenovo M920s Micro i7-8700", "Used")[1]
        == "excluded_term_present"
    )


def test_net_roi_and_auction_max_bid_include_shipping_and_fx_buffer() -> None:
    config = config_from_dict(
        {
            "destination": {"country": "LT", "postal_code": "01100"},
            "minimum_net_roi": 0.40,
            "costs": {"fx_buffer_rate": 0},
        }
    )
    profile = profile_from_dict(
        {
            "id": "server",
            "search_terms": ["server"],
            "tiers": [
                {"id": "used", "reference_price": {"amount": 300, "currency": "EUR"}}
            ],
        }
    )
    listing = Listing(
        item_id="1",
        title="Good server",
        url="https://www.ebay.de/itm/1",
        marketplace="EBAY_DE",
        host="www.ebay.de",
        listing_type="auction",
        price=Money(Decimal("100"), "EUR"),
        shipping=Money(Decimal("20"), "EUR"),
        origin_country="DE",
        origin_zone="EU",
        ship_to_country="LT",
        ship_to_postal_code="01100",
        import_cost_known=True,
        shipping_known=True,
        detail_verified=True,
        listing_type_verified=True,
    )

    result = DealScorer(config, ExchangeRates({"EUR": Decimal("1")})).score(
        listing, profile, profile.tiers[0]
    )
    assert result.qualified is True
    assert result.net_profit.amount == Decimal("180.00")
    assert result.max_bid is not None
    assert result.max_bid.amount == Decimal("194.29")


def test_unknown_shipping_cannot_be_a_deal() -> None:
    config = config_from_dict(
        {"destination": {"country": "LT", "postal_code": "01100"}}
    )
    profile = profile_from_dict(
        {"id": "pc", "search_terms": ["pc"], "tiers": [{"reference_price": 300}]}
    )
    listing = Listing(
        item_id="1",
        title="pc",
        url="https://www.ebay.de/itm/1",
        marketplace="EBAY_DE",
        host="www.ebay.de",
        listing_type="buy_it_now",
        price=Money(Decimal("100"), "EUR"),
        import_cost_known=True,
        detail_verified=True,
        listing_type_verified=True,
        ship_to_country="LT",
    )
    result = DealScorer(config).score(listing, profile, profile.tiers[0])
    assert result.qualified is False
    assert "destination_shipping_unknown" in result.reasons


def test_config_time_window_applies_when_profile_does_not_override_it() -> None:
    config = config_from_dict(
        {
            "destination": {"country": "LT", "postal_code": "01100"},
            "max_time_remaining_hours": 10,
        }
    )
    assert config.max_time_remaining_seconds == 10 * 60 * 60


def test_load_config_uses_example_when_default_local_config_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.example.json").write_text(
        '{"destination":{"country":"LT","postal_code":"01100"},'
        '"max_time_remaining_hours":10,"marketplaces":["DE"]}',
        encoding="utf-8",
    )

    config = load_config()

    assert config.destination.country == "LT"
    assert config.marketplaces == ("DE",)
    assert config.max_time_remaining_seconds == 10 * 60 * 60


def test_load_config_uses_example_for_missing_explicit_local_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.example.json").write_text(
        '{"destination":{"country":"LT","postal_code":"01100"},'
        '"marketplaces":["DE"],"request_budget":null}',
        encoding="utf-8",
    )

    config = load_config("config.local.json")

    assert config.marketplaces == ("DE",)
    assert config.request_budget is None


def test_host_circuit_opens_only_after_repeated_challenge() -> None:
    config = config_from_dict(
        {
            "destination": {"country": "LT", "postal_code": "01100"},
            "database_path": ":memory:",
        }
    )
    client = RateLimitedHttpClient(config)

    client._trip("www.ebay.de", challenge=True)
    assert "www.ebay.de" not in client._cooldown_until
    assert client.stats.challenges == 1

    client._trip("www.ebay.de", challenge=True)
    assert client._cooldown_until["www.ebay.de"] > 0
    assert client.stats.challenges == 2


def test_card_price_handles_european_separators() -> None:
    assert parse_card_price("EUR 1.234,56", "EUR") == Money(Decimal("1234.56"), "EUR")
    assert parse_card_price("$1,234.56", "USD") == Money(Decimal("1234.56"), "USD")


def test_scanner_enriches_a_candidate_before_scoring() -> None:
    search_html = """
    <li class="s-card" data-listingid="123">
      <a class="s-card__link" href="https://www.ebay.de/itm/123">
        <div class="s-card__title">Lenovo M920s SFF i7-8700</div>
      </a>
      <span class="s-card__price">EUR 100,00</span>
      <div class="s-card__attribute-row">from Germany</div>
    </li>
    """
    fake_detail = DETAIL_HTML.replace("AUCTION", "FIXED_PRICE")

    class FakeHttp:
        def __init__(self) -> None:
            self.stats = ScanStats()

        def get(self, marketplace, url, params=None, cache_seconds=0):
            del marketplace, params, cache_seconds
            self.stats.requested += 1
            body = search_html if "/sch/" in url else fake_detail
            return FetchedPage(url, 200, body.encode(), {})

    config = config_from_dict(
        {
            "destination": {"country": "LT", "postal_code": "01100"},
            "marketplaces": ["DE"],
            "database_path": ":memory:",
            "per_host_interval": 0,
        }
    )
    profile = profile_from_dict(
        {
            "id": "m920s",
            "search_terms": ["M920s"],
            "listing_types": ["buy_it_now"],
            "required": [["m920s"], ["i7-8700"]],
            "tiers": [
                {"id": "used", "reference_price": {"amount": 300, "currency": "EUR"}}
            ],
        }
    )
    scanner = MarketplaceScanner(config, rates=ExchangeRates({"EUR": Decimal("1")}))
    scanner.http = FakeHttp()
    result = scanner.scan([profile])

    assert result["stats"]["candidates"] == 1
    assert result["stats"]["enriched"] == 1
    assert result["stats"]["qualified"] == 1
    assert result["deals"][0]["listing"]["detail_verified"] is True
    scanner.store.close()
