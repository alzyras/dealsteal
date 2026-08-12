from dealsteal.ebay import EbayAuctionSearcher


def test_searcher_does_not_require_ebay_credentials() -> None:
    searcher = EbayAuctionSearcher(min_request_interval=0)

    assert searcher.check_api_status() == (
        True,
        "Public eBay search mode; no API token or login required",
    )


def test_public_search_parameters_preserve_query_json_fields() -> None:
    searcher = EbayAuctionSearcher(min_request_interval=0)

    params = searcher._build_params(
        "gopro hero 11",
        "GB",
        max_price=200,
        min_price=100,
        category_ids=["123"],
        condition_ids=["1000", "3000"],
    )

    assert params == {
        "_nkw": "gopro hero 11",
        "_ipg": "120",
        "_pgn": "1",
        "_sacat": "123",
        "LH_Auction": "1",
        "_sop": "1",
        "_udlo": "100",
        "_udhi": "200",
        "LH_ItemCondition": "1000|3000",
    }
