import os

from dealsteal.ebay import EbayAuctionSearcher


def test_ebay_access():
    """Test if eBay API access and auction search functionality work."""
    # Retrieve environment variables
    ebay_api_key = os.getenv("EBAY_API_KEY")
    ebay_app_id = os.getenv("EBAY_APP_ID")
    ebay_oauth_token = os.getenv("EBAY_OAUTH_TOKEN")

    # Validate required environment variables
    assert ebay_api_key is not None, "EBAY_API_KEY is not set."
    assert ebay_app_id is not None, "EBAY_APP_ID is not set."
    assert ebay_oauth_token is not None, "EBAY_OAUTH_TOKEN is not set."

    # Create an instance of EbayAuctionSearcher
    searcher = EbayAuctionSearcher(ebay_oauth_token, ebay_app_id)

    # Define search parameters
    keywords = "iphone"
    max_price = 1500
    min_price = 50
    max_time_remaining = 456_000

    # Perform a search
    auctions = searcher.search_ebay_auctions(
        keywords,
        max_price=max_price,
        min_price=min_price,
        max_time_remaining=max_time_remaining,
    )

    # Validate search results
    assert auctions is not None, "Search returned None."
    assert len(auctions) > 0, "No auctions found."
