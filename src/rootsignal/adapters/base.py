"""Map an external dataset onto the RootSignal business model.

A dataset that was not designed for this system will rarely carry everything it
expects. Real transaction data usually has sales and nothing else: no record of
what was ordered but never shipped, no stock positions, no plan.

The temptation is to fabricate the missing pieces so the whole pipeline runs.
That produces a system that appears to work and quietly answers questions the
data cannot answer — a fill rate derived from returns would read as a supply
failure when customers simply sent things back.

So an adapter declares what its dataset supports and what it does not, and the
analyses that depend on absent facts are skipped rather than faked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# The facts the full business model expects.
ALL_FACTS = ("fact_sales", "fact_orders", "fact_inventory", "fact_targets", "fact_kam_targets")
ALL_DIMENSIONS = ("dim_date", "dim_sku", "dim_customer", "dim_kam", "dim_region")

# What each analysis needs in order to mean anything.
ANALYSIS_REQUIREMENTS = {
    "sales_kpis": ("fact_sales",),
    "forecasting": ("fact_sales",),
    "trend_analysis": ("fact_sales",),
    "driver_decomposition": ("fact_sales",),
    "fulfilment_analysis": ("fact_orders",),
    "inventory_analysis": ("fact_inventory",),
    "target_variance": ("fact_targets",),
    "kam_performance": ("fact_kam_targets",),
    "supply_signals": ("fact_orders", "fact_inventory"),
}


@dataclass(frozen=True)
class DatasetCapabilities:
    """What a dataset can and cannot be asked.

    ``notes`` carries anything a reader needs in order not to misread the
    result — a metric that exists but means something different here, or a
    dimension that had to be approximated.
    """

    name: str
    available_facts: tuple[str, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def missing_facts(self) -> tuple[str, ...]:
        return tuple(fact for fact in ALL_FACTS if fact not in self.available_facts)

    def supports(self, analysis: str) -> bool:
        """Whether an analysis has the facts it needs."""
        required = ANALYSIS_REQUIREMENTS.get(analysis)
        if required is None:
            raise ValueError(
                f"Unknown analysis '{analysis}'; known: {sorted(ANALYSIS_REQUIREMENTS)}"
            )
        return all(fact in self.available_facts for fact in required)

    @property
    def supported_analyses(self) -> tuple[str, ...]:
        return tuple(name for name in sorted(ANALYSIS_REQUIREMENTS) if self.supports(name))

    @property
    def unsupported_analyses(self) -> tuple[str, ...]:
        return tuple(name for name in sorted(ANALYSIS_REQUIREMENTS) if not self.supports(name))

    def why_unsupported(self, analysis: str) -> str:
        """Which missing fact blocks an analysis, so the gap is legible."""
        if self.supports(analysis):
            return ""
        missing = [
            fact
            for fact in ANALYSIS_REQUIREMENTS[analysis]
            if fact not in self.available_facts
        ]
        return f"{analysis} needs {', '.join(missing)}, which this dataset does not contain."

    def describe(self) -> str:
        """A readable statement of what this dataset can answer."""
        lines = [f"{self.name}", ""]
        lines.append("Available: " + ", ".join(self.available_facts))
        if self.missing_facts:
            lines.append("Absent:    " + ", ".join(self.missing_facts))
        lines.append("")
        lines.append("Can answer:")
        for analysis in self.supported_analyses:
            lines.append(f"  + {analysis}")
        if self.unsupported_analyses:
            lines.append("")
            lines.append("Cannot answer:")
            for analysis in self.unsupported_analyses:
                lines.append(f"  - {self.why_unsupported(analysis)}")
        if self.notes:
            lines.append("")
            lines.append("Read before using:")
            for note in self.notes:
                lines.append(f"  * {note}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "available_facts": list(self.available_facts),
            "missing_facts": list(self.missing_facts),
            "supported_analyses": list(self.supported_analyses),
            "unsupported_analyses": list(self.unsupported_analyses),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class AdaptedDataset:
    """Tables mapped onto the business model, with what they can support."""

    tables: dict[str, pd.DataFrame]
    capabilities: DatasetCapabilities
    source_rows: int

    def require(self, analysis: str) -> None:
        """Raise rather than return a meaningless answer."""
        if not self.capabilities.supports(analysis):
            raise ValueError(self.capabilities.why_unsupported(analysis))


def empty_fact(name: str) -> pd.DataFrame:
    """A correctly shaped but empty frame for a fact the dataset lacks.

    The cleaning and validation layers expect the whole model to be present.
    Supplying an empty frame keeps them working without inventing rows, and the
    capability declaration is what stops anything reading meaning into it.
    """
    columns = {
        "fact_orders": [
            "order_id", "date", "customer_id", "region_code", "channel", "sku_id",
            "ordered_units", "fulfilled_units", "cancelled_units", "order_status", "sales_type",
        ],
        "fact_inventory": [
            "date", "sku_id", "warehouse", "region_code", "opening_stock", "received_units",
            "available_stock", "ordered_units", "fulfilled_units", "stockout_flag",
        ],
        "fact_targets": [
            "date", "region_code", "category", "channel",
            "sales_target", "order_target", "fill_rate_target",
        ],
        "fact_kam_targets": ["date", "kam_id", "sales_target", "order_target"],
    }
    if name not in columns:
        raise ValueError(f"No column definition for '{name}'.")
    return pd.DataFrame(columns=columns[name])
