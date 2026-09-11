"""Estimate the commercial size of a fulfilment shortfall.

Every estimate produced here carries the formula that made it and the
assumptions it rests on. An impact figure without its basis invites being quoted
as a measured loss, which it is not: this is the revenue associated with demand
that went unfulfilled, not money that was demonstrably lost.

Two things in particular are not modelled, and both push the estimate high:
a customer denied one product may buy another, and unfulfilled demand may be
served later rather than lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ImpactEstimate:
    """A quantified estimate together with the reasoning behind it."""

    value: float
    basis: str
    components: dict[str, float]
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        """One line stating the figure and how it was derived."""
        return f"{self.value:,.2f} based on {self.basis}"


BASELINE_ASSUMPTIONS = (
    "Unfulfilled demand is valued at the price actually realised on fulfilled units.",
    "No substitution: a customer denied this product is assumed not to buy another.",
    "No recovery: demand served later is still counted as a shortfall now.",
    "This is an estimate of associated revenue, not a measured loss.",
)


def realised_selling_price(net_sales: float, units: float) -> float:
    """Average price actually achieved per unit sold.

    Uses realised net sales rather than list price, so discounting is already
    reflected and the estimate is not inflated by prices nobody paid.
    """
    if units <= 0:
        raise ValueError("Cannot derive a realised selling price without fulfilled units.")
    return float(net_sales) / float(units)


def estimate_fulfilment_shortfall(
    ordered_units: float,
    fulfilled_units: float,
    baseline_fill_rate: float,
    net_sales: float,
) -> ImpactEstimate:
    """Value the units a segment would have fulfilled at its previous fill rate.

    This isolates the impact of a *deterioration* rather than valuing all
    unfulfilled demand. A segment that has always fulfilled 90% of its orders is
    not losing money every day by failing to reach 100%; what is worth
    quantifying is the gap that opened up.

        shortfall = ordered_now x fill_rate_before - fulfilled_now
        impact    = shortfall x realised average selling price

    A negative shortfall means fulfilment improved, and the estimate is zero
    rather than a negative loss.
    """
    if ordered_units < 0 or fulfilled_units < 0:
        raise ValueError("Ordered and fulfilled units must not be negative.")
    if not 0.0 <= baseline_fill_rate <= 1.0:
        raise ValueError("baseline_fill_rate must lie between 0 and 1.")

    expected_fulfilled = float(ordered_units) * float(baseline_fill_rate)
    shortfall_units = max(0.0, expected_fulfilled - float(fulfilled_units))

    price = realised_selling_price(net_sales, fulfilled_units) if fulfilled_units > 0 else 0.0
    value = round(shortfall_units * price, 2)

    return ImpactEstimate(
        value=value,
        basis=(
            "units not fulfilled relative to the segment's prior fill rate, "
            "valued at its realised average selling price"
        ),
        components={
            "ordered_units": round(float(ordered_units), 4),
            "fulfilled_units": round(float(fulfilled_units), 4),
            "baseline_fill_rate": round(float(baseline_fill_rate), 4),
            "expected_fulfilled_units": round(expected_fulfilled, 4),
            "shortfall_units": round(shortfall_units, 4),
            "realised_selling_price": round(price, 4),
        },
        assumptions=BASELINE_ASSUMPTIONS,
    )


def estimate_unfulfilled_demand(
    ordered_units: float,
    fulfilled_units: float,
    net_sales: float,
) -> ImpactEstimate:
    """Value all demand a segment did not fulfil, against a perfect fill rate.

    Broader than the shortfall estimate and correspondingly weaker as evidence:
    it counts structural unfulfilment that has always been present, so it says
    what perfect fulfilment would be worth rather than what changed.
    """
    if ordered_units < 0 or fulfilled_units < 0:
        raise ValueError("Ordered and fulfilled units must not be negative.")

    unfulfilled_units = max(0.0, float(ordered_units) - float(fulfilled_units))
    price = realised_selling_price(net_sales, fulfilled_units) if fulfilled_units > 0 else 0.0
    value = round(unfulfilled_units * price, 2)

    return ImpactEstimate(
        value=value,
        basis="all unfulfilled units valued at the realised average selling price",
        components={
            "ordered_units": round(float(ordered_units), 4),
            "fulfilled_units": round(float(fulfilled_units), 4),
            "unfulfilled_units": round(unfulfilled_units, 4),
            "realised_selling_price": round(price, 4),
        },
        assumptions=BASELINE_ASSUMPTIONS
        + ("A perfect fill rate is treated as the reference, which no operation sustains.",),
    )
