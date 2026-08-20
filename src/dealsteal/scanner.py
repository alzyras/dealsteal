"""High-volume, browserless eBay discovery and detail enrichment."""

from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import requests

from .config import load_config
from .detail import parse_detail_html
from .locales import Marketplace, normalize_country, origin_zone, resolve_marketplaces
from .matching import explain_match, matching_tier
from .models import Listing, Money, ProductProfile, ScannerConfig
from .scoring import DealScorer, ExchangeRates
from .storage import SQLiteStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchedPage:
    url: str
    status: int
    body: bytes
    headers: dict[str, str]


@dataclass
class ScanStats:
    requested: int = 0
    cache_hits: int = 0
    challenges: int = 0
    candidates: int = 0
    enriched: int = 0
    qualified: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "cache_hits": self.cache_hits,
            "challenges": self.challenges,
            "candidates": self.candidates,
            "enriched": self.enriched,
            "qualified": self.qualified,
            "rejected": dict(self.rejected),
        }


@dataclass(frozen=True)
class SearchJob:
    term: str
    listing_type: str
    marketplace: Marketplace
    category_ids: tuple[str, ...]
    condition_ids: tuple[str, ...]
    max_pages: int


class RateLimitedHttpClient:
    """Per-host throttled public HTTP client with conservative circuit breaking."""

    def __init__(self, config: ScannerConfig, store: SQLiteStore | None = None) -> None:
        self.config = config
        self.store = store
        self._local = threading.local()
        self._locks: dict[str, threading.Lock] = {}
        self._state_lock = threading.Lock()
        self._last_request: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._cache: dict[str, tuple[float, FetchedPage]] = {}
        self.stats = ScanStats()

    def _session(self, marketplace: Marketplace) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                }
            )
            self._local.session = session
        session.headers[
            "Accept-Language"
        ] = f"{marketplace.locale},{marketplace.locale.split('-')[0]};q=0.8,en;q=0.5"
        return session

    def _lock_for(self, host: str) -> threading.Lock:
        with self._state_lock:
            return self._locks.setdefault(host, threading.Lock())

    @staticmethod
    def _cache_key(url: str, params: dict[str, str] | None) -> str:
        query = urlencode(sorted((params or {}).items()))
        return hashlib.sha256(f"{url}?{query}".encode()).hexdigest()

    def get(
        self,
        marketplace: Marketplace,
        url: str,
        params: dict[str, str] | None = None,
        cache_seconds: int = 0,
    ) -> FetchedPage | None:
        key = self._cache_key(url, params)
        now = time.monotonic()
        with self._state_lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] <= cache_seconds:
                self.stats.cache_hits += 1
                return cached[1]
            if self.store and cache_seconds:
                cached_body = self.store.get_cached(key, cache_seconds)
                if cached_body is not None:
                    page = FetchedPage(url, 200, cached_body, {})
                    self._cache[key] = (now, page)
                    self.stats.cache_hits += 1
                    return page
            cooldown = self._cooldown_until.get(marketplace.host, 0)
            if cooldown > now:
                self.stats.challenges += 1
                return None
        lock = self._lock_for(marketplace.host)

        with lock:
            for attempt in range(3):
                with self._state_lock:
                    remaining = self.config.per_host_interval - (
                        time.monotonic() - self._last_request.get(marketplace.host, 0)
                    )
                if remaining > 0:
                    time.sleep(remaining + random.uniform(0, 0.5))
                try:
                    response = self._session(marketplace).get(
                        url, params=params, timeout=self.config.request_timeout
                    )
                    with self._state_lock:
                        self._last_request[marketplace.host] = time.monotonic()
                        self.stats.requested += 1
                except requests.RequestException as error:
                    LOGGER.warning("Request failed for %s: %s", marketplace.host, error)
                    if attempt == 2:
                        return None
                    time.sleep(2 ** attempt)
                    continue

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "2")
                    try:
                        delay = min(60.0, max(2.0, float(retry_after)))
                    except ValueError:
                        delay = 2.0
                    if attempt == 2:
                        self._trip(marketplace.host, challenge=False)
                        return None
                    time.sleep(delay)
                    continue
                if response.status_code == 403 or self._is_challenge(response):
                    self._trip(marketplace.host, challenge=True)
                    return None
                if response.status_code >= 500 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                if response.status_code != 200:
                    return None

                page = FetchedPage(
                    response.url,
                    response.status_code,
                    response.content,
                    dict(response.headers),
                )
                with self._state_lock:
                    self._cache[key] = (time.monotonic(), page)
                    self._failures[marketplace.host] = 0
                if self.store and cache_seconds:
                    self.store.put_cached(
                        key,
                        response.content,
                        response.status_code,
                        response.headers.get("Content-Type", "text/html"),
                    )
                return page
        return None

    def _trip(self, host: str, challenge: bool) -> None:
        with self._state_lock:
            self._failures[host] = self._failures.get(host, 0) + 1
            if challenge or self._failures[host] >= 2:
                self._cooldown_until[host] = time.monotonic() + 1800
                if challenge:
                    self.stats.challenges += 1

    @staticmethod
    def _is_challenge(response: requests.Response) -> bool:
        text = response.text[:5000].lower()
        return (
            "/splashui/" in response.url
            or "pardon our interruption" in text
            or "error page | ebay" in text
        )


