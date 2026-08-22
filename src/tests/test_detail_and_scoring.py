from datetime import UTC, datetime, timedelta
from decimal import Decimal

from dealsteal.detail import parse_detail_html
from dealsteal.models import (
    Destination,
    Listing,
    Money,
    PriceTier,
    ProductProfile,
    ScannerConfig,
)
from dealsteal.scoring import DealScorer, ExchangeRates

DETAIL_FIXTURE = r'''<script>
{"buyingFormat":"AUCTION","currentPrice":{"amount":"125.50","currency":"EUR"},
 "endTime":{"value":"2030-04-05T11:22:33.000Z"},
 "itemLocation":{"country":"DE"},
 "shipToLocation":{"country":"LT","postalCode":"01100"},
 "shippingCost":{"amount":"6,99","currency":"EUR","shipToLocations":["LTU"]}}
</script>'''

MODERN_BIN_FIXTURE = r'''<script>
{"BUY_BOX":{"_type":"BuyBoxModule","binModel":{"price":{"value":{"value":550,"currency":"EUR"}}}},
 "endTime":{"value":"2030-04-05T11:22:33.000Z"},
 "related":{"currentPrice":{"amount":"5.50","currency":"EUR"},"buyingFormat":"AUCTION"},
 "shippingCost":{"original":{"amount":5.50,"currency":"EUR"},"shipToLocations":["ITA"]}}
</script><span>Oggetto che si trova a: Cecchignola, Italia</span>'''


def test_detail_parser_uses_absolute_time_and_destination_shipping() -> None:
    parsed = parse_detail_html(DETAIL_FIXTURE, "LT")

    assert parsed.listing_type == "auction"
    assert parsed.price == Money(Decimal("125.50"), "EUR")
    assert parsed.end_time == datetime(2030, 4, 5, 11, 22, 33, tzinfo=UTC)
    assert parsed.origin_country == "DE"
    assert parsed.ship_to_country == "LT"
    assert parsed.ship_to_postal_code == "01100"
    assert parsed.shipping == Money(Decimal("6.99"), "EUR")
    assert parsed.shipping_known


def test_detail_parser_reads_json_ld_shipping_outside_buy_box() -> None:
    html = r'''<script type="application/ld+json">
    {"offers":{"priceCurrency":"EUR","price":"271",
      "shippingDetails":[{"shippingRate":{"value":"14.99","currency":"EUR"},
      "shippingDestination":{"addressCountry":"LTU"}}]}}
    </script>'''

    parsed = parse_detail_html(html, "LT", "01100")

    assert parsed.shipping == Money(Decimal("14.99"), "EUR")
    assert parsed.shipping_known
    assert parsed.ship_to_country == "LT"
    assert parsed.ship_to_postal_code == "01100"


def test_countdown_without_absolute_timestamp_is_not_promoted_to_end_time() -> None:
    parsed = parse_detail_html('<div class="time-left">Heute 22:05</div>', "LT")

    assert parsed.end_time is None


def test_modern_item_page_uses_primary_buy_box_not_related_listing() -> None:
    parsed = parse_detail_html(MODERN_BIN_FIXTURE, "LT")

    assert parsed.listing_type == "buy_it_now"
    assert parsed.price == Money(Decimal("550"), "EUR")
    assert parsed.origin_country == "IT"
    assert parsed.end_time == datetime(2030, 4, 5, 11, 22, 33, tzinfo=UTC)
    assert not parsed.shipping_known


def test_max_bid_solves_fx_buffer_for_the_new_bid() -> None:
    config = ScannerConfig(
        destination=Destination("LT", "01100"),
        minimum_net_roi=Decimal("0.40"),
        fx_buffer_rate=Decimal("0.02"),
    )
    listing = Listing(
        item_id="1",
        title="test",
        url="https://www.ebay.de/itm/1",
        marketplace="EBAY_DE",
        host="www.ebay.de",
        listing_type="auction",
        price=Money(Decimal("100"), "EUR"),
        shipping=Money(Decimal("10"), "EUR"),
        origin_country="DE",
        origin_zone="EU",
        ship_to_country="LT",
        ship_to_postal_code="01100",
        end_time=datetime.now(UTC) + timedelta(hours=2),
        end_time_source="item_page",
        detail_verified=True,
        shipping_known=True,
        import_cost_known=True,
        listing_type_verified=True,
    )
    profile = ProductProfile(
        profile_id="p",
        search_terms=("test",),
        tiers=(PriceTier("tier", Money(Decimal("280"), "EUR")),),
    )

    result = DealScorer(
        config, ExchangeRates({"EUR": Decimal("1")}, "2030-01-01")
    ).score(listing, profile, profile.tiers[0])

    assert result.qualified
    assert result.max_bid == Money(Decimal("186.27"), "EUR")


def test_auction_without_item_page_timestamp_cannot_qualify() -> None:
    config = ScannerConfig(destination=Destination("LT", "01100"))
    listing = Listing(
        item_id="2",
        title="test",
        url="https://www.ebay.de/itm/2",
        marketplace="EBAY_DE",
        host="www.ebay.de",
        listing_type="auction",
        price=Money(Decimal("10"), "EUR"),
        shipping=Money(Decimal("1"), "EUR"),
        origin_country="DE",
        origin_zone="EU",
        ship_to_country="LT",
        ship_to_postal_code="01100",
        detail_verified=True,
        shipping_known=True,
        import_cost_known=True,
        listing_type_verified=True,
    )
    profile = ProductProfile(
        profile_id="p",
        search_terms=("test",),
        max_time_remaining_seconds=86400,
        tiers=(PriceTier("tier", Money(Decimal("100"), "EUR")),),
    )

    result = DealScorer(config).score(listing, profile, profile.tiers[0])

    assert not result.qualified
    assert "absolute_end_time_unknown" in result.reasons
