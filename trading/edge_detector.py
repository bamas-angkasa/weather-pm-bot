"""
Detects edges between model probability and market-implied probability.

Edge = ModelProbability − MarketPrice

A positive edge on YES means we think YES is underpriced.
A negative edge on YES means we think NO is underpriced.
"""
from dataclasses import dataclass
from typing import Optional
from loguru import logger


@dataclass
class EdgeResult:
    model_probability: float
    market_price: float         # YES token mid-price
    edge: float                  # model_probability - market_price
    signal: str                  # "BUY_YES" | "BUY_NO" | "PASS"
    confidence: str              # "HIGH" | "MEDIUM" | "LOW"


class EdgeDetector:
    """Computes trading edge between model forecasts and market prices."""

    def __init__(self, edge_threshold: float = 0.10):
        self.edge_threshold = edge_threshold

    def compute(
        self,
        model_probability: float,
        market_yes_price: float,
        ensemble: Optional[list[float]] = None,
    ) -> EdgeResult:
        """
        Compute edge and trading signal.

        Args:
            model_probability: Our probability estimate from ensemble
            market_yes_price: Current YES token price on Polymarket
            ensemble: Optional raw ensemble for confidence estimation

        Returns:
            EdgeResult with signal and metadata.
        """
        edge = model_probability - market_yes_price

        # Determine signal
        if edge >= self.edge_threshold:
            signal = "BUY_YES"
        elif edge <= -self.edge_threshold:
            signal = "BUY_NO"
        else:
            signal = "PASS"

        # Confidence based on ensemble spread and edge magnitude
        confidence = self._assess_confidence(edge, ensemble)

        result = EdgeResult(
            model_probability=model_probability,
            market_price=market_yes_price,
            edge=edge,
            signal=signal,
            confidence=confidence,
        )

        logger.info(
            f"Edge analysis: model={model_probability:.3f} market={market_yes_price:.3f} "
            f"edge={edge:+.3f} → {signal} [{confidence}]"
        )
        return result

    def _assess_confidence(
        self,
        edge: float,
        ensemble: Optional[list[float]],
    ) -> str:
        """Assess confidence based on edge magnitude and ensemble spread."""
        abs_edge = abs(edge)

        if abs_edge >= 0.20:
            base = "HIGH"
        elif abs_edge >= 0.12:
            base = "MEDIUM"
        else:
            base = "LOW"

        # Downgrade if ensemble has wide spread relative to threshold distance
        if ensemble and len(ensemble) > 10:
            from statistics import stdev
            spread = stdev(ensemble)
            if spread > 5.0:  # very uncertain forecast (>5°C spread)
                if base == "HIGH":
                    base = "MEDIUM"
                elif base == "MEDIUM":
                    base = "LOW"

        return base
