"""
Risk management: enforces exposure limits before any trade is approved.

Limits:
  - max 3% of bankroll per individual market
  - max 10% of bankroll per city
  - max 5% daily loss stop-loss
  - max 25% total portfolio exposure
"""
import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from loguru import logger


@dataclass
class RiskState:
    positions: dict = field(default_factory=dict)       # market_id → position info
    city_exposure: dict = field(default_factory=dict)   # city → USDC exposure
    total_exposure: float = 0.0
    daily_pnl: float = 0.0
    date: str = field(default_factory=lambda: date.today().isoformat())


class RiskManager:
    """Tracks portfolio state and enforces risk limits."""

    def __init__(
        self,
        max_per_market: float = 0.10,
        max_per_city: float = 0.20,
        max_daily_loss: float = 0.10,
        max_total_exposure: float = 0.50,
        state_file: str = "state.json",
    ):
        self.max_per_market = max_per_market
        self.max_per_city = max_per_city
        self.max_daily_loss = max_daily_loss
        self.max_total_exposure = max_total_exposure
        self.state_file = state_file
        self.state = self._load_state()

    def check(
        self,
        market_id: str,
        city: str,
        proposed_size: float,
        bankroll: float,
    ) -> tuple[bool, str, float]:
        """
        Check if a proposed trade passes risk limits.

        Returns:
            (approved: bool, reason: str, capped_size: float)
        """
        today = date.today().isoformat()

        # Reset daily PnL if new day
        if self.state.date != today:
            self.state.daily_pnl = 0.0
            self.state.date = today

        # 1. Daily loss stop-loss
        if self.state.daily_pnl < -(bankroll * self.max_daily_loss):
            return False, f"Daily loss limit hit: {self.state.daily_pnl:.2f} USDC", 0.0

        # 2. Already have position in this market
        if market_id in self.state.positions:
            return False, "Already have position in this market", 0.0

        # 3. Cap per-market exposure
        max_market_usdc = bankroll * self.max_per_market
        capped_size = min(proposed_size, max_market_usdc)
        if capped_size < proposed_size:
            logger.info(f"Position capped by per-market limit: {proposed_size:.2f} → {capped_size:.2f}")

        # 4. Per-city exposure
        current_city_exp = self.state.city_exposure.get(city, 0.0)
        max_city_usdc = bankroll * self.max_per_city
        remaining_city = max_city_usdc - current_city_exp
        if remaining_city <= 0:
            return False, f"City exposure limit reached for {city}", 0.0
        capped_size = min(capped_size, remaining_city)

        # 5. Total portfolio exposure
        remaining_total = bankroll * self.max_total_exposure - self.state.total_exposure
        if remaining_total <= 0:
            return False, "Total portfolio exposure limit reached", 0.0
        capped_size = min(capped_size, remaining_total)

        if capped_size < 1.0:
            return False, f"Position size too small after caps: {capped_size:.2f}", 0.0

        return True, "OK", capped_size

    def record_trade(
        self,
        market_id: str,
        city: str,
        side: str,
        size: float,
        price: float,
        token_id: str,
    ):
        """Record a trade in portfolio state."""
        self.state.positions[market_id] = {
            "city": city,
            "side": side,
            "size": size,
            "price": price,
            "token_id": token_id,
            "date": date.today().isoformat(),
        }
        self.state.city_exposure[city] = self.state.city_exposure.get(city, 0.0) + size
        self.state.total_exposure += size
        self._save_state()

    def record_pnl(self, pnl_delta: float):
        """Update daily PnL."""
        self.state.daily_pnl += pnl_delta
        self._save_state()

    def _load_state(self) -> RiskState:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    data = json.load(f)
                state = RiskState(**data)
                # Reset if new day
                if state.date != date.today().isoformat():
                    state.daily_pnl = 0.0
                    state.date = date.today().isoformat()
                return state
            except Exception as e:
                logger.warning(f"Could not load state file: {e}")
        return RiskState()

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                import dataclasses
                json.dump(dataclasses.asdict(self.state), f, indent=2)
        except Exception as e:
            logger.error(f"Could not save state: {e}")
