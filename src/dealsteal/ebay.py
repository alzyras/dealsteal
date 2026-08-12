"""Direct, unauthenticated eBay auction search.

The official Browse API requires an application token.  This module instead
uses the public, server-rendered search page so a searcher can run without an
eBay application id, OAuth token, browser automation, or a signed-in session.
It deliberately uses one stable user agent, a persistent session, bounded
pagination, and Retry-After-aware backoff.  It does not rotate identities or
try to defeat eBay's access controls.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import requests

LOGGER = logging.getLogger(__name__)


class _SearchPageParser(HTMLParser):
    """Extract listing cards from eBay's server-rendered search page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.list_depth = 0
        self.tag_depth = 0
        self.capture: dict[str, Any] | None = None
        self.capture_ignore_depth = 0
        self.attribute_start_depth: int | None = None
        self.attribute_buffer: list[str] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    @staticmethod
    def _classes(attrs: dict[str, str]) -> set[str]:
        return set(attrs.get("class", "").split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag_depth += 1
        parsed_attrs = self._attrs(attrs)
        classes = self._classes(parsed_attrs)

        if tag == "li" and parsed_attrs.get("data-listingid"):
            if self.current is not None:
                self._finish_item()
            self.current = {
                "item_id": parsed_attrs["data-listingid"],
                "title": "",
                "url": "",
                "price_text": "",
                "time_left": "",
                "time_end_text": "",
                "gallery_url": "",
                "condition_text": "",
                "attribute_rows": [],
            }
            self.list_depth = 1
            return

        if self.current is None:
            return

        if tag == "li":
            self.list_depth += 1

        if (
            tag == "a"
            and "s-card__link" in classes
            and parsed_attrs.get("href")
            and not self.current["url"]
        ):
            self.current["url"] = parsed_attrs["href"]

        if tag == "img" and parsed_attrs.get("src") and not self.current["gallery_url"]:
            self.current["gallery_url"] = parsed_attrs["src"]

        if self.attribute_start_depth is not None:
            self.attribute_buffer.append("")
        elif tag == "div" and "s-card__attribute-row" in classes:
            self.attribute_start_depth = self.tag_depth
            self.attribute_buffer = []

        if self.capture is not None and "clipped" in classes:
            self.capture_ignore_depth += 1

        capture_kind = None
        if "s-card__title" in classes and tag in {"div", "span"}:
            capture_kind = "title"
        elif "s-card__price" in classes:
            capture_kind = "price_text"
        elif "s-card__time-left" in classes:
            capture_kind = "time_left"
        elif "s-card__time-end" in classes:
            capture_kind = "time_end_text"
        elif "s-card__subtitle" in classes:
            capture_kind = "condition_text"

        if capture_kind is not None and self.capture is None:
            self.capture = {
                "kind": capture_kind,
                "tag_depth": self.tag_depth,
                "buffer": [],
            }

    def handle_endtag(self, tag: str) -> None:
        if self.current is not None:
            if self.capture is not None and self.tag_depth == self.capture["tag_depth"]:
                value = " ".join(" ".join(self.capture["buffer"]).split())
                self.current[self.capture["kind"]] = value
                self.capture = None
                self.capture_ignore_depth = 0

            if (
                self.attribute_start_depth is not None
                and self.tag_depth == self.attribute_start_depth
            ):
                value = " ".join(" ".join(self.attribute_buffer).split())
                if value:
                    self.current["attribute_rows"].append(value)
                self.attribute_start_depth = None
                self.attribute_buffer = []

            if tag == "li":
                if self.list_depth == 1:
                    self._finish_item()
                    self.list_depth = 0
                elif self.list_depth > 1:
                    self.list_depth -= 1

        if self.capture_ignore_depth and self.capture is not None:
            self.capture_ignore_depth -= 1
        self.tag_depth = max(0, self.tag_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        if self.attribute_start_depth is not None:
            self.attribute_buffer.append(data)
        if self.capture is not None and self.capture_ignore_depth == 0:
            self.capture["buffer"].append(data)

    def _finish_item(self) -> None:
        if self.current is not None and self.current.get("item_id"):
            self.items.append(self.current)
        self.current = None
        self.capture = None
        self.attribute_start_depth = None
        self.attribute_buffer = []

    def close(self) -> None:
        super().close()
        self._finish_item()


class EbayAuctionSearcher:
    """Search public eBay listings without API credentials."""

    SEARCH_PATH = "/sch/i.html"
    WARMUP_PATH = "/sch/ebayadvsearch/?_sofindtype=0"
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    EUROPEAN_COUNTRIES = ["GB", "DE", "FR", "IT", "ES"]
    SITE_DOMAINS = {
        "US": "www.ebay.com",
        "CA": "www.ebay.ca",
        "GB": "www.ebay.co.uk",
        "AU": "www.ebay.com.au",
        "AT": "www.ebay.at",
        "BE": "www.ebay.be",
        "CH": "www.ebay.ch",
        "DE": "www.ebay.de",
        "ES": "www.ebay.es",
        "FR": "www.ebay.fr",
        "IE": "www.ebay.ie",
        "IT": "www.ebay.it",
        "NL": "www.ebay.nl",
        "PL": "www.ebay.pl",
    }
    CURRENCY_BY_COUNTRY = {
        "US": "USD",
        "CA": "CAD",
        "GB": "GBP",
        "AU": "AUD",
        "AT": "EUR",
        "BE": "EUR",
        "CH": "CHF",
        "DE": "EUR",
        "ES": "EUR",
        "FR": "EUR",
        "IE": "EUR",
        "IT": "EUR",
        "NL": "EUR",
        "PL": "PLN",
    }

    def __init__(
        self,
        app_id: str | None = None,
        cert_id: str | None = None,
        *,
        session: requests.Session | None = None,
        min_request_interval: float | None = None,
        max_pages: int | None = None,
        page_size: int | None = None,
        timeout: float | None = None,
    ) -> None:
        """Create a public searcher.

        ``app_id`` and ``cert_id`` are retained as ignored compatibility
        arguments so existing callers do not need to change at once.
        """
        del app_id, cert_id
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": os.getenv("EBAY_USER_AGENT", self.DEFAULT_USER_AGENT),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;" "q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": os.getenv("EBAY_ACCEPT_LANGUAGE", "en-US,en;q=0.9"),
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.min_request_interval = (
            float(os.getenv("EBAY_MIN_REQUEST_INTERVAL", "1.0"))
            if min_request_interval is None
            else max(0.0, min_request_interval)
        )
        self.max_pages = max(
            1,
            int(os.getenv("EBAY_MAX_PAGES", "2")) if max_pages is None else max_pages,
        )
        self.page_size = min(
            240,
            max(
                1,
                int(os.getenv("EBAY_PAGE_SIZE", "120"))
                if page_size is None
                else page_size,
            ),
        )
        self.timeout = (
            float(os.getenv("EBAY_REQUEST_TIMEOUT", "30"))
            if timeout is None
            else timeout
        )
        self._last_request_at = 0.0
        self._warmed_hosts: set[str] = set()

    def search_ebay_auctions(
        self,
        keywords: str,
        countries: list[str] | None = None,
        max_price: float | None = None,
        min_price: float | None = None,
        max_time_remaining: int | None = None,
        category_ids: list[str] | None = None,
        condition_ids: list[str] | None = None,
        sort_by_ending_soon: bool = True,
        include_auction_items: bool = True,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search one or more eBay sites and return normalized auction JSON.

        The return shape is intentionally compatible with the previous
        Browse/Finding API adapters.  Each requested site gets its own public
        search query, so ``countries`` is no longer silently truncated to one
        country.
        """
        if not keywords or not keywords.strip():
            return []

        countries_to_search = countries or self.EUROPEAN_COUNTRIES
        pages = max(1, max_pages or self.max_pages)
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for country in countries_to_search:
            country_code, host = self._resolve_site(country)
            if host is None:
                LOGGER.warning("Skipping unsupported eBay country/site: %s", country)
                continue

            for page in range(1, pages + 1):
                params = self._build_params(
                    keywords,
                    country_code,
                    max_price,
                    min_price,
                    category_ids,
                    condition_ids,
                    sort_by_ending_soon=sort_by_ending_soon,
                    include_auction_items=include_auction_items,
                    limit=self.page_size,
                    offset=(page - 1) * self.page_size,
                )
                response = self._get_search_page(host, params)
                if response is None:
                    break

                raw_items = self._extract_items(response.text)
                if not raw_items:
                    break

                page_results = 0
                for raw_item in raw_items:
                    item = self._format_public_item(raw_item, country_code, host)
                    if item is None:
                        continue
                    if not self._within_filters(
                        item, min_price, max_price, max_time_remaining
                    ):
                        continue
                    key = (country_code, item["item_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(item)
                    page_results += 1

                LOGGER.info(
                    "eBay %s page %s: parsed %s listings, kept %s auctions",
                    country_code,
                    page,
                    len(raw_items),
                    page_results,
                )

        results.sort(
            key=lambda item: self._time_string_to_seconds(item["time_remaining"])
        )
        return results

    def find_auction_deals(
        self,
        keywords: str,
        max_price: float | None = None,
        min_price: float | None = None,
        time_remaining_hours: int | float | None = None,
        countries: list[str] | None = None,
        category_ids: list[str] | None = None,
        condition_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Compatibility wrapper for the old ``find_auction_deals`` API."""
        max_seconds = (
            None
            if time_remaining_hours is None
            else int(float(time_remaining_hours) * 3600)
        )
        return self.search_ebay_auctions(
            keywords,
            countries=countries,
            max_price=max_price,
            min_price=min_price,
            max_time_remaining=max_seconds,
            category_ids=category_ids,
            condition_ids=condition_ids,
            **kwargs,
        )

    def check_api_status(self) -> tuple[bool, str]:
        """Report the credential-free public-search mode."""
        return True, "Public eBay search mode; no API token or login required"

    def _resolve_site(self, country_or_site: str) -> tuple[str, str | None]:
        value = str(country_or_site).strip()
        upper = value.upper()
        if upper in self.SITE_DOMAINS:
            return upper, self.SITE_DOMAINS[upper]
        if "." in value:
            host = urlsplit(value if "://" in value else f"https://{value}").netloc
            if host:
                return host.split(".")[1].upper() if host.count(".") > 1 else host, host
        return upper, None

    def _build_params(
        self,
        keywords: str,
        country: str,
        max_price: float | None,
        min_price: float | None,
        category_ids: list[str] | None,
        condition_ids: list[str] | None,
        sort_by_ending_soon: bool = True,
        include_auction_items: bool = True,
        limit: int = 120,
        offset: int = 0,
    ) -> dict[str, str]:
        """Build public-search query parameters from the old JSON fields."""
        del country
        params = {
            "_nkw": keywords,
            "_ipg": str(min(240, max(1, limit))),
            "_pgn": str((max(0, offset) // max(1, limit)) + 1),
            "_sacat": str(category_ids[0]) if category_ids else "0",
        }
        if include_auction_items:
            params["LH_Auction"] = "1"
        if sort_by_ending_soon:
            params["_sop"] = "1"
        if min_price is not None:
            params["_udlo"] = str(min_price)
        if max_price is not None:
            params["_udhi"] = str(max_price)
        if condition_ids:
            params["LH_ItemCondition"] = "|".join(str(value) for value in condition_ids)
        return params

    def _get_search_page(
        self, host: str, params: dict[str, str]
    ) -> requests.Response | None:
        url = f"https://{host}{self.SEARCH_PATH}"
        response = self._request("GET", url, params=params)
        if (
            response is not None
            and self._is_challenge(response)
            and host not in self._warmed_hosts
        ):
            LOGGER.info("Warming the public eBay session for %s", host)
            self._request("GET", f"https://{host}{self.WARMUP_PATH}")
            self._warmed_hosts.add(host)
            response = self._request("GET", url, params=params)
        if (
            response is None
            or response.status_code != 200
            or self._is_challenge(response)
        ):
            status = response.status_code if response is not None else "no response"
            LOGGER.warning("eBay search failed for %s with status %s", host, status)
            return None
        return response

    @staticmethod
    def _is_challenge(response: requests.Response) -> bool:
        """Recognize an access-control page without attempting to solve it."""
        return (
            response.status_code in {403, 429}
            or "/splashui/" in response.url
            or "Pardon Our Interruption" in response.text[:5000]
            or "<title>Error Page | eBay</title>" in response.text[:5000]
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        retries: int = 2,
    ) -> requests.Response | None:
        for attempt in range(retries + 1):
            wait = self.min_request_interval - (
                time.monotonic() - self._last_request_at
            )
            if wait > 0:
                time.sleep(wait)
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    timeout=self.timeout,
                )
                self._last_request_at = time.monotonic()
            except requests.RequestException as error:
                LOGGER.warning("eBay request failed (%s): %s", url, error)
                if attempt >= retries:
                    return None
                time.sleep(min(2 ** attempt, 8))
                continue

            if response.status_code == 429:
                if attempt >= retries:
                    return response
                retry_after = self._retry_after_seconds(response)
                LOGGER.warning("eBay rate limit response; waiting %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            if response.status_code >= 500 and attempt < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            return response
        return None

    @staticmethod
    def _retry_after_seconds(response: requests.Response) -> float:
        value = response.headers.get("Retry-After", "")
        try:
            return max(1.0, min(120.0, float(value)))
        except ValueError:
            return 2.0

    def _extract_items(self, data: str | dict[str, Any]) -> list[dict[str, Any]]:
        """Extract either public HTML cards or legacy Browse summaries."""
        if isinstance(data, dict):
            return data.get("itemSummaries", [])
        parser = _SearchPageParser()
        parser.feed(data)
        parser.close()
        return parser.items

    def _format_public_item(
        self, raw_item: dict[str, Any], country: str, host: str
    ) -> dict[str, Any] | None:
        time_left_text = raw_item.get("time_left", "")
        seconds = self._parse_time_left(time_left_text)
        if seconds is None:
            return None

        price_value, currency = self._parse_price(
            raw_item.get("price_text", ""), country
        )
        now = datetime.now(timezone.utc)
        end_time = now + timedelta(seconds=seconds)
        item_id = str(raw_item.get("item_id", "Unknown"))
        url = raw_item.get("url") or f"https://{host}/itm/{item_id}"
        rows = raw_item.get("attribute_rows", [])
        location = next(
            (
                re.sub(
                    r"^(?:Located in|from|aus|Versand aus|envoyé depuis|"
                    r"expédié depuis|situé en|ubicado en)\s+",
                    "",
                    row,
                    flags=re.IGNORECASE,
                ).strip()
                for row in rows
                if re.match(
                    r"^(?:Located in|from|aus|Versand aus|envoyé depuis|"
                    r"expédié depuis|situé en|ubicado en)\s+",
                    row,
                    re.IGNORECASE,
                )
            ),
            "Unknown",
        )
        bid_count = next(
            (
                int(match.group(1))
                for row in rows
                if (
                    match := re.search(
                        r"(\d+)\s+(?:bids?|gebote?|offres?|ofertas?|offerte?)\b",
                        row,
                        re.IGNORECASE,
                    )
                )
            ),
            0,
        )
        seller_user_id, feedback_score, feedback_percentage = self._seller_details(rows)

        return {
            "country": country,
            "title": raw_item.get("title") or "No title",
            "price": f"{price_value:.2f} {currency}",
            "time_remaining": str(timedelta(seconds=seconds)),
            "url": url,
            "category": "Unknown",
            "category_id": "Unknown",
            "item_id": item_id,
            "condition_id": "Unknown",
            "condition_display_name": raw_item.get("condition_text") or "Unknown",
            "listing_type": "Auction",
            "start_time": "Unknown",
            "end_time": end_time.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "seller_user_id": seller_user_id,
            "feedback_score": feedback_score,
            "feedback_percentage": feedback_percentage,
            "shipping_cost": self._shipping_cost(rows, currency),
            "location": location,
            "gallery_url": raw_item.get("gallery_url") or "No URL available",
            "bid_count": bid_count,
        }

    @staticmethod
    def _shipping_cost(rows: list[str], currency: str) -> str:
        shipping_words = (
            "delivery",
            "shipping",
            "postage",
            "versand",
            "lieferung",
            "livraison",
            "spedizione",
            "envío",
        )
        for row in rows:
            if any(word in row.lower() for word in shipping_words):
                return f"{row} ({currency})"
        return f"0.00 {currency}"

    @staticmethod
    def _seller_details(rows: list[str]) -> tuple[str, str, str]:
        for row in reversed(rows):
            match = re.match(r"\s*(\S+)\s+([\d.,]+)%[^()]*\(([^)]+)\)", row)
            if match:
                return match.group(1), match.group(3), f"{match.group(2)}%"
        return "Unknown", "Unknown", "Unknown"

    @staticmethod
    def _parse_price(text: str, country: str) -> tuple[float, str]:
        match = re.search(r"(?:[$£€]|CAD|AUD|EUR|GBP|CHF|PLN)?\s*([\d.,]+)", text)
        if not match:
            return 0.0, EbayAuctionSearcher.CURRENCY_BY_COUNTRY.get(country, "USD")
        number = match.group(1).replace(" ", "")
        if "," in number and "." in number:
            number = (
                number.replace(",", "")
                if number.rfind(".") > number.rfind(",")
                else number.replace(".", "").replace(",", ".")
            )
        elif "," in number:
            number = (
                number.replace(",", ".")
                if len(number.rsplit(",", 1)[-1]) == 2
                else number.replace(",", "")
            )
        try:
            value = float(number)
        except ValueError:
            value = 0.0
        symbol = text.strip()[:1]
        currency = {
            "$": EbayAuctionSearcher.CURRENCY_BY_COUNTRY.get(country, "USD"),
            "£": "GBP",
            "€": "EUR",
        }.get(
            symbol,
            EbayAuctionSearcher.CURRENCY_BY_COUNTRY.get(country, "USD"),
        )
        return value, currency

    @staticmethod
    def _within_filters(
        item: dict[str, Any],
        min_price: float | None,
        max_price: float | None,
        max_time_remaining: int | None,
    ) -> bool:
        price = float(str(item["price"]).split()[0])
        seconds = EbayAuctionSearcher._time_string_to_seconds(item["time_remaining"])
        return (
            (min_price is None or price >= min_price)
            and (max_price is None or price <= max_price)
            and (max_time_remaining is None or seconds <= max_time_remaining)
        )

    @staticmethod
    def _parse_time_left(text: str) -> int | None:
        if not text:
            return None
        matches = re.findall(
            r"(\d+)\s*(days?|d|tage?|t|jours?|jour|giorni?|giorno|días?|dias?|hours?|hrs?|h|stunden?|std|st|heures?|horas?|ore?|ora|minutes?|mins?|minuten?|minutos?|m|seconds?|secs?|sec|sekunden?|secondes?|secondi?|s)\b",
            text.lower(),
        )
        clock_match = re.search(r"(?:(\d+)\s+days?,\s*)?(\d+):(\d{2}):(\d{2})", text)
        if not matches and clock_match is None:
            return None
        units = {
            "d": 86400,
            "day": 86400,
            "days": 86400,
            "t": 86400,
            "tag": 86400,
            "tage": 86400,
            "jour": 86400,
            "jours": 86400,
            "giorno": 86400,
            "giorni": 86400,
            "día": 86400,
            "días": 86400,
            "dia": 86400,
            "dias": 86400,
            "h": 3600,
            "hr": 3600,
            "hrs": 3600,
            "hour": 3600,
            "hours": 3600,
            "st": 3600,
            "std": 3600,
            "stunde": 3600,
            "stunden": 3600,
            "heure": 3600,
            "heures": 3600,
            "hora": 3600,
            "horas": 3600,
            "ora": 3600,
            "ore": 3600,
            "m": 60,
            "min": 60,
            "mins": 60,
            "minute": 60,
            "minutes": 60,
            "minuten": 60,
            "minuto": 60,
            "minutos": 60,
            "s": 1,
            "sec": 1,
            "secs": 1,
            "second": 1,
            "seconds": 1,
            "sekunde": 1,
            "sekunden": 1,
            "seconde": 1,
            "secondes": 1,
            "secondi": 1,
        }
        seconds = sum(int(value) * units[unit] for value, unit in matches)
        if clock_match:
            days, hours, minutes, clock_seconds = (
                int(part or 0) for part in clock_match.groups()
            )
            seconds += days * 86400 + hours * 3600 + minutes * 60 + clock_seconds
        return seconds

    @staticmethod
    def _time_string_to_seconds(value: str) -> int:
        parsed = EbayAuctionSearcher._parse_time_left(value)
        if parsed is not None:
            return parsed
        try:
            return int(timedelta_from_string(value).total_seconds())
        except (TypeError, ValueError):
            return 999999999

    @staticmethod
    def _parse_end_time(end_time: str) -> datetime:
        value = end_time.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _format_item(item: dict[str, Any], time_remaining: timedelta) -> dict[str, Any]:
        """Keep the old helper available for callers with Browse-shaped data."""
        item_id = item.get("itemId", "Unknown")
        title = item.get("title", "No title")
        if isinstance(title, list):
            title = title[0] if title else "No title"
        return {
            "country": item.get("itemLocation", {}).get("country", "Unknown"),
            "title": title,
            "price": "0.00 USD",
            "time_remaining": str(time_remaining),
            "url": item.get("itemWebUrl", f"https://www.ebay.com/itm/{item_id}"),
            "category": "Unknown",
            "category_id": "Unknown",
            "item_id": item_id,
            "condition_id": "Unknown",
            "condition_display_name": "Unknown",
            "listing_type": "Auction",
            "start_time": "Unknown",
            "end_time": "Unknown",
            "seller_user_id": "Unknown",
            "feedback_score": "Unknown",
            "feedback_percentage": "Unknown",
            "shipping_cost": "0.00 USD",
            "location": "Unknown",
            "gallery_url": item.get("image", {}).get("imageUrl", "No URL available"),
        }

    @staticmethod
    def _map_condition_name_to_id(condition_name: str) -> str | None:
        mapping = {
            "new": "1000",
            "used": "3000",
            "refurbished": "2500",
            "for parts": "6000",
        }
        name = condition_name.lower().strip()
        return next((value for key, value in mapping.items() if key in name), None)


def timedelta_from_string(value: str) -> timedelta:
    """Parse the small subset of timedelta strings used by old callers."""
    match = re.fullmatch(r"(?:(\d+) days?, )?(\d+):(\d+):(\d+)", value.strip())
    if not match:
        raise ValueError(value)
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    searcher = EbayAuctionSearcher()
    LOGGER.info(searcher.search_ebay_auctions("gopro", max_time_remaining=28800))
