"""Run all JSON auction queries and optionally mirror results to Todoist."""

from __future__ import annotations

import glob
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - optional convenience dependency
    pass

try:
    from .ebay import EbayAuctionSearcher
    from .todoist import TodoistClient
except ImportError:  # pragma: no cover - supports `python src/.../runner.py`
    from ebay import EbayAuctionSearcher
    from todoist import TodoistClient

LOGGER = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        LOGGER.warning("Ignoring invalid integer %s=%r", name, os.getenv(name))
        return default


def _query_files() -> list[str]:
    pattern = os.getenv("ITEM_QUERY_GLOB", "store/item_queries/*.json")
    return sorted(glob.glob(pattern))


def _load_queries(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return [data] if isinstance(data, dict) else []


def _due_date(end_time: str) -> str:
    if end_time and end_time != "Unknown":
        try:
            parsed = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            LOGGER.debug("Could not parse auction end time %r", end_time)
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def main() -> None:
    """Search configured JSON files without requiring eBay credentials."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    searcher = EbayAuctionSearcher()
    todoist_token = os.getenv("TODOIST_TOKEN")
    todoist = TodoistClient(todoist_token) if todoist_token else None
    project_id = os.getenv("TODOIST_PROJECT")
    max_time_remaining = _int_env("MAX_TIME_REMAINING", 28800)
    query_files = _query_files()

    if not query_files:
        LOGGER.warning("No item query files matched %s", os.getenv("ITEM_QUERY_GLOB"))
        return
    if todoist is None:
        LOGGER.info("TODOIST_TOKEN is not set; search results will only be logged")

    for json_file in query_files:
        for query in _load_queries(json_file):
            keywords = query.get("keywords")
            if not keywords:
                LOGGER.warning("Skipping query without keywords in %s", json_file)
                continue
            LOGGER.info("Searching %s: %s", Path(json_file).name, query)
            auctions = searcher.search_ebay_auctions(
                keywords,
                countries=query.get("countries"),
                max_price=query.get("max_price"),
                min_price=query.get("min_price"),
                max_time_remaining=max_time_remaining,
                category_ids=query.get("category_ids"),
                condition_ids=query.get("condition_ids"),
            )
            LOGGER.info("Found %s auctions for %r", len(auctions), keywords)

            for auction in auctions:
                LOGGER.info(
                    "%s | %s | %s | %s",
                    auction["country"],
                    auction["title"],
                    auction["price"],
                    auction["time_remaining"],
                )
                if todoist is None:
                    continue
                title = (
                    f"{auction['country']} - {auction['title']} - {auction['price']}"
                )
                description = (
                    f"Time remaining: {auction['time_remaining']}\n"
                    f"URL: {auction['url']}\n"
                    f"Category: {auction['category']}"
                )
                todoist.submit_task(
                    title=title,
                    description=description,
                    due_date=_due_date(auction["end_time"]),
                    project_id=project_id,
                    item_id=str(auction["item_id"]),
                )


if __name__ == "__main__":
    main()
