"""
Computes probability P(condition holds) from ensemble forecast members.

Example:
  ensemble = [15.2, 16.4, 17.1, 16.8, 18.0, 14.9, ...]  (500+ members)
  threshold = 16.0, condition = ">="
  → P(temp >= 16) = count(x >= 16) / total
"""
from statistics import mean, stdev
from typing import Optional
from loguru import logger


class ProbabilityEngine:
    """Derives probabilities from ensemble forecast distributions."""

    def compute(
        self,
        ensemble: list[float],
        threshold: float,
        condition: str,
    ) -> Optional[float]:
        """
        Compute P(condition(value, threshold)) from ensemble members.

        Args:
            ensemble: List of forecast values (one per ensemble member)
            threshold: The threshold value from the market question
            condition: Operator string: ">=" | ">" | "<=" | "<" | "=="

        Returns:
            Probability in [0, 1], or None if insufficient data.
        """
        if not ensemble:
            return None

        n = len(ensemble)
        if n < 2:
            logger.warning(f"Only {n} ensemble member(s) — probability unreliable")

        ops = {
            ">=": lambda x, t: x >= t,
            ">":  lambda x, t: x > t,
            "<=": lambda x, t: x <= t,
            "<":  lambda x, t: x < t,
            "==": lambda x, t: abs(x - t) < 0.5,  # within 0.5 degrees
        }

        op = ops.get(condition)
        if not op:
            logger.error(f"Unknown condition: {condition!r}")
            return None

        hits = sum(1 for v in ensemble if op(v, threshold))
        probability = hits / n

        # Log distribution stats
        mu = mean(ensemble)
        sd = stdev(ensemble) if n > 1 else 0
        logger.debug(
            f"Ensemble stats: n={n}, mean={mu:.2f}, std={sd:.2f} | "
            f"P(value {condition} {threshold}) = {hits}/{n} = {probability:.3f}"
        )

        return probability

    def confidence_interval(
        self,
        ensemble: list[float],
        confidence: float = 0.90,
    ) -> tuple[float, float]:
        """Return (lower, upper) confidence interval for the ensemble."""
        if not ensemble:
            return (0.0, 0.0)
        sorted_e = sorted(ensemble)
        n = len(sorted_e)
        alpha = (1 - confidence) / 2
        lower_idx = int(alpha * n)
        upper_idx = int((1 - alpha) * n) - 1
        return (sorted_e[lower_idx], sorted_e[upper_idx])
