from __future__ import annotations

from typing import Any

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
    assert session.calls[0][2]["LH_Auction"] == "1"


def test_challenge_response_is_not_treated_as_empty_search() -> None:
    response = _response(
        "<title>Pardon Our Interruption...</title>",
        url="https://www.ebay.com/splashui/challenge",
    )

    assert EbayAuctionSearcher._is_challenge(response)
