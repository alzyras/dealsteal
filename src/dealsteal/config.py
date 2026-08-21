"""Versioned scanner configuration and legacy query compatibility."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import (
    Destination,
    Money,
    PriceTier,
    ProductProfile,
    ScannerConfig,
    decimal,
)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Iterable):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _groups(value: Any) -> tuple[tuple[str, ...], ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return ((value.strip(),),) if value.strip() else ()
    if not isinstance(value, Iterable):
        return ()
    result: list[tuple[str, ...]] = []
    for group in value:
        terms = _string_tuple(group)
        if terms:
            result.append(terms)
    return tuple(result)


def _money(value: Any, default_currency: str) -> Money:
    if isinstance(value, dict):
        amount = decimal(value.get("amount"))
        currency = str(value.get("currency", default_currency))
    else:
        amount = decimal(value)
        currency = default_currency
    if amount is None:
        raise ValueError("reference price must contain a numeric amount")
    return Money(amount, currency)


def _tier(data: dict[str, Any], default_currency: str, index: int) -> PriceTier:
    price = data.get("reference_price", data.get("average_price"))
    return PriceTier(
        tier_id=str(data.get("id", data.get("tier_id", f"tier-{index + 1}"))),
        reference_price=_money(price, default_currency),
        required=_groups(data.get("required", data.get("match", {}).get("required"))),
        excluded=_string_tuple(
            data.get("excluded", data.get("match", {}).get("excluded"))
        ),
        conditions=_string_tuple(data.get("conditions", data.get("condition"))),
    )


def profile_from_dict(data: dict[str, Any], index: int = 0) -> ProductProfile:
    """Parse version 2 profiles and the repository's legacy query shape."""
    match = data.get("match") if isinstance(data.get("match"), dict) else {}
    keywords = data.get("search_terms", data.get("keywords"))
    if isinstance(keywords, dict):
        keywords = keywords.get("terms")
    search_terms = _string_tuple(keywords)
    if not search_terms:
        raise ValueError("profile requires search_terms or keywords")

    listing_types = tuple(
        value.lower().replace("-", "_")
        for value in _string_tuple(data.get("listing_types", data.get("listing_type")))
    ) or ("auction", "buy_it_now")
    aliases = {
        "bin": "buy_it_now",
        "fixed_price": "buy_it_now",
        "fixed-price": "buy_it_now",
    }
    listing_types = tuple(aliases.get(value, value) for value in listing_types)
    invalid = set(listing_types) - {"auction", "buy_it_now"}
    if invalid:
        raise ValueError(f"unsupported listing types: {sorted(invalid)}")

    required = _groups(data.get("required", match.get("required")))
    excluded = _string_tuple(data.get("excluded", match.get("excluded")))
    tiers_data = data.get("tiers", data.get("price_tiers", []))
    if isinstance(tiers_data, dict):
        tiers_data = [tiers_data]
    tiers = tuple(
        _tier(item, str(data.get("currency", "EUR")), tier_index)
        for tier_index, item in enumerate(tiers_data or [])
        if isinstance(item, dict)
    )
    max_time = data.get("max_time_remaining_seconds")
    if max_time is None and data.get("max_time_remaining_hours") is not None:
        max_time = int(float(data["max_time_remaining_hours"]) * 3600)

    return ProductProfile(
        profile_id=str(data.get("id", data.get("profile_id", f"profile-{index + 1}"))),
        search_terms=search_terms,
        listing_types=listing_types,
        marketplaces=tuple(
            value.upper()
            for value in _string_tuple(data.get("marketplaces", data.get("countries")))
        ),
        required=required,
        excluded=excluded,
        category_ids=_string_tuple(data.get("category_ids")),
        condition_ids=_string_tuple(data.get("condition_ids")),
        tiers=tiers,
        max_time_remaining_seconds=int(max_time) if max_time is not None else None,
        max_pages=int(data["max_pages"]) if data.get("max_pages") is not None else None,
    )


def load_profiles(paths: Iterable[str | Path]) -> list[ProductProfile]:
    profiles: list[ProductProfile] = []
    for path in paths:
        file_path = Path(path)
        with file_path.open(encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict) and isinstance(data.get("products"), list):
            values = data["products"]
        elif isinstance(data, list):
            values = data
        else:
            values = [data]
        for value in values:
            if isinstance(value, dict):
                profiles.append(profile_from_dict(value, len(profiles)))
    return profiles


