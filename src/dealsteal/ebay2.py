import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
import logging
logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class EbayAuctionSearcher:
    EBAY_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
    EUROPEAN_COUNTRIES = [
        "AL", "AD", "AT", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE",
        "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU",
        "MT", "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "SM", "RS", "SK",
        "SI", "ES", "SE", "CH", "UA", "GB", "VA"
    ]

    def __init__(self, app_id):
        """
        Initializes the eBay API client with the provided application ID.

        Args:
            app_id (str): The application ID for the eBay API.
        """
        self.app_id = app_id

    def search_ebay_auctions(
        self,
        keywords,
        countries=None,
        max_price=None,
        min_price=None,
        max_time_remaining=None,
        category_ids=None,
        condition_ids=None,
    ):
        """
        Search eBay auctions based on specified criteria.

        Args:
            keywords (str): Keywords to search for in the auction titles.
            countries (list, optional): List of country codes to search within. Defaults to European countries.
            max_price (float, optional): Maximum price of the items to search for. Defaults to None.
            min_price (float, optional): Minimum price of the items to search for. Defaults to None.
            max_time_remaining (int, optional): Maximum time remaining for the auction in seconds. Defaults to None.
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
        """
        Build the headers required for making requests to the eBay API.

        Returns:
            dict: A dictionary containing the necessary headers for the eBay API request.
        """
        return {
            "X-EBAY-SOA-SECURITY-APPNAME": self.app_id,
            "X-EBAY-SOA-OPERATION-NAME": "findItemsAdvanced",
            "X-EBAY-SOA-REQUEST-DATA-FORMAT": "JSON",
            "X-EBAY-SOA-RESPONSE-DATA-FORMAT": "JSON",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        keywords,
        country,
        max_price,
        min_price,
        category_ids,
        condition_ids,
    ):
        item_filters = [
            {"name": "ListingType", "value": "Auction"},
            {"name": "LocatedIn", "value": country},
        ]

        if max_price is not None:
            item_filters.append({"name": "MaxPrice", "value": str(max_price)})

        if min_price is not None:
            item_filters.append({"name": "MinPrice", "value": str(min_price)})

        if category_ids:
            item_filters.append({"name": "CategoryId", "value": category_ids})

        if condition_ids:
            item_filters.append({"name": "Condition", "value": condition_ids})

        return {
            "keywords": keywords,
            "paginationInput": {"entriesPerPage": 50},
            "itemFilter": item_filters,
        }

    def _make_request(self, headers, payload, retries=3):
        for attempt in range(retries):
            try:
                response = requests.post(self.EBAY_API_URL, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as error:
                LOGGER.error(f"Attempt {attempt + 1}: Error: {error}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
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
            listing_info = item.get("listingInfo", [{}])[0]
            end_time = listing_info.get("endTime", "")
            end_datetime = self._parse_end_time(end_time)
            time_remaining = end_datetime - datetime.now(timezone.utc)

            if max_time_remaining and time_remaining.total_seconds() > max_time_remaining:
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
        selling_status = item.get("sellingStatus", [{}])[0]
        current_price = selling_status.get("currentPrice", [{}])[0]
        shipping_info = item.get("shippingInfo", [{}])[0]
        shipping_cost = shipping_info.get("shippingServiceCost", [{}])[0]

        return {
            "country": item.get("country", ["Unknown"])[0],
            "title": item.get("title", ["No title"])[0],
            "price": f"{current_price.get('__value__', '0')} {current_price.get('@currencyId', 'USD')}",
            "time_remaining": str(time_remaining),
            "url": item.get("viewItemURL", ["No URL available"])[0],
            "category": item.get("primaryCategory", [{}])[0].get("categoryName", ["Unknown"])[0],
            "category_id": item.get("primaryCategory", [{}])[0].get("categoryId", ["Unknown"])[0],
            "item_id": item.get("itemId", ["Unknown"])[0],
            "condition_id": item.get("condition", [{}])[0].get("conditionId", ["Unknown"])[0],
            "condition_display_name": item.get("condition", [{}])[0].get("conditionDisplayName", ["Unknown"])[0],
            "listing_type": item.get("listingInfo", [{}])[0].get("listingType", ["Unknown"])[0],
            "start_time": item.get("listingInfo", [{}])[0].get("startTime", ["Unknown"])[0],
            "end_time": item.get("listingInfo", [{}])[0].get("endTime", ["Unknown"])[0],
            "seller_user_id": item.get("sellerInfo", [{}])[0].get("sellerUserName", ["Unknown"])[0],
            "feedback_score": item.get("sellerInfo", [{}])[0].get("feedbackScore", ["Unknown"])[0],
            "feedback_percentage": item.get("sellerInfo", [{}])[0].get("positiveFeedbackPercent", ["Unknown"])[0],
            "shipping_cost": f"{shipping_cost.get('__value__', '0')} {shipping_cost.get('@currencyId', 'USD')}",
            "location": item.get("location", ["Unknown"])[0],
            "gallery_url": item.get("galleryURL", ["No URL available"])[0],
        }


# Example usage
if __name__ == "__main__":
    EBAY_APP_ID = os.getenv("EBAY_APP_ID")

    if not EBAY_APP_ID:
        LOGGER.error("EBAY_APP_ID environment variable is not set.")
    else:
        searcher = EbayAuctionSearcher(EBAY_APP_ID)
        auctions = searcher.search_ebay_auctions(
            keywords="gopro -3",
            max_price=500,
            min_price=50,
            max_time_remaining=21600,  # 6 hours in seconds
            countries=["DE"]  # Limiting to Germany for testing
        )

        for auction in auctions:
            LOGGER.info(auction)
