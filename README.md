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
Set `"listing_type": "buy_it_now"` (also `bin` or `fixed_price`) to search
fixed-price listings separately; the default is `auction`. Results expose
`shipping_known`, `shipping_cost_value`, `origin_region`, `import_duty`,
`import_vat`, `import_cost_known`, `origin_country_source`, and `landed_price`.
The default site list is
inside the EU customs union, so UK and Switzerland are skipped. Listings whose
visible origin is outside the EU are skipped too; listings without a country
are skipped because an eBay marketplace country is not proof of seller origin.
An explicit listing location is required for the no-import-cost calculation. If
eBay does not show a shipping amount, it is reported as unknown rather than
free and the listing is excluded from search results.

The Python API exposes the same split directly through
`search_ebay_auctions(...)` and `search_ebay_buy_it_now(...)`.

The searcher uses one persistent `requests` session, requests up to two pages
per site by default, and honors `Retry-After` when eBay asks the client to
slow down. Tune this behavior with `EBAY_MAX_PAGES`, `EBAY_PAGE_SIZE`,
`EBAY_MIN_REQUEST_INTERVAL`, and `EBAY_REQUEST_TIMEOUT`. It uses one stable
user agent; it does not rotate identities or attempt to bypass eBay access
controls.

Todoist synchronization is optional. Set `TODOIST_TOKEN` and optionally
`TODOIST_PROJECT` to submit found auctions; without them, the runner only logs
the results.
