"""Landed-cost and net-ROI scoring."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import requests

from .models import DealResult, Listing, Money, PriceTier, ProductProfile, ScannerConfig

LOGGER = logging.getLogger(__name__)
ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


@dataclass(frozen=True)
class ExchangeRates:
    """Rates expressed as units of currency per EUR."""

    rates: dict[str, Decimal]
    as_of: str | None = None

    def convert(self, money: Money, currency: str) -> Money | None:
        target = currency.upper()
        if money.currency == target:
            return money
        source_rate = self.rates.get(money.currency)
        target_rate = self.rates.get(target)
        if source_rate is None or target_rate is None:
            return None
        amount_eur = money.amount / source_rate
        return Money(
            (amount_eur * target_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            target,
        )

    @classmethod
    def from_ecb(
        cls, session: requests.Session | None = None, timeout: float = 20.0
    ) -> ExchangeRates:
        client = session or requests.Session()
        response = client.get(ECB_DAILY_URL, timeout=timeout)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        rates = {"EUR": Decimal("1")}
        as_of = None
        for element in root.iter():
            if element.tag.endswith("Cube") and element.attrib.get("currency"):
                rates[element.attrib["currency"].upper()] = Decimal(
                    element.attrib["rate"]
                )
            as_of = element.attrib.get("time", as_of)
        return cls(rates=rates, as_of=as_of)


def _zero(currency: str) -> Money:
    return Money(Decimal("0"), currency)


class DealScorer:
    def __init__(
        self, config: ScannerConfig, rates: ExchangeRates | None = None
    ) -> None:
        self.config = config
        self.rates = rates or ExchangeRates(
            {config.reporting_currency: Decimal("1"), "EUR": Decimal("1")}
        )

    def _to_reporting(self, value: Money | None) -> Money | None:
        if value is None:
            return None
        return self.rates.convert(value, self.config.reporting_currency)

    def _rates_fresh_for(self, values: tuple[Money | None, ...]) -> bool:
        """Require a dated ECB snapshot whenever a currency conversion is used."""
        needs_conversion = any(
            value is not None and value.currency != self.config.reporting_currency
            for value in values
        )
        if not needs_conversion:
            return True
        if not self.rates.as_of:
            return False
        try:
            as_of = datetime.fromisoformat(self.rates.as_of).date()
        except ValueError:
            return False
        return (datetime.now(UTC).date() - as_of).days <= self.config.max_rate_age_days

    def score(
        self,
        listing: Listing,
        profile: ProductProfile,
        tier: PriceTier,
    ) -> DealResult:
        currency = self.config.reporting_currency
        reference = self._to_reporting(tier.reference_price)
        reasons: list[str] = []
        if reference is None:
            reasons.append("reference_currency_unavailable")
            reference = _zero(currency)
        price = self._to_reporting(listing.price)
        shipping = self._to_reporting(listing.shipping) if listing.shipping else None
        duty = (
            self._to_reporting(listing.import_duty)
            if listing.import_duty
            else _zero(currency)
        )
        vat = (
            self._to_reporting(listing.import_vat)
            if listing.import_vat
            else _zero(currency)
        )
        if not self._rates_fresh_for(
            (tier.reference_price, listing.price, listing.shipping)
        ):
            reasons.append("fx_rate_missing_or_stale")
        if price is None:
            reasons.append("price_unknown_or_currency_unavailable")
        if not listing.shipping_known or shipping is None:
            reasons.append("destination_shipping_unknown")
        if not listing.import_cost_known:
            reasons.append("import_cost_unknown")
        if not listing.detail_verified:
            reasons.append("item_detail_unverified")
        if not listing.listing_type_verified:
            reasons.append("listing_type_unverified")
        if listing.ship_to_country != self.config.destination.country:
            reasons.append("destination_not_verified")
        if listing.origin_zone not in self.config.allowed_origin_zones:
            reasons.append("seller_origin_not_allowed")
        time_limit = (
            profile.max_time_remaining_seconds
            if profile.max_time_remaining_seconds is not None
            else self.config.max_time_remaining_seconds
        )
        if time_limit is not None:
            if listing.listing_type != "auction":
                pass
            elif listing.end_time is None or listing.end_time_source != "item_page":
                reasons.append("absolute_end_time_unknown")
            else:
                remaining = (listing.end_time - datetime.now(UTC)).total_seconds()
                if remaining < 0 or remaining > time_limit:
                    reasons.append("auction_outside_time_window")

        if reasons:
            return DealResult(
                listing=listing,
                profile_id=profile.profile_id,
                tier_id=tier.tier_id,
                reference_price=reference,
                acquisition_cost=_zero(currency),
                net_resale_proceeds=_zero(currency),
                net_profit=_zero(currency),
                net_roi=Decimal("-1"),
                max_total_acquisition=_zero(currency),
                max_bid=None,
                qualified=False,
                reasons=tuple(dict.fromkeys(reasons)),
            )

        assert price is not None and shipping is not None
        base_cost = price.amount + shipping.amount + duty.amount + vat.amount
        fx_buffer = base_cost * self.config.fx_buffer_rate
        acquisition_amount = base_cost + self.config.purchase_overhead + fx_buffer
        proceeds_amount = (
            reference.amount * (Decimal("1") - self.config.selling_fee_rate)
            - self.config.resale_overhead
            - self.config.repair_reserve
            - self.config.risk_reserve
        )
        profit_amount = proceeds_amount - acquisition_amount
        roi = (
            profit_amount / acquisition_amount if acquisition_amount else Decimal("-1")
        )
        max_total_amount = proceeds_amount / (
            Decimal("1") + self.config.minimum_net_roi
        )
        # Solve the acquisition formula again for the bid.  The FX buffer is
        # proportional to the whole purchase, so subtracting the current
        # acquisition cost would understate the safe bid when the bid changes.
        fixed_cost = (
            shipping.amount + duty.amount + vat.amount + self.config.purchase_overhead
        )
        max_bid_amount = (max_total_amount - fixed_cost) / (
            Decimal("1") + self.config.fx_buffer_rate
        )
        max_bid = Money(
            max(Decimal("0"), max_bid_amount).quantize(Decimal("0.01")), currency
        )
        qualified = roi >= self.config.minimum_net_roi
        if not qualified:
            reasons.append("below_minimum_net_roi")
        return DealResult(
            listing=listing,
            profile_id=profile.profile_id,
            tier_id=tier.tier_id,
            reference_price=reference,
            acquisition_cost=Money(
                acquisition_amount.quantize(Decimal("0.01")), currency
            ),
            net_resale_proceeds=Money(
                proceeds_amount.quantize(Decimal("0.01")), currency
            ),
            net_profit=Money(profit_amount.quantize(Decimal("0.01")), currency),
            net_roi=roi.quantize(Decimal("0.0001")),
            max_total_acquisition=Money(
                max_total_amount.quantize(Decimal("0.01")), currency
            ),
            max_bid=max_bid if listing.listing_type == "auction" else None,
            qualified=qualified,
            reasons=tuple(dict.fromkeys(reasons)),
        )
