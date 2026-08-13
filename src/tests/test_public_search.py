from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import requests

from dealsteal.ebay import EbayAuctionSearcher

SEARCH_HTML = """
<ul class="srp-results">
  <li class="s-card" data-listingid="123">
    <a class="s-card__link" href="https://www.ebay.co.uk/itm/123">
      <div role="heading" class="s-card__title">
        <span>GoPro Hero 11 Black</span>
        <span class="clipped">Opens in a new window</span>
      </div>
    </a>
    <div class="s-card__subtitle">Pre-Owned</div>
    <span class="s-card__price">€150.00</span>
    <span class="s-card__time-left">2h 5m left</span>
    <span class="s-card__time-end">(Today)</span>
    <div class="s-card__attribute-row">1 bid</div>
    <div class="s-card__attribute-row">+€5.00 delivery</div>
    <div class="s-card__attribute-row">from Germany</div>
  </li>
  <li class="s-card" data-listingid="456">
    <a class="s-card__link" href="https://www.ebay.co.uk/itm/456">
      <div role="heading" class="s-card__title">Shop on eBay</div>
    </a>
    <span class="s-card__price">£20.00</span>
  </li>
</ul>
"""


def _response(
    body: str, status: int = 200, url: str = "https://www.ebay.co.uk/sch/i.html"
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = body.encode()
    response.headers["Content-Type"] = "text/html"
    return response


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        timeout: float,
    ) -> requests.Response:
        del timeout
        self.calls.append((method, url, params))
        if "ebayadvsearch" in url:
            return _response("<title>Advanced Search | eBay</title>", url=url)
        return _response(SEARCH_HTML, url=url)


def test_parser_keeps_auction_cards_and_ignores_non_auctions() -> None:
    searcher = EbayAuctionSearcher(min_request_interval=0)

    items = searcher._extract_items(SEARCH_HTML)

    assert len(items) == 2
    assert items[0]["title"] == "GoPro Hero 11 Black"
    assert items[0]["time_left"] == "2h 5m left"
    assert items[0]["condition_text"] == "Pre-Owned"
    assert items[1]["time_left"] == ""


def test_search_uses_public_html_and_normalizes_auction_json() -> None:
    session = FakeSession()
    searcher = EbayAuctionSearcher(
        session=session,
        min_request_interval=0,
        max_pages=1,
        page_size=120,
    )

    results = searcher.search_ebay_auctions(
        "gopro",
        countries=["DE"],
        min_price=100,
        max_price=200,
        max_time_remaining=3 * 3600,
    )

    assert len(results) == 1
    assert results[0]["item_id"] == "123"
    assert results[0]["price"] == "150.00 EUR"
    assert results[0]["listing_type"] == "Auction"
    assert results[0]["location"] == "Germany"
    assert results[0]["bid_count"] == 1
    assert results[0]["condition_display_name"] == "Pre-Owned"
    assert results[0]["origin_region"] == "EU"
    assert results[0]["import_cost_known"] is True
    assert results[0]["import_duty"] == "0.00 EUR"
    assert results[0]["import_vat"] == "0.00 EUR"
    assert results[0]["origin_country_source"] == "listing"
    assert session.calls[0][2]["LH_Auction"] == "1"


def test_buy_it_now_search_is_separate_and_includes_landed_shipping() -> None:
    session = FakeSession()
    searcher = EbayAuctionSearcher(
        session=session,
        min_request_interval=0,
        max_pages=1,
        page_size=120,
    )

    results = searcher.search_ebay_buy_it_now("gopro", countries=["DE"])

    result = next(item for item in results if item["item_id"] == "123")
    assert result["listing_type"] == "Buy It Now"
    assert result["time_remaining"] == "2:05:00"
    assert result["end_time"] != "Unknown"
    assert result["shipping_cost_value"] == 5.0
    assert result["shipping_currency"] == "EUR"
    assert result["shipping_known"] is True
    assert result["landed_price"] == "155.00 EUR"
    assert result["origin_country_source"] == "listing"
    assert session.calls[0][2]["LH_BIN"] == "1"
    assert "LH_Auction" not in session.calls[0][2]


def test_non_eu_sites_are_skipped() -> None:
    session = FakeSession()
    searcher = EbayAuctionSearcher(session=session, min_request_interval=0, max_pages=1)

    assert searcher.search_ebay_buy_it_now("server", countries=["GB", "CH"]) == []
    assert session.calls == []


def test_non_eu_origin_listing_is_skipped_even_on_eu_site() -> None:
    searcher = EbayAuctionSearcher(min_request_interval=0)

    item = searcher._format_public_item(
        {
            "item_id": "uk-origin",
            "title": "Imported PC",
            "price_text": "EUR 100,00",
            "time_left": "",
            "attribute_rows": ["+ EUR 10,00 delivery", "from United Kingdom"],
        },
        "DE",
        "www.ebay.de",
        listing_type="buy_it_now",
    )

    assert item is None


@pytest.mark.parametrize("location", ["Großbritannien", "Kanada", "United Kingdom"])
def test_localized_non_eu_origin_is_skipped(location: str) -> None:
    searcher = EbayAuctionSearcher(min_request_interval=0)

    item = searcher._format_public_item(
        {
            "item_id": "localized-origin",
            "title": "Imported PC",
            "price_text": "EUR 100,00",
            "time_left": "",
            "attribute_rows": [f"from {location}"],
        },
        "DE",
        "www.ebay.de",
        listing_type="buy_it_now",
    )

    assert item is None


