"""
calculators.py

Deterministic treaty math, kept strictly separate from the LLM
extraction layer. Implements a strategy pattern: pick a calculator by
treaty type, feed it the loss + user-entered treaty parameters, get
back the figures the PLA needs (Retained Loss / Amount Affected to
Treaty, or Retained + per-layer XL recoveries).

Nothing here ever talks to the LLM or the filesystem -- pure functions
over numbers, easy to unit test in isolation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class TreatyCalculationError(ValueError):
    """Raised when inputs are missing or nonsensical for a calculation."""


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass
class ProportionalResult:
    cession_pct: float
    retained_loss: float
    amount_affected_to_treaty: float


@dataclass
class XLLayerResult:
    layer_number: int
    deductible: float
    layer_limit: float
    amount_affected: float


@dataclass
class XLResult:
    retained_loss: float
    layers: list[XLLayerResult] = field(default_factory=list)

    @property
    def total_amount_affected(self) -> float:
        return sum(layer.amount_affected for layer in self.layers)


# --------------------------------------------------------------------------
# Strategy interface
# --------------------------------------------------------------------------

class TreatyCalculator(ABC):
    """Common interface every concrete calculator implements."""

    name: str

    @abstractmethod
    def calculate(self, *args, **kwargs):
        ...


class SurplusCalculator(TreatyCalculator):
    """Surplus treaty: cession % derived from Retention Line vs Sum Insured."""

    name = "Surplus"

    def calculate(self, loss: float, retention_line: float, sum_insured: float) -> ProportionalResult:
        if loss < 0:
            raise TreatyCalculationError("Loss cannot be negative.")
        if sum_insured is None or sum_insured <= 0:
            raise TreatyCalculationError("Sum Insured must be a positive number.")
        if retention_line is None or retention_line < 0:
            raise TreatyCalculationError("Retention Line cannot be negative.")

        cession_pct = max(0.0, 1.0 - (retention_line / sum_insured))
        retained_loss = loss * (1.0 - cession_pct)
        amount_affected = loss * cession_pct
        return ProportionalResult(
            cession_pct=cession_pct,
            retained_loss=retained_loss,
            amount_affected_to_treaty=amount_affected,
        )


class QuotaShareCalculator(TreatyCalculator):
    """Quota Share treaty: user supplies a fixed cession percentage."""

    name = "Quota Share"

    def calculate(self, loss: float, cession_pct: float) -> ProportionalResult:
        if loss < 0:
            raise TreatyCalculationError("Loss cannot be negative.")
        if cession_pct is None or not (0.0 <= cession_pct <= 1.0):
            raise TreatyCalculationError("Cession percentage must be between 0 and 1 (e.g. 0.4 for 40%).")

        retained_loss = loss * (1.0 - cession_pct)
        amount_affected = loss * cession_pct
        return ProportionalResult(
            cession_pct=cession_pct,
            retained_loss=retained_loss,
            amount_affected_to_treaty=amount_affected,
        )


@dataclass
class XLLayerInput:
    deductible: float
    layer_limit: float


class XLCalculator(TreatyCalculator):
    """Excess of Loss treaty: retained loss + recovery per stacked layer.

    Layers are applied sequentially in the order given. Each layer's
    deductible is generally the top of the layer below it, but the
    calculator does not assume that -- it uses whatever deductible is
    entered for that layer, as XL programmes are sometimes non-contiguous.
    """

    name = "XL"

    def calculate(self, loss: float, layers: list[XLLayerInput]) -> XLResult:
        if loss < 0:
            raise TreatyCalculationError("Loss cannot be negative.")
        if not layers:
            raise TreatyCalculationError("At least one layer (Deductible + Layer Limit) is required.")

        layer_results: list[XLLayerResult] = []
        for i, layer in enumerate(layers, start=1):
            if layer.deductible is None or layer.deductible < 0:
                raise TreatyCalculationError(f"Layer {i}: Deductible cannot be negative.")
            if layer.layer_limit is None or layer.layer_limit <= 0:
                raise TreatyCalculationError(f"Layer {i}: Layer Limit must be a positive number.")

            amount_affected = max(
                0.0,
                min(loss, layer.deductible + layer.layer_limit) - layer.deductible,
            )
            layer_results.append(
                XLLayerResult(
                    layer_number=i,
                    deductible=layer.deductible,
                    layer_limit=layer.layer_limit,
                    amount_affected=amount_affected,
                )
            )

        # Retained loss uses the first (lowest) layer's deductible, which is
        # the retention the reinsured carries before any layer responds.
        first_deductible = layers[0].deductible
        retained_loss = min(loss, first_deductible)

        return XLResult(retained_loss=retained_loss, layers=layer_results)


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

_CALCULATORS: dict[str, TreatyCalculator] = {
    "Surplus": SurplusCalculator(),
    "Quota Share": QuotaShareCalculator(),
    "XL": XLCalculator(),
}


def get_calculator(treaty_type: str) -> TreatyCalculator:
    try:
        return _CALCULATORS[treaty_type]
    except KeyError as exc:
        raise TreatyCalculationError(
            f"Unknown treaty type '{treaty_type}'. Expected one of: {', '.join(_CALCULATORS)}"
        ) from exc
