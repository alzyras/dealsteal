# DealSteal
![Deal Steal](dealsteal.png)

Deal Steal searches public eBay auction pages directly. It does not require an
eBay application id, OAuth token, browser automation, or an eBay login.

## Requirements

- Python 3.11
- UV (https://github.com/astral-sh/uv)

## Installation

Install with:

```sh
uv sync --extra dev
```

Run the JSON queries with:

```sh
uv run dealsteal
```

Each file in `store/item_queries/*.json` may contain one query object or a
list of query objects. Existing fields such as `keywords`, `countries`,
`min_price`, `max_price`, `category_ids`, and `condition_ids` are supported.

The searcher uses one persistent `requests` session, requests up to two pages
per site by default, and honors `Retry-After` when eBay asks the client to
slow down. Tune this behavior with `EBAY_MAX_PAGES`, `EBAY_PAGE_SIZE`,
`EBAY_MIN_REQUEST_INTERVAL`, and `EBAY_REQUEST_TIMEOUT`. It uses one stable
user agent; it does not rotate identities or attempt to bypass eBay access
controls.

Todoist synchronization is optional. Set `TODOIST_TOKEN` and optionally
`TODOIST_PROJECT` to submit found auctions; without them, the runner only logs
the results.
