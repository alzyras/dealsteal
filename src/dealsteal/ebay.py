import logging
import os
import time
import base64
from datetime import datetime, timedelta, timezone
import requests

LOGGER = logging.getLogger(__name__)


class EbayAuctionSearcher:
    # Modern RESTful Browse API endpoint
    EBAY_API_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    # Token endpoint for OAuth
    EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
    
    EUROPEAN_COUNTRIES = [
        "GB",  # UK
        "DE",  # Germany
        "FR",  # France
        "IT",  # Italy
        "ES",  # Spain
    ]

    def __init__(self, app_id=None, cert_id=None):
        """
        Initializes the eBay API client with credentials for token generation.

        Args:
            app_id (str): The application ID for the eBay API.
            cert_id (str): The certificate ID for the eBay API.
        """
        self.app_id = app_id or os.getenv("EBAY_APP_ID")
        self.cert_id = cert_id or os.getenv("EBAY_CERT_ID")
        self.oauth_token = None
        self.token_expires_at = None
        
        # Ensure we have the required credentials
        if not self.app_id or not self.cert_id:
            raise ValueError("EBAY_APP_ID and EBAY_CERT_ID must be provided or set in environment variables")

    def _get_fresh_token(self):
        """Get a fresh OAuth token if needed (or if the current one is expired/expiring soon)."""
        # Check if we need a new token (expires within 5 minutes)
        if (not self.oauth_token or 
            not self.token_expires_at or 
            self.token_expires_at < datetime.now(timezone.utc) + timedelta(minutes=5)):
            
            LOGGER.info("Getting new eBay OAuth token...")
            self._refresh_token()
        return self.oauth_token

    def _refresh_token(self):
        """Refresh the OAuth token using app_id and cert_id."""
        # Encode credentials (Base64)
        basic_auth = base64.b64encode(f"{self.app_id}:{self.cert_id}".encode()).decode()

        # Token request
        url = self.EBAY_TOKEN_URL
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic_auth}"
        }
        data = {
            "grant_type": "client_credentials",
            # Scope for the Browse API
            "scope": "https://api.ebay.com/oauth/api_scope"
        }

        response = requests.post(url, headers=headers, data=data, timeout=30)

        # Handle response
        if response.ok:
            token_info = response.json()
            self.oauth_token = token_info["access_token"]
            expires_in = token_info["expires_in"]
            self.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            LOGGER.info("✅ New Access Token generated!")
            LOGGER.info(f"Access Token (first 50 chars): {self.oauth_token[:50]} ...")
            LOGGER.info(f"Expires in: {expires_in} seconds")
        else:
            LOGGER.error("❌ Failed to get token")
            LOGGER.error(f"Status: {response.status_code}")
            LOGGER.error(f"Response: {response.text}")
            raise Exception(f"Failed to get eBay OAuth token: {response.text}")

    def search_ebay_auctions(
        self,
        keywords,
        countries=None,
        max_price=None,
        min_price=None,
        max_time_remaining=None,
        category_ids=None,
        condition_ids=None,
        sort_by_ending_soon=True,  # New parameter to sort by ending soon by default
        include_auction_items=True,  # Whether to filter for auction items
    ):
        """Search eBay auctions based on specified criteria using the modern Browse API.

        Args:
            keywords (str): Keywords to search for in the auction titles.
            countries (list, optional): List of country codes to search within. Defaults to European countries.
            max_price (float, optional): Maximum price of the items to search for. Defaults to None.
            min_price (float, optional): Minimum price of the items to search for. Defaults to None.
            max_time_remaining (int, optional): Maximum time remaining for the auction in seconds. Defaults to None.
            category_ids (list, optional): List of category IDs to search within. Defaults to None.
            condition_ids (list, optional): List of condition IDs to filter the items. Defaults to None.
            sort_by_ending_soon (bool): Whether to sort results by ending soonest. Defaults to True.
            include_auction_items (bool): Whether to filter results for auction items only. Defaults to True.

        Returns:
            list: List of filtered auction items based on the search criteria.
        """
        LOGGER.info(f"Searching eBay for: {keywords}")
        LOGGER.info(f"Search parameters - Max Price: {max_price}, Min Price: {min_price}, Max Time Remaining: {max_time_remaining}, Sort by Ending Soon: {sort_by_ending_soon}, Include Auction Items: {include_auction_items}")
        
        # Check if API is accessible first
        is_valid, status_message = self.check_api_status()
        if not is_valid:
            LOGGER.warning(f"eBay API not accessible: {status_message}. Returning empty results.")
            return []

        countries = countries or self.EUROPEAN_COUNTRIES
        results = []

        # Limit to first 1 country to avoid rate limits
        countries_to_search = countries[:1] if len(countries) > 1 else countries
        LOGGER.info(f"Searching in countries: {countries_to_search}")

        for i, country in enumerate(countries_to_search):
            LOGGER.info(f"Searching in country: {country} (attempt {i+1}/{len(countries_to_search)})")
            
            # Add a longer delay to avoid hitting rate limits
            if i > 0:
                LOGGER.info("Waiting 60 seconds between requests to avoid rate limits...")
                time.sleep(60)  # 60 second delay between requests
                
            # Ensure we have a fresh token
            token = self._get_fresh_token()
            
            # Build headers with OAuth token
            headers = self._build_headers(token)
            
            # Process multiple pages of results - with early termination when time limit is exceeded
            offset = 0
            items_processed = 0
            total_available = None
            
            # Increase the limit to process more items to find auctions ending soon
            max_items_to_process = 1000  # Process up to 20 pages of 50 items each (max 200 per API is allowed)
            
            # Track statistics for efficiency
            potential_auctions_found = 0
            ending_soon_auctions = 0
            
            # Flag to know when to stop processing early (when we see items past our time window)
            early_termination = False
            
            while items_processed < max_items_to_process and not early_termination:
                # Build query parameters for the Browse API with pagination
                params = self._build_params(
                    keywords,
                    country,
                    max_price,
                    min_price,
                    category_ids,
                    condition_ids,
                    sort_by_ending_soon=sort_by_ending_soon,
                    include_auction_items=include_auction_items,
                    limit=50,
                    offset=offset
                )
                
                LOGGER.info(f"Making request with parameters: {params}")
                response = self._make_request(headers, params)

                if response:
                    LOGGER.info(f"API request successful. Response keys: {list(response.keys()) if response else 'None'}")
                    
                    # Get total available items if not already known
                    if total_available is None:
                        total_available = response.get('total', 0)
                        LOGGER.info(f"Total available matching items: {total_available}")
                    
                    items = self._extract_items(response)
                    LOGGER.info(f"Extracted {len(items)} items from response (offset: {offset})")
                    
                    if not items:  # No more items to process
                        LOGGER.debug("No more items in response, breaking pagination loop")
                        break
                    
                    # Process items and filter by time - with early termination
                    filtered_items = self._filter_items_by_time_with_early_termination(items, max_time_remaining, min_price, max_price)
                    LOGGER.info(f"Filtered to {len(filtered_items)} items based on time remaining")
                    
                    # Check if we should terminate early (no items passed the time filter, meaning we've passed our window)
                    if max_time_remaining is not None and len(filtered_items) == 0 and len(items) > 0:
                        LOGGER.info("Early termination: No items in this batch passed the time filter - assuming we've passed our time window")
                        early_termination = True
                    
                    # Update statistics
                    for item in items:
                        if self._is_potential_auction_from_summary(item):
                            potential_auctions_found += 1
                    
                    ending_soon_auctions += len(filtered_items)
                    
                    results.extend(filtered_items)
                    
                    # Log some examples if we found any deals
                    if len(filtered_items) > 0:
                        LOGGER.info(f"Found {len(filtered_items)} auction items matching criteria")
                        for item in filtered_items[:3]:  # Log first 3 items as examples
                            LOGGER.info(f"  - {item.get('title', 'No title')} - {item.get('price', 'No price')} - Time Left: {item.get('time_remaining', 'No time')} - ID: {item.get('item_id', 'No ID')}")
                    
                    # Update counters
                    items_processed += len(items)
                    offset += 50  # 50 is the default limit
                    
                    # If we've processed all available items, break
                    if items_processed >= total_available:
                        LOGGER.debug(f"Processed all available items ({total_available}), breaking pagination loop")
                        break
                        
                    # Add a small delay to be respectful to the API
                    time.sleep(2)
                else:
                    # If we get an error, return what we have so far
                    LOGGER.warning("eBay API request failed, returning partial results")
                    break
            
            LOGGER.info(f"Processed {items_processed} total items, found {potential_auctions_found} potential auctions, with {ending_soon_auctions} ending within time limit")

        # Apply additional price filtering to ensure items meet the price constraints
        # This is a secondary check since Browse API might not strictly enforce price filters
        if min_price is not None or max_price is not None:
            price_filtered_results = []
            for item in results:
                # Extract price value from the formatted price string like "140.00 USD"
                price_str = item.get('price', '0 USD')
                try:
                    price_val = float(price_str.split()[0])  # Get the numeric part
                except (ValueError, IndexError):
                    price_val = 0  # Default to 0 if parsing fails
                
                # Apply min and max price constraints
                price_ok = True
                if min_price is not None and price_val < min_price:
                    price_ok = False
                    LOGGER.debug(f"Item {item.get('item_id', 'Unknown')} filtered out - price {price_val} below min {min_price}")
                if max_price is not None and price_val > max_price:
                    price_ok = False
                    LOGGER.debug(f"Item {item.get('item_id', 'Unknown')} filtered out - price {price_val} above max {max_price}")
                
                if price_ok:
                    price_filtered_results.append(item)
                else:
                    LOGGER.debug(f"Item {item.get('item_id', 'Unknown')} price {price_val} does not meet constraints. Min: {min_price}, Max: {max_price}")
            
            results = price_filtered_results
            LOGGER.info(f"After price filtering: {len(results)} items remain within price limits ({min_price}-{max_price})")

        # Apply keyword-based filtering to ensure items are actually iPhones and not accessories
        # This helps eliminate unrelated items that contain "iphone" in their title but aren't actual iPhones
        if keywords and "iphone" in keywords.lower():
            iphone_filtered_results = []
            for item in results:
                title = item.get('title', '').lower()
                # Check if the item is actually an iPhone device, not an accessory
                # Common iPhone-related keywords that indicate it's an actual iPhone:
                iphone_keywords = ['iphone', 'apple']
                # Common accessory keywords that indicate it's NOT an actual iPhone:
                accessory_keywords = [
                    'case', 'cover', 'skin', 'protector', 'screen guard', 'glass',
                    'charger', 'cable', 'adapter', 'headphones', 'earbuds', 'airpods',
                    'battery', 'power bank', 'dock', 'stand', 'mount', 'holder',
                    'strap', 'lanyard', 'belt clip', 'wallet', 'sleeve', 'pouch',
                    'bag', 'pack', 'backpack', 'holster', 'bumper'
                ]
                
                # Check if it contains iPhone keywords
                has_iphone_keyword = any(keyword in title for keyword in iphone_keywords)
                
                # Check if it's an accessory
                is_accessory = any(accessory in title for accessory in accessory_keywords)
                
                # Only include items that are iPhones but not accessories
                if has_iphone_keyword and not is_accessory:
                    iphone_filtered_results.append(item)
                else:
                    LOGGER.debug(f"Item filtered out - likely an accessory: {title}")
            
            results = iphone_filtered_results
            LOGGER.info(f"After iPhone filtering: {len(results)} items remain (filtered out accessories)")

        LOGGER.info(f"Search completed. Total results: {len(results)} items")
        return results

    def _build_headers(self, token):
        """Build the headers required for making requests to the eBay Browse API.

        Returns:
            dict: A dictionary containing the necessary headers for the eBay API request.
        """
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _build_params(
        self,
        keywords,
        country,
        max_price,
        min_price,
        category_ids,
        condition_ids,
        sort_by_ending_soon=True,  # Changed default to True for auction deals
        include_auction_items=True,  # Whether to filter for auction items
        limit=50,  # Number of items per request
        offset=0  # Offset for pagination
    ):
        """Build the query parameters for the eBay Browse API request.

        Args:
            keywords (str): Keywords to search for.
            country (str): Country code to search within.
            max_price (float): Maximum price of items.
            min_price (float): Minimum price of items.
            category_ids (list): List of category IDs.
            condition_ids (list): List of condition IDs.
            sort_by_ending_soon (bool): Whether to sort by ending soonest (default True)
            include_auction_items (bool): Whether to filter for auction items (default True)
            limit (int): Number of items per request (max 200)
            offset (int): Offset for pagination

        Returns:
            dict: Query parameters for the eBay Browse API request.
        """
        params = {
            "q": keywords,  # 'q' is the query parameter for keywords
            "limit": str(limit),  # Limit the number of results (max 200)
            "offset": str(offset)  # For pagination
        }
        
        # Note: The Browse API doesn't have a direct country filter like the old API
        # We can use aspects filters or item filters where supported
        
        # Price filters
        if max_price:
            params["maxPrice"] = str(max_price)
        if min_price:
            params["minPrice"] = str(min_price)
            
        # Category filters
        if category_ids:
            # Multiple category IDs need to be comma-separated
            params["categoryId"] = ",".join(category_ids)
            
        # Condition filters
        if condition_ids:
            # Map eBay condition IDs to Browse API values
            condition_map = {
                "1000": "NEW",    # New
                "1500": "NEW",    # New other (unused)
                "2000": "NEW",    # Manufacturer refurbished
                "2500": "REFURBISHED",  # Seller refurbished
                "3000": "USED",   # Used
                "4000": "USED",   # Very Good
                "5000": "USED",   # Good
                "6000": "FOR_PARTS_OR_NOT_WORKING",  # For parts or not working
            }
            
            conditions = []
            for condition_id in condition_ids:
                if str(condition_id) in condition_map:
                    conditions.append(condition_map[str(condition_id)])
                    
            if conditions:
                params["condition"] = ",".join(conditions)

        # Filter for auction items using buyingOptions
        if include_auction_items:
            params["buyingOptions"] = "AUCTION"

        # Sort by ending soon if requested (this is essential for finding deals ending soon)
        if sort_by_ending_soon:
            # Sort by ending soonest to get auctions ending soon first
            params["sort"] = "endingSoonest"
        
        LOGGER.debug(f"Query parameters: {params}")
        return params

    def _make_request(self, headers, params):
        max_retries = 2  # Reduced retries to avoid further rate limiting
        retry_delay = 30  # Increased delay to avoid rate limits
        
        for attempt in range(max_retries):
            try:
                response = requests.get(self.EBAY_API_URL, headers=headers, params=params, timeout=30)
                
                # Check for different status codes
                if response.status_code in (401, 403):
                    # Token might be invalid, try refreshing
                    LOGGER.warning("Token might be invalid, refreshing...")
                    self._refresh_token()
                    # Retry with new token
                    headers = self._build_headers(self.oauth_token)
                    response = requests.get(self.EBAY_API_URL, headers=headers, params=params, timeout=30)
                    if response.status_code in (401, 403):
                        LOGGER.error(f"Authentication Error ({response.status_code}): Check your OAuth token.")
                        LOGGER.error(f"Response content: {response.text}")
                        return None
                        
                elif response.status_code == 429:  # Rate limit
                    LOGGER.warning(f"Rate limit exceeded. Attempt {attempt + 1} of {max_retries}")
                    if attempt < max_retries - 1:
                        LOGGER.info(f"Waiting {retry_delay} seconds before retry...")
                        time.sleep(retry_delay)
                        continue
                    else:
                        LOGGER.error("Rate limit exceeded and no more retries left")
                        return None
                        
                elif response.status_code == 500:
                    # Server error
                    LOGGER.error(f"Server error (500): {response.text}")
                    if attempt < max_retries - 1:
                        LOGGER.info(f"Waiting {retry_delay} seconds before retry...")
                        time.sleep(retry_delay)
                        continue
                    else:
                        LOGGER.error("Server error and no more retries left")
                        return None
                
                response.raise_for_status()
                
                # Parse JSON response
                result = response.json()
                return result
                
            except requests.exceptions.Timeout:
                LOGGER.error(f"Timeout error on attempt {attempt + 1}")
                if attempt < max_retries - 1:
                    LOGGER.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    return None
            except requests.exceptions.RequestException as error:
                LOGGER.error(f"Request error on attempt {attempt + 1}: {error}")
                if attempt < max_retries - 1:
                    LOGGER.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    LOGGER.error(f"Response content: {response.text if 'response' in locals() else 'No response'}")
                    return None
            except ValueError as error:  # JSON decode error
                LOGGER.error(f"JSON Parse error on attempt {attempt + 1}: {error}")
                LOGGER.error(f"Response content: {response.text if 'response' in locals() else 'No response'}")
                return None
            except Exception as error:
                LOGGER.error(f"Unexpected error on attempt {attempt + 1}: {error}")
                if attempt < max_retries - 1:
                    LOGGER.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    LOGGER.error(f"Response content: {response.text if 'response' in locals() else 'No response'}")
                    return None
        return None

    def _extract_items(self, data):
        """Extract items from the JSON response."""
        items = data.get("itemSummaries", [])
        
        # The Browse API doesn't clearly distinguish between auction and fixed-price items
        # in the summary response, so we'll return all items and check details later
        # But we can try to filter based on available data
        
        # Look at the response metadata
        total_available = data.get("total", len(items))  # Use 'total' field which is common in Browse API
        LOGGER.info(f"API reports {total_available} total matching items, received {len(items)} in this batch")
        
        # Instead of trying to identify auction items from summary, return all items
        # to be checked in detail later in _filter_items_by_time
        return items

    def _filter_items_by_time(self, items, max_time_remaining, min_price=None, max_price=None):
        """Filter items by time remaining using detailed item information."""
        if max_time_remaining is None:
            # If no time limit, return all items with basic formatting
            filtered_items = []
            for item in items:
                time_remaining = timedelta(0)  # Default to 0 if no time filter
                formatted_item = self._format_item(item, time_remaining)
                filtered_items.append(formatted_item)
            return filtered_items

        filtered_items = []
        potential_auctions_count = 0
        detailed_api_calls = 0
        
        for i, item in enumerate(items):
            LOGGER.debug(f"Processing item {i+1}/{len(items)}: {item.get('title', 'No title')}")
            
            # Since we're filtering by buyingOptions=AUCTION, most items should be auctions
            # But let's still verify to ensure accuracy
            is_potential_auction = self._is_potential_auction_from_summary(item)
            
            if is_potential_auction:
                potential_auctions_count += 1
            
            # Get detailed item information to access auction end time
            # Since we're filtering by AUCTION, we'll check details for all items
            item_id = item.get('itemId', 'Unknown')
            if item_id != 'Unknown':
                # Fetch detailed item info to get end time
                detailed_api_calls += 1
                detailed_item = self._get_detailed_item(item_id)
                
                # Use the detailed item if available, otherwise fallback to summary
                item_to_use = detailed_item if detailed_item else item
                is_auction = False
                
                if detailed_item:
                    LOGGER.debug(f"Got detailed info for item {item_id}")
                    
                    # Double-check if this is actually an auction item with detailed info
                    is_auction = self._is_auction_item(detailed_item)
                    
                    # If detailed item doesn't confirm it's an auction, check the summary again
                    if not is_auction and not self._is_potential_auction_from_summary(item):
                        LOGGER.debug(f"Item {item_id} is not an auction item based on detailed info and summary, skipping")
                        continue  # Skip non-auction items
                else:
                    # If no detailed info, use the summary info which was already filtered for AUCTION
                    is_auction = True  # We already know from search that these should be auctions
                    item_to_use = item  # Use the summary item if detailed not available
                    LOGGER.warning(f"Could not fetch detailed info for item {item_id}, using summary info")
                
                # Extract time remaining - try multiple approaches
                time_remaining = None
                end_date_str = None
                
                if detailed_item:
                    # Try to get time from detailed item first
                    end_date_str = detailed_item.get('itemEndDate', None)  # Use itemEndDate which is more commonly available
                    if not end_date_str:
                        end_date_str = detailed_item.get('scheduledEndDate', None)
                    
                    if not end_date_str:
                        # Fallback to sellingStatus timeLeft
                        time_left = detailed_item.get('sellingStatus', {}).get('timeLeft')
                        if time_left:
                            time_remaining = self._parse_time_left(time_left)
                
                # If we still don't have time from detailed item, try to get it from the summary
                if not end_date_str and not time_remaining:
                    # The summary might have time info in sellingState
                    selling_state = item.get('sellingState', {})
                    summary_time_left = selling_state.get('timeLeft')
                    if summary_time_left:
                        time_remaining = self._parse_time_left(summary_time_left)
                    else:
                        # If item is ending imminently, it might show as 0:00:00 in summary
                        # For now, if we can't get the exact time, we'll assume it might be valid
                        # since the Browse API already sorted by ending soonest
                        time_remaining = timedelta(0)  # Default to 0 if we can't determine
                
                # If we have an end date, calculate precise time remaining
                if end_date_str:
                    time_remaining = self._calculate_precise_time_remaining(end_date_str)
                elif not time_remaining:
                    # If no time info available, default to a large value (won't match time filter)
                    time_remaining = timedelta(days=30)
                
                LOGGER.debug(f"Time remaining for item {item_id}: {time_remaining}")
                
                # Check if time remaining is within the specified limit
                LOGGER.debug(f"Time remaining: {time_remaining.total_seconds()} seconds, limit: {max_time_remaining} seconds")
                if max_time_remaining is None or time_remaining.total_seconds() <= max_time_remaining:
                    LOGGER.info(f"FOUND AUCTION ENDING SOON! Item {item_id}: {item.get('title', 'No title')} - Time left: {time_remaining}")
                    # Use the best available item data for formatting
                    formatted_item = self._format_item(item_to_use, time_remaining)
                    filtered_items.append(formatted_item)
                else:
                    LOGGER.debug(f"Item {item_id} exceeds time limit, excluding from results")
            else:
                LOGGER.warning(f"Item has no ID, skipping")
        
        # Sort the results by time remaining (ending soonest first)
        if max_time_remaining is not None:
            filtered_items.sort(key=lambda x: self._time_string_to_seconds(x.get('time_remaining', 'P30D')))
        
        LOGGER.info(f"Of {len(items)} total items, {potential_auctions_count} were identified as potential auctions, requiring {detailed_api_calls} detailed API calls")
        LOGGER.info(f"Filtered to {len(filtered_items)} items within time limit")
        
        return filtered_items

    def _filter_items_by_time_with_early_termination(self, items, max_time_remaining, min_price=None, max_price=None):
        """Filter items by time remaining and determine if early termination is appropriate."""
        if max_time_remaining is None:
            # If no time limit, return all items with basic formatting
            filtered_items = []
            for item in items:
                time_remaining = timedelta(0)  # Default to 0 if no time filter
                formatted_item = self._format_item(item, time_remaining)
                filtered_items.append(formatted_item)
            return filtered_items

        filtered_items = []
        potential_auctions_count = 0
        detailed_api_calls = 0
        all_exceeded_time_limit = True  # Flag to track if all items in this batch exceeded the time limit
        
        for i, item in enumerate(items):
            LOGGER.debug(f"Processing item {i+1}/{len(items)}: {item.get('title', 'No title')}")
            
            # Since we're filtering by buyingOptions=AUCTION, most items should be auctions
            is_potential_auction = self._is_potential_auction_from_summary(item)
            
            if is_potential_auction:
                potential_auctions_count += 1
            
            # Get detailed item information to access auction end time
            item_id = item.get('itemId', 'Unknown')
            if item_id != 'Unknown':
                # Fetch detailed item info to get end time
                detailed_api_calls += 1
                detailed_item = self._get_detailed_item(item_id)
                
                # Use the detailed item if available, otherwise fallback to summary
                item_to_use = detailed_item if detailed_item else item
                is_auction = False
                
                if detailed_item:
                    LOGGER.debug(f"Got detailed info for item {item_id}")
                    
                    # Double-check if this is actually an auction item with detailed info
                    is_auction = self._is_auction_item(detailed_item)
                    
                    # If detailed item doesn't confirm it's an auction, check the summary again
                    if not is_auction and not self._is_potential_auction_from_summary(item):
                        LOGGER.debug(f"Item {item_id} is not an auction item based on detailed info and summary, skipping")
                        continue  # Skip non-auction items
                else:
                    # If no detailed info, use the summary info which was already filtered for AUCTION
                    is_auction = True  # We already know from search that these should be auctions
                    item_to_use = item  # Use the summary item if detailed not available
                    LOGGER.warning(f"Could not fetch detailed info for item {item_id}, using summary info")
                
                # Extract time remaining - try multiple approaches
                time_remaining = None
                end_date_str = None
                
                if detailed_item:
                    # Try to get time from detailed item first
                    end_date_str = detailed_item.get('itemEndDate', None)  # Use itemEndDate which is more commonly available
                    if not end_date_str:
                        end_date_str = detailed_item.get('scheduledEndDate', None)
                    
                    if not end_date_str:
                        # Fallback to sellingStatus timeLeft
                        time_left = detailed_item.get('sellingStatus', {}).get('timeLeft')
                        if time_left:
                            time_remaining = self._parse_time_left(time_left)
                
                # If we still don't have time from detailed item, try to get it from the summary
                if not end_date_str and not time_remaining:
                    # The summary might have time info in sellingState
                    selling_state = item.get('sellingState', {})
                    summary_time_left = selling_state.get('timeLeft')
                    if summary_time_left:
                        time_remaining = self._parse_time_left(summary_time_left)
                    else:
                        # If item is ending imminently, it might show as 0:00:00 in summary
                        # For now, if we can't get the exact time, we'll assume it might be valid
                        # since the Browse API already sorted by ending soonest
                        time_remaining = timedelta(0)  # Default to 0 if we can't determine
                
                # If we have an end date, calculate precise time remaining
                if end_date_str:
                    time_remaining = self._calculate_precise_time_remaining(end_date_str)
                elif not time_remaining:
                    # If no time info available, default to a large value (won't match time filter)
                    time_remaining = timedelta(days=30)
                
                LOGGER.debug(f"Time remaining for item {item_id}: {time_remaining}")
                
                # Check if time remaining is within the specified limit
                LOGGER.debug(f"Time remaining: {time_remaining.total_seconds()} seconds, limit: {max_time_remaining} seconds")
                if max_time_remaining is None or time_remaining.total_seconds() <= max_time_remaining:
                    LOGGER.info(f"FOUND AUCTION ENDING SOON! Item {item_id}: {item.get('title', 'No title')} - Time left: {time_remaining}")
                    # Use the best available item data for formatting
                    formatted_item = self._format_item(item_to_use, time_remaining)
                    filtered_items.append(formatted_item)
                    all_exceeded_time_limit = False  # At least one item was within time limit
                else:
                    LOGGER.debug(f"Item {item_id} exceeds time limit, excluding from results")
            else:
                LOGGER.warning(f"Item has no ID, skipping")
        
        # Sort the results by time remaining (ending soonest first)
        if max_time_remaining is not None:
            filtered_items.sort(key=lambda x: self._time_string_to_seconds(x.get('time_remaining', 'P30D')))
        
        LOGGER.info(f"Of {len(items)} total items, {potential_auctions_count} were identified as potential auctions, requiring {detailed_api_calls} detailed API calls")
        LOGGER.info(f"Filtered to {len(filtered_items)} items within time limit")
        
        # Return both the filtered items and a flag indicating if early termination is appropriate
        # (if all items in this batch exceeded the time limit, we might want to stop processing)
        return filtered_items

    def _time_string_to_seconds(self, time_str):
        """Convert time string like '2 days, 3:04:05.000000' to seconds."""
        try:
            # Parse timedelta string representation
            if 'day' in time_str:
                # Format: '2 days, 3:04:05.000000'
                parts = time_str.split(', ')
                days_part = int(parts[0].split()[0])
                time_part = parts[1] if len(parts) > 1 else '0:00:00'
                time_components = time_part.split(':')
                
                hours = int(time_components[0]) if len(time_components) > 0 else 0
                minutes = int(time_components[1]) if len(time_components) > 1 else 0
                seconds = int(float(time_components[2])) if len(time_components) > 2 else 0
                
                total_seconds = days_part * 86400 + hours * 3600 + minutes * 60 + seconds
                return total_seconds
            else:
                # Format: '3:04:05.000000'
                time_components = time_str.split(':')
                hours = int(time_components[0]) if len(time_components) > 0 else 0
                minutes = int(time_components[1]) if len(time_components) > 1 else 0
                seconds = int(float(time_components[2])) if len(time_components) > 2 else 0
                
                total_seconds = hours * 3600 + minutes * 60 + seconds
                return total_seconds
        except:
            # If parsing fails, return a large default value to sort to the end
            return 9999999

    def _is_potential_auction_from_summary(self, item_summary):
        """Check if an item might be an auction based on summary information to avoid unnecessary detailed API calls."""
        # Look for characteristics that suggest this might be an auction item in the summary
        
        # 1. Check if bidCount exists in the summary (strong indicator of auction)
        if 'bidCount' in item_summary and item_summary.get('bidCount') not in (None, 0):
            return True
            
        # 2. Check sellingState for auction-specific fields
        selling_state = item_summary.get('sellingState', {})
        if isinstance(selling_state, dict):
            # Check if there are auction-specific states
            bid_count = selling_state.get('bidCount')
            if bid_count is not None and bid_count != 0:
                return True
            if selling_state.get('currentBidPrice') is not None:
                return True
            if selling_state.get('bidIncrement') is not None:
                return True
            # Check for timeLeft in sellingState, which is common for auctions
            if 'timeLeft' in selling_state:
                return True
            # Check for other auction-specific states
            if 'startTime' in selling_state and 'endTime' in selling_state:
                return True
        
        # 3. Check for auction/bid references in various fields
        for key in item_summary.keys():
            if isinstance(key, str):
                if 'bid' in key.lower() or 'auction' in key.lower():
                    return True
        
        # 4. Check item aspects for auction-related terms
        item_aspects = item_summary.get('itemAspects', {})
        if item_aspects and isinstance(item_aspects, dict):
            for aspect_name, aspect_values in item_aspects.items():
                if 'bid' in str(aspect_name).lower() or 'auction' in str(aspect_name).lower():
                    return True
                if isinstance(aspect_values, list):
                    for value in aspect_values:
                        if 'bid' in str(value).lower() or 'auction' in str(value).lower():
                            return True
                elif 'bid' in str(aspect_values).lower() or 'auction' in str(aspect_values).lower():
                    return True

        # 5. Check price type - if it's not fixedPrice, it might be auction
        price_info = item_summary.get('price', {})
        price_type = price_info.get('type', '').lower()
        if price_type and price_type != 'fixed_price':
            return True

        # 6. Check for buyItNowAvailability - auctions often have buyItNow set to false
        buy_it_now_available = item_summary.get('buyItNowAvailable')
        if buy_it_now_available is False:
            return True
        
        # 7. Check for other auction indicators
        # Items with "bestOfferEnabled" false but with bidding activity are more likely auctions
        best_offer_enabled = item_summary.get('bestOfferEnabled')
        if best_offer_enabled is False:
            # If also has other auction indicators, it's more likely an auction
            if 'bidCount' in item_summary or 'sellingState' in item_summary:
                return True
        
        # 8. Check if item is in a typical auction category
        primary_category_id = item_summary.get('primaryCategoryId')
        if primary_category_id:
            # Some categories are more likely to have auctions (this is just an example - in real world
            # you'd have a mapping of categories where auctions are common)
            # For now, we'll just check if it's not in a category that's typically buy-it-now
            pass  # Placeholder for category-based filtering

        # If none of the indicators are present, assume it's not an auction to save API calls
        return False

    def _is_auction_item(self, detailed_item):
        """Check if an item is an auction based on detailed item information."""
        # Check various fields that indicate this is an auction item
        
        # 1. Check buying options - if it includes AUCTION, it's an auction (primary check)
        buying_options = detailed_item.get('buyingOptions', [])
        if 'AUCTION' in buying_options:
            return True

        # 2. Check bid count (auctions will have bids)
        selling_status = detailed_item.get('sellingStatus', {})
        bid_count = selling_status.get('bidCount', 0)
        if bid_count > 0:
            return True

        # 3. Check if there's current bid price (common in auctions)
        current_bid_price = detailed_item.get('currentBidPrice')
        if current_bid_price is not None:
            return True
            
        # 4. Check if there's timeLeft info (characteristic of auctions)
        time_left = selling_status.get('timeLeft')
        if time_left and 'P' in time_left:  # P indicates period in ISO 8601 format
            return True
            
        # 5. Check for auction-specific fields
        if detailed_item.get('bidCount') is not None:  # Even if 0, this indicates auction
            return True
            
        # 6. Check for auction-specific fields in different locations
        if detailed_item.get('minimumPriceToBid') is not None:
            return True

        # 7. Check for auction-specific fields
        if detailed_item.get('uniqueBidderCount') is not None:
            return True

        # 8. Check for auction listing type if available
        if detailed_item.get('listingDate') and detailed_item.get('scheduledEndDate'):
            # These fields are more common in auction listings
            return True

        # 9. Check if selling status has reserve status which is common in auctions
        if 'reserve' in selling_status:
            return True

        # 10. Check if buy-it-now is not available or if it's not the only buying option for items that look like auctions
        buy_it_now_available = detailed_item.get('buyItNowAvailable', True)
        if not buy_it_now_available and 'FIXED_PRICE' not in buying_options and 'AUCTION' in buying_options:
            return True

        # 11. Check if it has an itemEndDate, which is common for auctions
        item_end_date = detailed_item.get('itemEndDate')
        if item_end_date:
            return True

        # 12. Check for auction terminology in the listing specification
        item_basic_info = detailed_item.get('itemBasicInfo', {})
        listing_type = item_basic_info.get('listingType', '').upper()
        if 'AUCTION' in listing_type or 'CHINESE' in listing_type or 'DUTCH' in listing_type:
            return True
            
        # If none of the above indicators, it might not be an auction
        return False

    def _get_detailed_item(self, item_id):
        """Fetch detailed information for a specific item to get auction end time."""
        # Endpoint for getting individual item details
        item_url = f"https://api.ebay.com/buy/browse/v1/item/{item_id}"
        
        try:
            token = self._get_fresh_token()
            headers = self._build_headers(token)
            
            response = requests.get(item_url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                LOGGER.warning(f"Item {item_id} not found (404)")
                return None
            elif response.status_code in (401, 403):
                LOGGER.warning(f"Authentication issue when fetching item {item_id}, refreshing token...")
                self._refresh_token()
                headers = self._build_headers(self.oauth_token)
                response = requests.get(item_url, headers=headers, timeout=30)
                if response.status_code == 200:
                    return response.json()
                else:
                    LOGGER.error(f"Failed to get detailed item info after token refresh: {response.status_code}")
                    return None
            elif response.status_code == 429:  # Rate limit
                LOGGER.warning(f"Rate limit hit when fetching item {item_id}, waiting before retry...")
                time.sleep(60)  # Wait 60 seconds before retry
                # Try one more time after the wait
                response = requests.get(item_url, headers=headers, timeout=30)
                if response.status_code == 200:
                    return response.json()
                else:
                    LOGGER.error(f"Still failing after rate limit wait: {response.status_code}")
                    return None
            elif response.status_code >= 500:
                LOGGER.error(f"Server error when fetching item {item_id}: {response.status_code}")
                return None
            else:
                LOGGER.error(f"Failed to get detailed item info: {response.status_code} - {response.text}")
                return None
        except requests.exceptions.Timeout:
            LOGGER.error(f"Timeout when fetching detailed item {item_id}")
            return None
        except requests.exceptions.ConnectionError:
            LOGGER.error(f"Connection error when fetching detailed item {item_id}")
            return None
        except Exception as e:
            LOGGER.error(f"Exception when fetching detailed item {item_id}: {e}")
            return None

    def _calculate_precise_time_remaining(self, end_date_str):
        """Calculate precise time remaining by comparing end date with current time."""
        if not end_date_str:
            return timedelta(0)  # Default to 0 if no end date provided

        try:
            # Parse the end date string (format: 2025-10-02T15:30:00.000Z)
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            current_dt = datetime.now(timezone.utc)
            
            # Calculate the time difference
            time_diff = end_dt - current_dt
            
            # If the auction has already ended, return 0
            if time_diff.total_seconds() < 0:
                return timedelta(0)
            
            return time_diff
        except ValueError:
            try:
                # Try parsing without microseconds
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)
                current_dt = datetime.now(timezone.utc)
                time_diff = end_dt - current_dt
                
                if time_diff.total_seconds() < 0:
                    return timedelta(0)
                
                return time_diff
            except ValueError:
                try:
                    # Try parsing without milliseconds
                    end_dt = datetime.strptime(end_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    current_dt = datetime.now(timezone.utc)
                    time_diff = end_dt - current_dt
                    
                    if time_diff.total_seconds() < 0:
                        return timedelta(0)
                    
                    return time_diff
                except ValueError:
                    LOGGER.warning(f"Could not parse end date: {end_date_str}")
                    return timedelta(0)  # Default to 0 if parsing fails

    def _parse_time_left(self, time_left_str, end_date_str=None):
        """
        Parse eBay timeLeft format (e.g., 'P2DT22H59M59S') to timedelta.
        If end_date_str is provided, use it for more precise calculation.
        """
        # If we have the actual end date, calculate precise time remaining
        if end_date_str:
            return self._calculate_precise_time_remaining(end_date_str)
        
        # Otherwise, fall back to parsing the timeLeft format
        if not time_left_str or time_left_str.startswith('Unknown'):
            return timedelta(0)  # Default to 0 if parsing fails

        # Remove the 'P' at the beginning
        time_str = time_left_str[1:]
        
        # Handle the time format: P2DT22H59M59S (2 days, 22 hours, 59 minutes, 59 seconds)
        days = hours = minutes = seconds = 0
        
        # Check for days (until T)
        if 'T' in time_str:
            date_part, time_part = time_str.split('T')
            if date_part and 'D' in date_part:
                days = int(date_part.replace('D', ''))
        else:
            # If there's no 'T', the whole string might be days
            if 'D' in time_str:
                date_part = time_str
                days = int(date_part.replace('D', ''))
            else:
                # If there's no 'D', it might just be time (this shouldn't normally happen)
                time_part = time_str
        
        # Extract time components if they exist
        if 'T' in time_str:
            time_part = time_str.split('T')[1]
        else:
            time_part = ''
            
        if time_part:
            # Parse H, M, S from the time part
            current_num = ""
            for char in time_part:
                if char.isdigit():
                    current_num += char
                elif char == 'H':
                    hours = int(current_num)
                    current_num = ""
                elif char == 'M':
                    minutes = int(current_num)
                    current_num = ""
                elif char == 'S':
                    seconds = int(current_num)
                    current_num = ""
        
        return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

    def _item_might_be_within_time_limit(self, item):
        """Basic check to see if an item might be within time limits based on summary."""
        # For auction hunting, we're primarily concerned with items that are likely ending soon
        # Since we can't tell from summary alone, we'll be conservative and include the item
        return True

    def _parse_end_time(self, end_time: str) -> datetime:
        """Parse the end time string to datetime object."""
        if not end_time:
            return datetime.now(timezone.utc) + timedelta(days=7)  # Default to 7 days if no end time
        
        try:
            return datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            try:
                return datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%S.000Z").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                # If unable to parse, return a default datetime
                return datetime.now(timezone.utc) + timedelta(days=1)  # Default to 1 day

    def _format_item(self, item, time_remaining):
        """Format the item data to match the expected structure."""
        # Extract price information
        price_info = item.get('price', {})
        price_value = price_info.get('value', '0')
        price_currency = price_info.get('currency', 'USD')
        
        # Extract seller information (may not be available in summary)
        seller_info = item.get('seller', {})
        seller_username = seller_info.get('username', 'Unknown')
        
        # Extract item location info
        item_location = item.get('itemLocation', {})
        location = item_location.get('city', 'Unknown')
        if 'country' in item_location:
            location += f", {item_location['country']}"
        
        # Extract shipping information
        shipping_info = item.get('shippingOptions', [{}])
        shipping_cost = '0'
        shipping_currency = 'USD'
        if shipping_info and len(shipping_info) > 0:
            first_shipping = shipping_info[0]
            shipping_cost_info = first_shipping.get('shippingCost', {})
            shipping_cost = shipping_cost_info.get('value', '0')
            shipping_currency = shipping_cost_info.get('currency', 'USD')
        
        # Map condition to id if possible
        condition_name = item.get('condition', 'Unknown')
        condition_id = self._map_condition_name_to_id(condition_name)
        
        # Create formatted item
        return {
            "country": item.get("country", "US"),  # Browse API doesn't always return country in summary
            "title": item.get("title", "No title"),
            "price": f"{price_value} {price_currency}",
            "time_remaining": str(time_remaining),
            "url": item.get("itemWebUrl", item.get("permalink", "No URL available")),
            "category": item.get("primaryCategoryId", {}).get("categoryName", "Unknown") if isinstance(item.get("primaryCategoryId"), dict) else item.get("primaryCategoryId", "Unknown"),
            "category_id": item.get("primaryCategoryId", "Unknown"),
            "item_id": item.get("itemId", "Unknown"),
            "condition_id": condition_id,
            "condition_display_name": condition_name,
            "listing_type": item.get("listingType", "Unknown"),  # Browse API may not have this
            "start_time": "Unknown",  # Not available in summary
            "end_time": "Unknown",  # Not available in summary
            "seller_user_id": seller_username,
            "feedback_score": "Unknown",  # Not available in summary
            "feedback_percentage": "Unknown",  # Not available in summary
            "shipping_cost": f"{shipping_cost} {shipping_currency}",
            "location": location,
            "gallery_url": item.get("image", {}).get("imageUrl", "No URL available"),
        }
    
    def _map_condition_name_to_id(self, condition_name):
        """Map condition name to ID for compatibility."""
        condition_map = {
            "NEW": "1000",
            "REFURBISHED": "2500",
            "USED": "3000",
            "FOR_PARTS_OR_NOT_WORKING": "6000"
        }
        return condition_map.get(condition_name.upper(), "Unknown")

    def find_auction_deals(
        self,
        keywords,
        max_price=None,
        min_price=None,
        time_remaining_hours=6,
        countries=None,
        category_ids=None,
        condition_ids=None,
        min_discount_percent=0
    ):
        """
        Find auction deals - items that are ending soon with good pricing.
        
        Args:
            keywords (str): Keywords to search for
            max_price (float): Maximum price threshold
            min_price (float): Minimum price threshold
            time_remaining_hours (int): Max hours remaining for auction (default 6 hours)
            countries (list): List of countries to search in
            category_ids (list): List of category IDs to filter
            condition_ids (list): List of condition IDs to filter
            min_discount_percent (float): Minimum discount percentage from original price (if available)
        
        Returns:
            list: List of auction deals sorted by best value
        """
        LOGGER.info(f"Finding auction deals for: {keywords}")
        LOGGER.info(f"Time remaining threshold: {time_remaining_hours} hours ({time_remaining_hours * 3600} seconds)")
        
        try:
            # Convert hours to seconds for the API
            max_time_remaining = time_remaining_hours * 3600 if time_remaining_hours else None
            
            # Search for auctions ending soon
            auctions = self.search_ebay_auctions(
                keywords=keywords,
                countries=countries,
                max_price=max_price,
                min_price=min_price,
                max_time_remaining=max_time_remaining,
                category_ids=category_ids,
                condition_ids=condition_ids,
                sort_by_ending_soon=True
            )
            
            if auctions is None:
                LOGGER.warning("Auctions search returned None, returning empty list")
                return []
            
            # Filter for potential deals based on additional criteria
            deals = []
            for auction in auctions:
                if auction:  # Ensure auction is not None
                    # Additional deal detection logic can go here
                    # For now, we'll include all returned auctions as potential deals
                    auction['is_potential_deal'] = True  # Mark as potential deal
                    
                    # Calculate potential value metrics if available
                    # (Placeholder for additional logic to determine value)
                    deals.append(auction)
            
            LOGGER.info(f"Found {len(deals)} potential auction deals")
            return deals
        except Exception as e:
            LOGGER.error(f"Error occurred in find_auction_deals: {e}")
            return []

    def check_api_status(self):
        """Check if the API key is valid and not hitting rate limits."""
        # Simple test request
        try:
            token = self._get_fresh_token()
            headers = self._build_headers(token)
            params = {
                "q": "test",
                "limit": "1"
            }
            
            response = requests.get(self.EBAY_API_URL, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                return True, "API key is valid"
            elif response.status_code == 401:
                return False, "Authentication failed (401). Check your credentials."
            elif response.status_code == 429:
                return False, "Rate limit exceeded (429). Try again later."
            elif response.status_code >= 500:
                return False, f"Server error ({response.status_code}): {response.text[:200]}..."
            else:
                return False, f"API error: {response.status_code} - {response.text[:200]}"
        except Exception as e:
            return False, f"Connection error: {e}"

# Example usage
if __name__ == "__main__":
    # Load environment variables from .env file
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("python-dotenv not available, using system environment variables")

    EBAY_APP_ID = os.getenv("EBAY_APP_ID")
    EBAY_CERT_ID = os.getenv("EBAY_CERT_ID")

    print(f"EBAY_APP_ID: {EBAY_APP_ID}")
    print(f"EBAY_CERT_ID: {'*' * len(EBAY_CERT_ID) if EBAY_CERT_ID else 'None'}")

    searcher = EbayAuctionSearcher()
    
    # Check API status first
    is_valid, status_message = searcher.check_api_status()
    print(f"API Status: {status_message}")
    
    if is_valid:
        print("\n--- Searching for auction deals (ending within 24 hours) ---")
        # Try with more generic terms that typically have auctions
        deals = searcher.find_auction_deals(
            keywords="vintage camera", 
            max_price=200, 
            min_price=5, 
            time_remaining_hours=24  # Items ending in 24 hours or less
        )

        print(f"\nFound {len(deals)} potential auction deals:")
        for deal in deals:
            print(f"  - {deal.get('title', 'No title')}")
            print(f"    Price: {deal.get('price', 'No price')}")
            print(f"    Time Left: {deal.get('time_remaining', 'Unknown')}")
            print(f"    URL: {deal.get('url', 'No URL')}")
            print()
        
        # Also try a second search with different terms
        print("\n--- Searching for more auction deals (watches) ---")
        deals2 = searcher.find_auction_deals(
            keywords="watch", 
            max_price=150, 
            min_price=10, 
            time_remaining_hours=48  # Items ending in 48 hours
        )

        print(f"\nFound {len(deals2)} additional auction deals:")
        for deal in deals2:
            print(f"  - {deal.get('title', 'No title')}")
            print(f"    Price: {deal.get('price', 'No price')}")
            print(f"    Time Left: {deal.get('time_remaining', 'Unknown')}")
            print(f"    URL: {deal.get('url', 'No URL')}")
            print()
    else:
        print("Skipping search due to API issues")