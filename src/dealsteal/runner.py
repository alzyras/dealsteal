import glob
import json
import logging
import os
import time
from datetime import datetime, timedelta

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("python-dotenv not available, using system environment variables")

try:
    from .ebay import EbayAuctionSearcher
    from .todoist import TodoistClient
except ImportError:
    from ebay import EbayAuctionSearcher
    from todoist import TodoistClient

# Load environment variables
TODOIST_API_TOKEN = os.environ.get("TODOIST_TOKEN")
PROJECT_ID = os.environ.get("TODOIST_PROJECT")
EBAY_APP_ID = os.environ.get("EBAY_APP_ID")
EBAY_CERT_ID = os.environ.get("EBAY_CERT_ID")
MAX_TIME_REMAINING = int(os.environ.get("MAX_TIME_REMAINING") or 28800)  # Default to 8 hours

client = TodoistClient(TODOIST_API_TOKEN)
searcher = EbayAuctionSearcher(EBAY_APP_ID, EBAY_CERT_ID)

LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Check API status before starting
is_valid, status_message = searcher.check_api_status()
LOGGER.info(f"eBay API Status: {status_message}")

if not is_valid:
    LOGGER.warning("eBay API is not accessible. Running in offline mode...")
    # Continue running but without eBay functionality

json_files = glob.glob("store/item_queries/*.json")

for i, json_file in enumerate(json_files):
    # Add delay between processing different files to avoid rate limits
    if i > 0:
        LOGGER.info("Waiting 5 minutes before processing next file to avoid rate limits...")
        time.sleep(300)  # 5 minutes
        
    with open(json_file, "r") as file:
        data = json.load(file)

        if isinstance(data, list):
            json_list = data
        else:
            json_list = [data]

        for item in json_list:
            LOGGER.info(item)
            auctions = searcher.search_ebay_auctions(
                item["keywords"],
                max_price=item["max_price"],
                min_price=item["min_price"],
                max_time_remaining=MAX_TIME_REMAINING,
            )
            
            # If we get rate limited, continue with empty results
            if not auctions:
                LOGGER.warning("No auctions returned, possibly due to rate limiting. Continuing...")
            
            for auction in auctions:
                LOGGER.info(auction)
                title = (
                    f"{auction['country']} - {auction['title']} - {auction['price']}"
                )
                description = (
                    f"Time remaining: {auction['time_remaining']}\n"
                    f"URL: {auction['url']}\n"
                    f"Category: {auction['category']}"
                )
                # Handle case where end_time is 'Unknown' from the Browse API
                end_time_str = auction["end_time"]
                if end_time_str != "Unknown":
                    try:
                        end_time = datetime.strptime(
                            end_time_str, "%Y-%m-%dT%H:%M:%S.%fZ"
                        )
                        due_date = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except ValueError:
                        # If parsing fails, use a default due date (e.g., 1 day from now)
                        due_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    # Use a default due date if end_time is unknown
                    due_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                
                item_id = str(auction["item_id"])
                client.submit_task(
                    title=title,
                    description=description,
                    due_date=due_date,
                    project_id=PROJECT_ID,
                    item_id=item_id,
                )
