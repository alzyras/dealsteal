from __future__ import annotations

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
    <span class="s-card__price">£150.00</span>
    <span class="s-card__time-left">2h 5m left</span>
    <span class="s-card__time-end">(Today)</span>
    <div class="s-card__attribute-row">1 bid</div>
    <div class="s-card__attribute-row">+£5.00 delivery</div>
    <div class="s-card__attribute-row">from United Kingdom</div>
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
        countries=["GB"],
        min_price=100,
        max_price=200,
        max_time_remaining=3 * 3600,
    )

    assert len(results) == 1
    assert results[0]["item_id"] == "123"
    assert results[0]["price"] == "150.00 GBP"
    assert results[0]["listing_type"] == "Auction"
    assert results[0]["location"] == "United Kingdom"
    assert results[0]["bid_count"] == 1
    assert results[0]["condition_display_name"] == "Pre-Owned"
    assert session.calls[0][2]["LH_Auction"] == "1"


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
                "aus Vereinigte Staaten von Amerika",
                "seller 99,5% positiv (212)",
            ],
            "condition_text": "Gebraucht",
        },
        "DE",
        "www.ebay.de",
    )

    assert item is not None
    assert item["price"] == "77.98 EUR"
    assert item["location"] == "Vereinigte Staaten von Amerika"
    assert item["bid_count"] == 17
    assert item["shipping_cost"] == "+ EUR 16,37 Lieferung (EUR)"
    assert item["seller_user_id"] == "seller"
    assert item["feedback_score"] == "212"
    assert item["feedback_percentage"] == "99,5%"
