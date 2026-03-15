"""
Fractional Kelly Criterion position sizing.

Kelly formula:
  f* = (b*p - q) / b

Where:
  b = net odds (payout / stake - 1)  = (1 - price) / price  for binary outcomes
  p = model probability of winning
  q = 1 - p

We use 0.25 * Kelly (quarter Kelly) for safety.
"""
from dataclasses import dataclass
from loguru import logger


@dataclass
class PositionSize:
    kelly_fraction: float       # raw Kelly fraction
    adjusted_fraction: float    # after safety multiplier
    usdc_size: float            # dollar amount to bet
    max_payout: float           # potential payout if wins


class PositionSizer:
    """Computes fractional Kelly position sizes."""

    def __init__(self, kelly_fraction: float = 0.25):
        self.kelly_fraction = kelly_fraction

    def compute(
        self,
        model_probability: float,
        market_price: float,
        bankroll: float,
        signal: str,
    ) -> PositionSize:
        """
        Compute position size using fractional Kelly.

        Args:
            model_probability: Our probability that YES wins (0–1)
            market_price: Market YES price (0–1)
            bankroll: Total available bankroll in USDC
            signal: "BUY_YES" or "BUY_NO"

        Returns:
            PositionSize with dollar amounts.
        """
        if signal == "BUY_YES":
            p = model_probability           # P(YES)
            price = market_price            # cost of YES token
        elif signal == "BUY_NO":
            p = 1.0 - model_probability     # P(NO)
            price = 1.0 - market_price      # cost of NO token
        else:
            return PositionSize(0, 0, 0, 0)

        q = 1.0 - p

        if price <= 0 or price >= 1:
            logger.warning(f"Invalid market price: {price}")
            return PositionSize(0, 0, 0, 0)

        # Net odds: if we bet $1 on a token at price p, we win $(1/price - 1) net
        b = (1.0 - price) / price

        # Kelly fraction
        kelly = (b * p - q) / b

        if kelly <= 0:
            logger.debug(f"Kelly <= 0 ({kelly:.4f}), no bet")
            return PositionSize(kelly, 0, 0, 0)

        # Apply fractional Kelly
        adjusted = kelly * self.kelly_fraction

        # Dollar size
        usdc_size = adjusted * bankroll
        max_payout = usdc_size / price

        logger.debug(
            f"Kelly sizing: b={b:.3f} p={p:.3f} q={q:.3f} → "
            f"kelly={kelly:.4f} adjusted={adjusted:.4f} size=${usdc_size:.2f}"
        )

        return PositionSize(
            kelly_fraction=kelly,
            adjusted_fraction=adjusted,
            usdc_size=usdc_size,
            max_payout=max_payout,
        )