def parse_card_price(text: str, currency: str) -> Money | None:
    """Parse common localized card prices without assuming the marketplace locale."""
    if not text:
        return None
    value = re.sub(r"[^0-9,.\-]", "", text)
    if not value:
        return None
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        tail = value.rsplit(",", 1)[1]
        value = value.replace(",", "." if len(tail) <= 2 else "")
    elif value.count(".") > 1:
        value = value.replace(".", "")
    try:
        return Money(Decimal(value), currency)
    except (ArithmeticError, ValueError):
        return None


class MarketplaceScanner:
    def __init__(
        self,
        config: ScannerConfig | None = None,
        store: SQLiteStore | None = None,
        rates: ExchangeRates | None = None,
    ) -> None:
        self.config = config or load_config()
        self.store = store or SQLiteStore(self.config.database_path)
        self.http = RateLimitedHttpClient(self.config, self.store)
        self.rates = rates or self._load_rates()
        self.scorer = DealScorer(self.config, self.rates)
        self._parser = _parser()

    def _load_rates(self) -> ExchangeRates:
        try:
            return ExchangeRates.from_ecb(timeout=self.config.request_timeout)
        except Exception as error:  # noqa: BLE001 - scan remains useful for EUR-only data
            LOGGER.warning("Could not load ECB rates: %s", error)
            return ExchangeRates(
                {"EUR": Decimal("1"), self.config.reporting_currency: Decimal("1")}
            )

    def _jobs(self, profiles: list[ProductProfile]) -> list[SearchJob]:
        jobs: dict[tuple[Any, ...], SearchJob] = {}
        for profile in profiles:
            sites = resolve_marketplaces(
                profile.marketplaces or self.config.marketplaces
            )
            for term in profile.search_terms:
                for listing_type in profile.listing_types:
                    for site in sites:
                        key = (
                            term,
                            listing_type,
                            site.code,
                            profile.category_ids,
                            profile.condition_ids,
                        )
                        jobs[key] = SearchJob(
                            term,
                            listing_type,
                            site,
                            profile.category_ids,
                            profile.condition_ids,
                            profile.max_pages or self.config.max_pages,
                        )
        return list(jobs.values())

    def _search_job(self, job: SearchJob) -> list[Listing]:
        results: list[Listing] = []
        seen: set[str] = set()
        for page_number in range(1, job.max_pages + 1):
            params = {
                "_nkw": job.term,
                "_ipg": "120",
                "_pgn": str(page_number),
                "_sacat": job.category_ids[0] if job.category_ids else "0",
                "_stpos": self.config.destination.postal_code,
                "LH_BIN" if job.listing_type == "buy_it_now" else "LH_Auction": "1",
                "_sop": "1",
            }
            if job.condition_ids:
                params["LH_ItemCondition"] = "|".join(job.condition_ids)
            page = self.http.get(
                job.marketplace,
                f"https://{job.marketplace.host}/sch/i.html",
                params,
                self.config.search_cache_seconds,
            )
            if page is None:
                break
            raw_items = self._parser._extract_items(
                page.body.decode("utf-8", "replace")
            )
            if not raw_items:
                break
            new_on_page = 0
            for raw in raw_items:
                item_id = str(raw.get("item_id", "")).strip()
                title = str(raw.get("title", "")).strip()
                if not item_id or not title or title.casefold() == "shop on ebay":
                    continue
                if item_id in seen:
                    continue
                seen.add(item_id)
                price = parse_card_price(
                    str(raw.get("price_text", "")), job.marketplace.currency
                )
                location = _card_location(raw.get("attribute_rows", []))
                results.append(
                    Listing(
                        item_id=item_id,
                        title=title,
                        url=str(
                            raw.get("url")
                            or f"https://{job.marketplace.host}/itm/{item_id}"
                        ),
                        marketplace=job.marketplace.code,
                        host=job.marketplace.host,
                        listing_type=job.listing_type,
                        price=price,
                        origin_country=normalize_country(location),
                        origin_zone=origin_zone(normalize_country(location)),
                        condition=str(raw.get("condition_text") or "") or None,
                        bid_count=_bid_count(raw.get("attribute_rows", [])),
                    )
                )
                new_on_page += 1
            if new_on_page == 0:
                break
        return results

    def _enrich(self, listing: Listing, marketplace: Marketplace) -> Listing | None:
        ttl = 21600 if listing.listing_type == "buy_it_now" else 1800
        page = self.http.get(marketplace, listing.url, cache_seconds=ttl)
        if page is None:
            listing.rejection_reasons.append("detail_request_failed_or_challenged")
            return listing
        data = parse_detail_html(
            page.body.decode("utf-8", "replace"), self.config.destination.country
        )
        listing.detail_verified = True
        listing.fetched_at = datetime.now(UTC)
        if data.price:
            listing.price = data.price
        if data.end_time:
            listing.end_time = data.end_time
        if data.origin_country:
            listing.origin_country = data.origin_country
        listing.origin_zone = origin_zone(listing.origin_country)
        listing.ship_to_country = data.ship_to_country
        listing.ship_to_postal_code = data.ship_to_postal_code
        listing.shipping = data.shipping
        listing.shipping_known = data.shipping_known
        listing.import_duty = data.import_duty
        listing.import_vat = data.import_vat
        listing.import_cost_known = data.import_cost_known
        if listing.origin_zone == "EU" and self.config.destination.country in {
            "AT",
            "BE",
            "BG",
            "HR",
            "CY",
            "CZ",
            "DE",
            "DK",
            "EE",
            "ES",
            "FI",
            "FR",
            "GR",
            "HU",
            "IE",
            "IT",
            "LT",
            "LU",
            "LV",
            "MT",
            "NL",
            "PL",
            "PT",
            "RO",
            "SE",
            "SI",
            "SK",
        }:
            currency = (
                listing.price.currency
                if listing.price
                else self.config.reporting_currency
            )
            listing.import_duty = Money(Decimal("0"), currency)
            listing.import_vat = Money(Decimal("0"), currency)
            listing.import_cost_known = True
        if data.listing_type:
            listing.listing_type_verified = data.listing_type == listing.listing_type
            listing.listing_type = data.listing_type
        if listing.ship_to_country != self.config.destination.country:
            listing.rejection_reasons.append("destination_country_not_verified")
        if (
            listing.ship_to_postal_code
            and listing.ship_to_postal_code != self.config.destination.postal_code
        ):
            listing.rejection_reasons.append("destination_postal_code_not_verified")
        if not listing.shipping_known:
            listing.rejection_reasons.append("destination_shipping_unknown")
        if listing.end_time is None and listing.listing_type == "auction":
            listing.rejection_reasons.append("absolute_end_time_unknown")
        return listing

    def scan(
        self,
        profiles: list[ProductProfile],
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        scan_id = self.store.start_scan()
        stats = self.http.stats
        candidates: dict[str, Listing] = {}
        jobs = self._jobs(profiles)

        def report(event: dict[str, Any]) -> None:
            if emit:
                emit(event)

        with ThreadPoolExecutor(max_workers=self.config.max_concurrency) as executor:
            futures = {executor.submit(self._search_job, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    rows = future.result()
                except Exception as error:  # noqa: BLE001 - one marketplace must not abort all scans
                    LOGGER.exception("Search job failed for %s", job.marketplace.code)
                    report(
                        {
                            "event": "search_error",
                            "marketplace": job.marketplace.code,
                            "error": str(error),
                        }
                    )
                    continue
                for row in rows:
                    candidates.setdefault(row.item_id, row)
                report(
                    {
                        "event": "page_complete",
                        "marketplace": job.marketplace.code,
                        "listing_type": job.listing_type,
                        "term": job.term,
                        "candidates": len(rows),
                    }
                )
        stats.candidates = len(candidates)

        deals: list[dict[str, Any]] = []
        discovered: list[dict[str, Any]] = []
        for listing in candidates.values():
            if stats.requested >= self.config.request_budget:
                stats.reject("request_budget_exhausted")
                break
            matching_profiles: list[tuple[ProductProfile, Any]] = []
            for profile in profiles:
                matched, reason = explain_match(
                    profile, listing.title, listing.condition
                )
                if not matched:
                    stats.reject(reason)
                    continue
                tier = matching_tier(profile, listing.title, listing.condition)
                matching_profiles.append((profile, tier))
            if not matching_profiles:
                continue
            marketplace = next(
                (item for item in resolve_marketplaces((listing.marketplace,))), None
            )
            if marketplace is None:
                continue
            enriched = self._enrich(listing, marketplace)
            if enriched is None:
                continue
            stats.enriched += 1
            self.store.save_listing(enriched)
            discovered.append(enriched.as_dict())
            report({"event": "listing_enriched", "listing": enriched.as_dict()})
            for profile, tier in matching_profiles:
                if tier is None:
                    continue
                result = self.scorer.score(enriched, profile, tier)
                if result.qualified:
                    stats.qualified += 1
                    deal = result.as_dict()
                    deals.append(deal)
                    self.store.save_deal(deal)
                    report({"event": "deal", "deal": deal})
                else:
                    for reason in result.reasons:
                        stats.reject(reason)

        status = (
            "complete" if stats.requested < self.config.request_budget else "partial"
        )
        self.store.finish_scan(scan_id, status, stats.as_dict())
        return {
            "scan_id": scan_id,
            "status": status,
            "deals": deals,
            "listings": discovered,
            "stats": stats.as_dict(),
        }


def _parser() -> Any:
    # Importing the old card parser does not create a network session or perform I/O.
    from .ebay import EbayAuctionSearcher

    return EbayAuctionSearcher(min_request_interval=0, max_pages=1)


def _card_location(rows: list[str]) -> str | None:
    for row in rows:
        value = re.sub(
            r"^(?:located in|from|aus|versand aus|envoye depuis|expedie depuis|situe en|ubicado en|aus)\s+",
            "",
            str(row),
            flags=re.IGNORECASE,
        ).strip()
        if value != str(row).strip():
            return value
    return None


def _bid_count(rows: list[str]) -> int:
    for row in rows:
        match = re.search(
            r"(\d+)\s+(?:bids?|gebote?|offres?|ofertas?|offerte?)\b",
            str(row),
            re.IGNORECASE,
        )
        if match:
            return int(match.group(1))
    return 0
