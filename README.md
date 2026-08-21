# DealSteal

DealSteal scans public eBay pages without an eBay login, application token, or
browser. Search pages are used for discovery; promising candidates are checked
on their public item page for destination-specific shipping, seller origin,
listing type, and an absolute UTC auction end time.

## Setup

```sh
cp config.example.json config.local.json
uv sync --extra dev
```

Edit `config.local.json` with the real destination postal code and your resale
cost assumptions. The example defaults to Lithuania, EU-origin sellers, EUR,
and a 40% net ROI threshold. It also excludes non-EU marketplace hosts such as
the UK by default. The local config and SQLite database are ignored by Git.

To compare eBay acquisition prices with current Lithuanian resale prices, keep
the local `skelbiu-api` service running and enable its adapter in
`config.local.json`:

```json
{
  "skelbiu_api": {
    "enabled": true,
    "base_url": "http://127.0.0.1:8080",
    "search_limit": 50,
    "detail_limit": 25
  }
}
```

The scanner uses `/v1/listings` for discovery and then
`/v1/listings/{id}` for every comparison candidate. Search-card status is not
trusted: only a detail response with `status: "active"` and a positive price
is used for the live resale average. Sold, removed, missing-price, and other
non-active listings cannot qualify a deal. The local API itself does not need
an API key.

## Product profiles

Version 2 profiles can be stored in any JSON file under `store/item_queries/`:

```json
{
  "version": 2,
  "products": [
    {
      "id": "thinkcentre-m920s-i7-8700",
      "search_terms": ["Lenovo ThinkCentre M920s i7-8700 SFF"],
      "listing_types": ["auction", "buy_it_now"],
      "marketplaces": ["DE", "FR", "PL"],
      "match": {
        "required": [["m920s"], ["i7-8700", "i7 8700"]],
        "excluded": ["micro", "parts", "broken"]
      },
      "tiers": [
        {
          "id": "used-complete",
          "reference_price": {"amount": 300, "currency": "EUR"},
          "conditions": ["used", "pre-owned"]
        }
      ],
      "max_time_remaining_seconds": 86400,
      "max_pages": 2
    }
  ]
}
```

Required groups are ANDed; alternatives inside a group are ORed. A listing
must match exactly one tier to be scored. Profiles without tiers still provide
discovery results but cannot be called profitable deals.

The repository also includes a tracked broad technology watchlist with
conservative Lithuanian Skelbiu reference prices:

```sh
uv run dealsteal validate profiles/tech_deals.json
uv run dealsteal scan profiles/tech_deals.json --jsonl
```

Those reference prices are starting assumptions, not automatically inferred
market values. Update the tiers when your local resale estimate changes.

Legacy files containing `keywords`, `countries`, `listing_type`, price, category,
and condition fields continue to work. Convert them with:

```sh
uv run dealsteal migrate store/item_queries/*.json --output profiles.v2.json
```

## Commands

```sh
uv run dealsteal validate
uv run dealsteal scan --jsonl
uv run dealsteal scan --deals-only
uv run dealsteal watch --interval 15m --jsonl
uv run dealsteal report --deals-only
```

The JSONL scanner prints page progress, enriched listings, qualified deals, and
a final statistics record. Deal results include landed acquisition cost, net
resale proceeds, net profit, ROI, and an auction maximum safe bid.

Listings with unknown destination shipping, import treatment, currency
conversion, absolute auction time, or listing type are retained with rejection
reasons but never qualify as deals. Shipping and import amounts are estimates
until checkout, so the output is a decision aid rather than a purchase promise.

For an auction ending window, the scanner ignores the search-card countdown when
deciding whether an item qualifies. It must find an absolute ISO timestamp on the
public item page and compares that UTC timestamp with the current clock. A
countdown can prioritize a detail fetch, but it cannot qualify an ending-window
deal. Detail cache lifetimes are adaptive: six hours for Buy It Now, 30 minutes
for distant auctions, five minutes inside 24 hours, and one minute inside one
hour. SQLite also keeps query cursors so a budget-interrupted watch pass can
resume without starting the interrupted page over.

The scanner uses a stable user agent, per-host throttling, bounded concurrency,
cache, retry-after handling, and a host circuit breaker. It does not rotate
identities or attempt to bypass eBay access controls.