def test_missing_shipping_is_unknown_not_free() -> None:
    searcher = EbayAuctionSearcher(min_request_interval=0)

    item = searcher._format_public_item(
        {
            "item_id": "no-shipping",
            "title": "No shipping shown",
            "price_text": "EUR 100,00",
            "time_left": "",
            "attribute_rows": ["from Germany"],
        },
        "DE",
        "www.ebay.de",
        listing_type="buy_it_now",
    )

    assert item is not None
    assert item["shipping_known"] is False
    assert item["shipping_cost"] == "Unknown"
    assert item["shipping_cost_value"] is None
    assert item["landed_price"] == "Unknown"


def test_missing_origin_country_is_marked_unknown_for_public_formatter() -> None:
    searcher = EbayAuctionSearcher(min_request_interval=0)

    item = searcher._format_public_item(
        {
            "item_id": "marketplace-origin",
            "title": "Mini PC",
            "price_text": "EUR 100,00",
            "time_left": "",
            "attribute_rows": ["+ EUR 10,00 delivery"],
        },
        "DE",
        "www.ebay.de",
        listing_type="buy_it_now",
    )

    assert item is not None
    assert item["origin_country"] == "Unknown"
    assert item["origin_country_source"] == "unknown"
    assert item["import_cost_known"] is False
    assert item["landed_price"] == "Unknown"


def test_challenge_response_is_not_treated_as_empty_search() -> None:
    response = _response(
        "<title>Pardon Our Interruption...</title>",
        url="https://www.ebay.com/splashui/challenge",
    )

    assert EbayAuctionSearcher._is_challenge(response)


def test_localized_time_and_listing_fields_are_normalized() -> None:
    searcher = EbayAuctionSearcher(min_request_interval=0)

    assert searcher._parse_time_left("Noch 1 Std 57 Min") == 7020
    assert searcher._parse_time_left("1 jour 2 heures 3 minutes") == 93780
    assert searcher._parse_time_left("6 days, 2:00:00 left") == (6 * 86400) + 7200
    assert searcher._time_string_to_seconds("6 days, 2:00:00") == (6 * 86400) + 7200
    assert searcher._parse_time_left("Nog 12u 44m") == (12 * 3600) + 44 * 60
    assert (
        searcher._parse_time_left("Nog 1 dag 12u 44m") == (86400 + 12 * 3600) + 44 * 60
    )
    assert not searcher._within_filters(
        {"price": "10.00 EUR", "time_remaining": "6 days, 2:00:00"},
        None,
        None,
        86400,
    )

    item = searcher._format_public_item(
        {
            "item_id": "789",
            "title": "MacBook Pro",
            "price_text": "EUR 77,98",
            "time_left": "Noch 1 Std 57 Min",
            "url": "https://www.ebay.de/itm/789",
            "attribute_rows": [
                "17 Gebote · Restzeit Noch 1 Std 57 Min",
                "+ EUR 16,37 Lieferung",
                "aus Deutschland",
                "seller 99,5% positiv (212)",
            ],
            "condition_text": "Gebraucht",
        },
        "DE",
        "www.ebay.de",
    )

    assert item is not None
    assert item["price"] == "77.98 EUR"
    assert item["location"] == "Deutschland"
    assert item["bid_count"] == 17
    assert item["shipping_cost"] == "+ EUR 16,37 Lieferung (EUR)"
    assert item["seller_user_id"] == "seller"
    assert item["feedback_score"] == "212"
    assert item["feedback_percentage"] == "99,5%"


@pytest.mark.parametrize(
    ("text", "expected_seconds"),
    [
        ("Ends in 2 days 3 hours 4 minutes 5 seconds", 183845),
        ("Noch 2 Tage 3 Std 4 Min 5 Sek", 183845),
        ("Encore 2 j 3 h 4 min 5 s", 183845),
        ("Restano 2g 3h 4m 5s", 183845),
        ("Quedan 2 días 3 h 4 min 5 s", 183845),
        ("Nog 2 dagen 3u 4m 5s", 183845),
        ("Pozostało 2 dni 3 godz. 4 min 5 sek.", 183845),
    ],
)
def test_all_supported_locale_countdown_units(text: str, expected_seconds: int) -> None:
    assert EbayAuctionSearcher._parse_time_left(text) == expected_seconds


@pytest.mark.parametrize(
    "time_left",
    [
        "Ends in 2 days 3 hours 4 minutes 5 seconds",
        "Noch 2 Tage 3 Std 4 Min 5 Sek",
        "Encore 2 j 3 h 4 min 5 s",
        "Restano 2g 3h 4m 5s",
        "Quedan 2 días 3 h 4 min 5 s",
        "Nog 2 dagen 3u 4m 5s",
        "Pozostało 2 dni 3 godz. 4 min 5 sek.",
    ],
)
def test_formatted_end_time_matches_countdown(time_left: str) -> None:
    before = datetime.now(timezone.utc)
    item = EbayAuctionSearcher(min_request_interval=0)._format_public_item(
        {
            "item_id": "date-check",
            "title": "Date check",
            "price_text": "EUR 1,00",
            "time_left": time_left,
        },
        "DE",
        "www.ebay.de",
    )
    after = datetime.now(timezone.utc)

    assert item is not None
    end_time = datetime.fromisoformat(item["end_time"].replace("Z", "+00:00"))
    expected = 183845
    assert (
        before + timedelta(seconds=expected, milliseconds=-1)
        <= end_time
        <= (after + timedelta(seconds=expected))
    )
