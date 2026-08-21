"""Typed models shared by configuration, scanning, storage, and scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


def decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    """Convert JSON numeric values without introducing binary float error."""
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return default


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.amount.is_nan() or self.amount.is_infinite():
            raise ValueError("Money amount must be finite")
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError(f"Invalid ISO currency code: {self.currency!r}")
        object.__setattr__(self, "currency", self.currency.upper())

    def as_dict(self) -> dict[str, str]:
        return {"amount": str(self.amount), "currency": self.currency}


@dataclass(frozen=True)
class Destination:
    country: str
    postal_code: str
    currency: str = "EUR"

    def __post_init__(self) -> None:
        country = self.country.upper().strip()
        if len(country) != 2 or not country.isalpha():
            raise ValueError("destination.country must be an ISO-3166 alpha-2 code")
        if not self.postal_code.strip():
            raise ValueError("destination.postal_code is required")
        object.__setattr__(self, "country", country)
        object.__setattr__(self, "currency", self.currency.upper())

    def as_dict(self) -> dict[str, str]:
        return {
            "country": self.country,
            "postal_code": self.postal_code,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class PriceTier:
    tier_id: str
    reference_price: Money
    required: tuple[tuple[str, ...], ...] = ()
    excluded: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.tier_id,
            "reference_price": self.reference_price.as_dict(),
            "required": [list(group) for group in self.required],
            "excluded": list(self.excluded),
            "conditions": list(self.conditions),
        }


@dataclass(frozen=True)
class ProductProfile:
    profile_id: str
    search_terms: tuple[str, ...]
    listing_types: tuple[str, ...] = ("auction", "buy_it_now")
    marketplaces: tuple[str, ...] = ()
    required: tuple[tuple[str, ...], ...] = ()
    excluded: tuple[str, ...] = ()
    category_ids: tuple[str, ...] = ()
    condition_ids: tuple[str, ...] = ()
    tiers: tuple[PriceTier, ...] = ()
    max_time_remaining_seconds: int | None = None
    max_pages: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.profile_id,
            "search_terms": list(self.search_terms),
            "listing_types": list(self.listing_types),
            "marketplaces": list(self.marketplaces),
            "match": {
                "required": [list(group) for group in self.required],
                "excluded": list(self.excluded),
            },
            "category_ids": list(self.category_ids),
            "condition_ids": list(self.condition_ids),
            "tiers": [tier.as_dict() for tier in self.tiers],
            "max_time_remaining_seconds": self.max_time_remaining_seconds,
            "max_pages": self.max_pages,
        }


@dataclass(frozen=True)
class ScannerConfig:
    destination: Destination
    allowed_origin_zones: tuple[str, ...] = ("EU",)
    reporting_currency: str = "EUR"
    minimum_net_roi: Decimal = Decimal("0.40")
    selling_fee_rate: Decimal = Decimal("0")
    purchase_overhead: Decimal = Decimal("0")
    resale_overhead: Decimal = Decimal("0")
    repair_reserve: Decimal = Decimal("0")
    risk_reserve: Decimal = Decimal("0")
    fx_buffer_rate: Decimal = Decimal("0.02")
    database_path: str = "store/dealsteal.sqlite3"
    search_cache_seconds: int = 600
    watch_interval_seconds: int = 900
    request_budget: int = 500
    max_pages: int = 2
    max_concurrency: int = 4
    per_host_interval: float = 2.0
    request_timeout: float = 20.0
    max_rate_age_days: int = 7
    marketplaces: tuple[str, ...] = ()
    skelbiu_api_enabled: bool = False
    skelbiu_api_base_url: str = "http://127.0.0.1:8080"
    skelbiu_api_timeout: float = 20.0
    skelbiu_search_limit: int = 50
    skelbiu_detail_limit: int = 25

    def __post_init__(self) -> None:
        object.__setattr__(self, "reporting_currency", self.reporting_currency.upper())
        if not self.minimum_net_roi >= 0:
            raise ValueError("minimum_net_roi must be non-negative")
        if not 0 <= self.selling_fee_rate < 1:
            raise ValueError("selling_fee_rate must be between 0 and 1")
        if not 0 <= self.fx_buffer_rate < 1:
            raise ValueError("fx_buffer_rate must be between 0 and 1")
        if self.request_budget < 1 or self.max_pages < 1:
            raise ValueError("request_budget and max_pages must be positive")
        if self.skelbiu_api_timeout <= 0:
            raise ValueError("skelbiu_api_timeout must be positive")
        if self.skelbiu_search_limit < 1 or self.skelbiu_detail_limit < 1:
            raise ValueError("Skelbiu limits must be positive")


@dataclass
class Listing:
    item_id: str
    title: str
    url: str
    marketplace: str
    host: str
    listing_type: str
    price: Money | None = None
    shipping: Money | None = None
    import_duty: Money | None = None
    import_vat: Money | None = None
    origin_country: str | None = None
    origin_zone: str | None = None
    ship_to_country: str | None = None
    ship_to_postal_code: str | None = None
    condition: str | None = None
    end_time: datetime | None = None
    end_time_source: str | None = None
    fetched_at: datetime | None = None
    bid_count: int = 0
    detail_verified: bool = False
    shipping_known: bool = False
    import_cost_known: bool = False
    listing_type_verified: bool = False
    rejection_reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        def money(value: Money | None) -> dict[str, str] | None:
            return value.as_dict() if value else None

        return {
            "item_id": self.item_id,
            "title": self.title,
            "url": self.url,
            "marketplace": self.marketplace,
            "host": self.host,
            "listing_type": self.listing_type,
            "price": money(self.price),
            "shipping": money(self.shipping),
            "import_duty": money(self.import_duty),
            "import_vat": money(self.import_vat),
            "origin_country": self.origin_country,
            "origin_zone": self.origin_zone,
            "ship_to_country": self.ship_to_country,
            "ship_to_postal_code": self.ship_to_postal_code,
            "condition": self.condition,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "end_time_source": self.end_time_source,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "bid_count": self.bid_count,
            "detail_verified": self.detail_verified,
            "shipping_known": self.shipping_known,
            "import_cost_known": self.import_cost_known,
            "listing_type_verified": self.listing_type_verified,
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True)
class ResaleListing:
    """A live comparison listing returned by the configured resale API."""

    item_id: str
    title: str
    url: str
    price: Money
    status: str
    location: str | None = None
    updated_at: datetime | None = None
    fetched_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        # The local Skelbiu API resolves sold/removed pages itself.  Treat
        # anything other than its explicit active state as unsafe to compare.
        return self.status.casefold().strip() == "active"

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "url": self.url,
            "price": self.price.as_dict(),
            "status": self.status,
            "location": self.location,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


@dataclass(frozen=True)
class DealResult:
    listing: Listing
    profile_id: str
    tier_id: str
    reference_price: Money
    acquisition_cost: Money
    net_resale_proceeds: Money
    net_profit: Money
    net_roi: Decimal
    max_total_acquisition: Money
    max_bid: Money | None
    qualified: bool
    reasons: tuple[str, ...] = ()
    resale_average: Money | None = None
    resale_comparables: tuple[ResaleListing, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "tier_id": self.tier_id,
            "listing": self.listing.as_dict(),
            "reference_price": self.reference_price.as_dict(),
            "acquisition_cost": self.acquisition_cost.as_dict(),
            "net_resale_proceeds": self.net_resale_proceeds.as_dict(),
            "net_profit": self.net_profit.as_dict(),
            "net_roi": str(self.net_roi),
            "max_total_acquisition": self.max_total_acquisition.as_dict(),
            "max_bid": self.max_bid.as_dict() if self.max_bid else None,
            "qualified": self.qualified,
            "reasons": list(self.reasons),
            "resale_average": (
                self.resale_average.as_dict() if self.resale_average else None
            ),
            "resale_comparables": [item.as_dict() for item in self.resale_comparables],
        }
