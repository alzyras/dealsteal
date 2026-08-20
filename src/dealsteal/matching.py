"""Explainable product-profile matching."""

from __future__ import annotations

import re
import unicodedata

from .models import PriceTier, ProductProfile


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold().replace("+", " plus ")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _contains(text: str, term: str) -> bool:
    normalized = normalize_text(term)
    if not normalized:
        return False
    return normalized in text


def _groups_match(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    return all(any(_contains(text, term) for term in group) for group in groups)


def _tier_matches(text: str, condition: str | None, tier: PriceTier) -> bool:
    if not _groups_match(text, tier.required):
        return False
    if any(_contains(text, term) for term in tier.excluded):
        return False
    if tier.conditions:
        condition_text = normalize_text(condition)
        if not any(_contains(condition_text, item) for item in tier.conditions):
            return False
    return True


def matching_tier(
    profile: ProductProfile, title: str, condition: str | None
) -> PriceTier | None:
    """Return the only matching tier; ambiguity is intentionally rejected."""
    text = normalize_text(title)
    if not _groups_match(text, profile.required):
        return None
    if any(_contains(text, term) for term in profile.excluded):
        return None
    matches = [tier for tier in profile.tiers if _tier_matches(text, condition, tier)]
    return matches[0] if len(matches) == 1 else None


def explain_match(
    profile: ProductProfile, title: str, condition: str | None
) -> tuple[bool, str]:
    text = normalize_text(title)
    if not _groups_match(text, profile.required):
        return False, "required_terms_missing"
    if any(_contains(text, term) for term in profile.excluded):
        return False, "excluded_term_present"
    if not profile.tiers:
        return True, "discovery_only"
    matching = [tier for tier in profile.tiers if _tier_matches(text, condition, tier)]
    if len(matching) == 0:
        return False, "no_price_tier_matches"
    if len(matching) > 1:
        return False, "price_tier_ambiguous"
    return True, "matched"
