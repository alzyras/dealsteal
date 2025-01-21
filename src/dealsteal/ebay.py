import logging
import os
from datetime import datetime, timedelta, timezone

import requests

import dealsteal  # noqa

LOGGER = logging.getLogger(__name__)


class EbayAuctionSearcher:
    EBAY_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
    EUROPEAN_COUNTRIES = [
        "AL",
        "AD",
        "AT",
        "BY",
        "BE",
        "BA",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IS",
        "IE",
        "IT",
        "LV",
        "LI",
        "LT",
        "LU",
        "MT",
        "MC",
        "ME",
        "NL",
        "MK",
        "NO",
        "PL",
        "PT",
        "RO",
        "SM",
        "RS",
        "SK",
        "SI",
        "ES",
        "SE",
        "CH",
        "UA",
        "GB",
        "VA",
    ]

    def __init__(self, oauth_token, app_id):
        """
        Initializes the eBay API client with the provided OAuth token and application ID.

        Args:
            oauth_token (str): The OAuth token for authenticating API requests.
            app_id (str): The application ID for the eBay API.
        """
        self.oauth_token = oauth_token
        self.app_id = app_id

    def search_ebay_auctions(
        self,
        keywords,
        countries=None,
        max_price=None,
        min_price=None,
        max_time_remaining=None,
        categories=None,
        category_ids=None,
        condition_ids=None,
    ):
        """Search eBay auctions based on specified criteria.

        Args:
            keywords (str): Keywords to search for in the auction titles.
            countries (list, optional): List of country codes to search within. Defaults to European countries.
            max_price (float, optional): Maximum price of the items to search for. Defaults to None.
            min_price (float, optional): Minimum price of the items to search for. Defaults to None.
            max_time_remaining (str, optional): Maximum time remaining for the auction. Defaults to None.
            categories (list, optional): List of category names to search within. Defaults to None.
            category_ids (list, optional): List of category IDs to search within. Defaults to None.
            condition_ids (list, optional): List of condition IDs to filter the items. Defaults to None.

        Returns:
            list: List of filtered auction items based on the search criteria.
        """
        countries = countries or self.EUROPEAN_COUNTRIES
        results = []

        for country in countries:
            headers = self._build_headers()
            payload = self._build_payload(
                keywords,
                country,
                max_price,
                min_price,
                categories,
                category_ids,
                condition_ids,
            )
            response = self._make_request(headers, payload)

            if response:
                items = self._extract_items(response)
                filtered_items = self._filter_items_by_time(items, max_time_remaining)
                results.extend(filtered_items)

        return results

    def _build_headers(self):
        """Build the headers required for making requests to the eBay API.

        Returns:
            dict: A dictionary containing the necessary headers for the eBay API request.
        """
        return {
            "Authorization": f"Bearer {self.oauth_token}",
            "X-EBAY-SOA-SECURITY-APPNAME": self.app_id,
            "X-EBAY-SOA-OPERATION-NAME": "findItemsAdvanced",
            "X-EBAY-SOA-REQUEST-DATA-FORMAT": "JSON",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        keywords,
        country,
        max_price,
        min_price,
        categories,
        category_ids,
        condition_ids,
    ):
        item_filters = [
            {"name": "ListingType", "value": "Auction"},
            {"name": "LocatedIn", "value": country},
        ]

        if max_price:
            item_filters.append({"name": "MaxPrice", "value": str(max_price)})

        if min_price:
            item_filters.append({"name": "MinPrice", "value": str(min_price)})

        if categories:
            payload["categoryId"] = categories

        if category_ids:
            item_filters.append({"name": "CategoryId", "value": category_ids})

        if condition_ids:
            item_filters.append({"name": "Condition", "value": condition_ids})

        return {
            "keywords": keywords,
            "paginationInput": {"entriesPerPage": 50},
            "itemFilter": item_filters,
        }

    def _make_request(self, headers, payload):
        try:
            response = requests.post(self.EBAY_API_URL, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as error:
            LOGGER.error(f"Error: {error}")
            return None

    def _extract_items(self, data):
        response_data = data.get("findItemsAdvancedResponse", [])
        if not response_data:
            return []

        search_result = response_data[0].get("searchResult", [{}])[0]
        return search_result.get("item", [])

    def _filter_items_by_time(self, items, max_time_remaining):
        filtered_items = []
        for item in items:
            end_time = item.get("listingInfo", [{}])[0].get("endTime", "")
            end_datetime = self._parse_end_time(end_time)
            time_remaining = end_datetime - datetime.now(timezone.utc)

            if max_time_remaining and time_remaining > timedelta(
                seconds=max_time_remaining
            ):
                continue

            filtered_items.append(self._format_item(item, time_remaining))

        return filtered_items

    def _parse_end_time(self, end_time: str) -> datetime:
        if isinstance(end_time, list):
            end_time = end_time[0]

        try:
            return datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%S.000Z").replace(
                tzinfo=timezone.utc
            )

    def _format_item(self, item, time_remaining):
        return {
            "country": item.get("country", "Unknown")[0],
            "title": item.get("title", "No title")[0],
            "price": f"{item.get('sellingStatus', [{}])[0].get('currentPrice', [{}])[0].get('__value__', '0')} {item.get('sellingStatus', [{}])[0].get('currentPrice', [{}])[0].get('@currencyId', 'USD')}",
            "time_remaining": str(time_remaining),
            "url": item.get("viewItemURL", "No URL available")[0],
            "category": item.get("primaryCategory", [{}])[0].get(
                "categoryName", "Unknown"
            )[0],
            "category_id": item.get("primaryCategory", [{}])[0].get(
                "categoryId", "Unknown"
            ),
            "item_id": item.get("itemId", "Unknown")[0],
            "condition_id": item.get("condition", [{}])[0].get(
                "conditionId", "Unknown"
            )[0],
            "condition_display_name": item.get("condition", [{}])[0].get(
                "conditionDisplayName", "Unknown"
            )[0],
            "listing_type": item.get("listingInfo", [{}])[0].get(
                "listingType", "Unknown"
            )[0],
            "start_time": item.get("listingInfo", [{}])[0].get("startTime", "Unknown"),
            "end_time": item.get("listingInfo", [{}])[0].get("endTime", "Unknown")[0],
            "seller_user_id": item.get("sellerInfo", [{}])[0].get(
                "sellerUserName", "Unknown"
            ),
            "feedback_score": item.get("sellerInfo", [{}])[0].get(
                "feedbackScore", "Unknown"
            ),
            "feedback_percentage": item.get("sellerInfo", [{}])[0].get(
                "positiveFeedbackPercent", "Unknown"
            ),
            "shipping_cost": f"{item.get('shippingInfo', [{}])[0].get('shippingServiceCost', [{}])[0].get('__value__', '0')} {item.get('shippingInfo', [{}])[0].get('shippingServiceCost', [{}])[0].get('@currencyId', 'USD')}",
            "location": item.get("location", "Unknown")[0],
            "gallery_url": item.get("galleryURL", "No URL available"),
        }


# Example usage
if __name__ == "__main__":
    EBAY_OAUTH_TOKEN = os.getenv("EBAY_OAUTH_TOKEN")
    EBAY_APP_ID = os.getenv("EBAY_APP_ID")

    searcher = EbayAuctionSearcher(EBAY_OAUTH_TOKEN, EBAY_APP_ID)
    auctions = searcher.search_ebay_auctions(
        keywords="gopro -3", max_price=500, min_price=50, max_time_remaining=21600
    )

    for auction in auctions:
        LOGGER.info(auction)
