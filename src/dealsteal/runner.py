import glob
import json
import logging
import os
from datetime import datetime

from ebay import EbayAuctionSearcher
from todoist import TodoistClient

TODOIST_API_TOKEN = os.environ.get("TODOIST_TOKEN")
PROJECT_ID = os.environ.get("TODOIST_PROJECT")
EBAY_OAUTH_TOKEN = os.environ.get("EBAY_OAUTH_TOKEN")
EBAY_APP_ID = os.environ.get("EBAY_APP_ID")
MAX_TIME_REMAINING = int(os.environ.get("MAX_TIME_REMAINING"))

client = TodoistClient(TODOIST_API_TOKEN)
searcher = EbayAuctionSearcher(EBAY_OAUTH_TOKEN, EBAY_APP_ID)

LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

json_files = glob.glob("store/item_queries/*.json")

for json_file in json_files:
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
                end_time = datetime.strptime(
                    auction["end_time"], "%Y-%m-%dT%H:%M:%S.%fZ"
                )
                due_date = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                item_id = str(auction["item_id"])
                client.submit_task(
                    title=title,
                    description=description,
                    due_date=due_date,
                    project_id=PROJECT_ID,
                    item_id=item_id,
                )