def _config_decimal(data: dict[str, Any], key: str, default: str) -> Any:
    value = decimal(data.get(key), decimal(default))
    if value is None:
        raise ValueError(f"config.{key} must be numeric")
    return value


def config_from_dict(data: dict[str, Any]) -> ScannerConfig:
    destination_data = data.get("destination", {})
    destination = Destination(
        country=str(
            destination_data.get("country", data.get("destination_country", "LT"))
        ),
        postal_code=str(
            destination_data.get(
                "postal_code", data.get("destination_postal_code", "01100")
            )
        ),
        currency=str(
            destination_data.get("currency", data.get("reporting_currency", "EUR"))
        ),
    )
    costs = data.get("costs", {}) if isinstance(data.get("costs", {}), dict) else {}
    skelbiu = (
        data.get("skelbiu_api", {})
        if isinstance(data.get("skelbiu_api", {}), dict)
        else {}
    )
    max_time = data.get("max_time_remaining_seconds")
    if max_time is None and data.get("max_time_remaining_hours") is not None:
        max_time = int(float(data["max_time_remaining_hours"]) * 3600)
    config = ScannerConfig(
        destination=destination,
        allowed_origin_zones=tuple(
            value.upper()
            for value in _string_tuple(data.get("allowed_origin_zones", ["EU"]))
        ),
        reporting_currency=str(
            data.get("reporting_currency", destination.currency)
        ).upper(),
        minimum_net_roi=_config_decimal(data, "minimum_net_roi", "0.40"),
        selling_fee_rate=_config_decimal(costs, "selling_fee_rate", "0"),
        purchase_overhead=_config_decimal(costs, "purchase_overhead", "0"),
        resale_overhead=_config_decimal(costs, "resale_overhead", "0"),
        repair_reserve=_config_decimal(costs, "repair_reserve", "0"),
        risk_reserve=_config_decimal(costs, "risk_reserve", "0"),
        fx_buffer_rate=_config_decimal(costs, "fx_buffer_rate", "0.02"),
        database_path=str(data.get("database_path", "store/dealsteal.sqlite3")),
        search_cache_seconds=int(data.get("search_cache_seconds", 600)),
        watch_interval_seconds=int(data.get("watch_interval_seconds", 900)),
        request_budget=int(data.get("request_budget", 500)),
        max_pages=int(data.get("max_pages", 2)),
        max_concurrency=int(data.get("max_concurrency", 4)),
        per_host_interval=float(data.get("per_host_interval", 2.0)),
        request_timeout=float(data.get("request_timeout", 20.0)),
        max_rate_age_days=int(data.get("max_rate_age_days", 7)),
        max_time_remaining_seconds=(int(max_time) if max_time is not None else None),
        marketplaces=tuple(
            value.upper() for value in _string_tuple(data.get("marketplaces"))
        ),
        skelbiu_api_enabled=bool(
            skelbiu.get(
                "enabled",
                os.getenv("SKELBIU_API_ENABLED", "").casefold() in {"1", "true", "yes"},
            )
        ),
        skelbiu_api_base_url=str(
            skelbiu.get(
                "base_url", os.getenv("SKELBIU_API_URL", "http://127.0.0.1:8080")
            )
        ).rstrip("/"),
        skelbiu_api_timeout=float(skelbiu.get("timeout", 20.0)),
        skelbiu_search_limit=int(skelbiu.get("search_limit", 50)),
        skelbiu_detail_limit=int(skelbiu.get("detail_limit", 25)),
    )
    if config.search_cache_seconds < 1 or config.watch_interval_seconds < 1:
        raise ValueError("cache and watch intervals must be positive")
    if config.max_concurrency < 1 or config.per_host_interval < 0:
        raise ValueError("concurrency must be positive and host interval non-negative")
    return config


def load_config(path: str | Path | None = None) -> ScannerConfig:
    selected = Path(path or os.getenv("DEALSTEAL_CONFIG", "config.local.json"))
    if not selected.exists():
        return config_from_dict({})
    with selected.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("scanner config must be a JSON object")
    return config_from_dict(data)


def migrate_legacy_queries(paths: Iterable[str | Path]) -> dict[str, Any]:
    """Return a version 2 discovery profile document for old query files."""
    profiles = load_profiles(paths)
    return {
        "version": 2,
        "products": [profile.as_dict() for profile in profiles],
        "note": "Add tiers with reference_price values before expecting qualified deals.",
    }
