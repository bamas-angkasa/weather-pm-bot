"""
Priority scoring for market legs.

Two-phase design to minimize API calls:
  Phase 1 (local, free): day_score + price_score — filter before fetching forecast
  Phase 2 (after forecast): win_score — requires model probability

Phase 1 thresholds (configurable):
  MIN_DAY_SCORE   = 0.6  — skip 11–14 day markets (too unreliable)
  MIN_PRICE_SCORE = 0.8  — skip extreme prices (<8¢ or >65¢ on buy side)
  MAX_LEGS_PER_EVENT = 3 — only top N legs per event pass to forecast

Final score = edge × day_score × price_score × win_score
"""
from dataclasses import dataclass

MIN_DAY_SCORE = 0.2    # allow same-day/next-day; day multiplier handles ranking
MIN_PRICE_SCORE = 0.8
MAX_LEGS_PER_EVENT = 3


@dataclass
class PriorityScore:
    day_score: float
    price_score: float
    win_score: float
    edge: float
    final: float


class PriorityScorer:

    def day_score(self, days_ahead: int) -> float:
        """Score based on days until market resolution. Sweet spot: 3–7 days."""
        if days_ahead <= 1:
            return 0.2   # nearly resolved, market fully efficient
        elif days_ahead == 2:
            return 0.6
        elif days_ahead <= 7:
            return 1.0   # sweet spot
        elif days_ahead <= 10:
            return 0.7
        elif days_ahead <= 14:
            return 0.4
        else:
            return 0.0   # forecast too unreliable beyond 14 days

    def price_score(self, yes_price: float, signal: str) -> float:
        """
        Score based on the price of the token we'd buy.
        Low-price YES = cheap early market = high upside if model is right.
        Signal determines which token price matters.
        """
        price = yes_price if signal == "BUY_YES" else (1.0 - yes_price)

        if price < 0.03:
            return 0.0   # near-impossible, not worth it
        elif price < 0.08:
            return 0.5   # very cheap, high risk
        elif price <= 0.25:
            return 1.0   # sweet spot: cheap but plausible
        elif price <= 0.65:
            return 0.8   # fair odds, good liquidity
        elif price <= 0.90:
            return 0.5   # market already confident
        else:
            return 0.2   # near-certainty, little upside

    def win_score(self, model_probability: float, signal: str) -> float:
        """
        Score based on win probability on our side.
        Requires model probability — called after forecast fetch.
        Returns 0 if we'd be betting against our own model (win < 50%).
        """
        win_prob = model_probability if signal == "BUY_YES" else (1.0 - model_probability)

        if win_prob < 0.50:
            return 0.0   # discard — betting against the model
        elif win_prob < 0.65:
            return 0.8
        else:
            return 1.0

    def local_rank(self, yes_price: float, days_ahead: int) -> float:
        """
        Phase 1 combined rank — used to cap legs per event before forecast fetch.
        Takes the better of BUY_YES or BUY_NO price score.
        """
        d = self.day_score(days_ahead)
        p = max(self.price_score(yes_price, "BUY_YES"), self.price_score(yes_price, "BUY_NO"))
        return d * p

    def score(
        self,
        edge: float,
        days_ahead: int,
        yes_price: float,
        signal: str,
        model_probability: float,
    ) -> PriorityScore:
        d = self.day_score(days_ahead)
        p = self.price_score(yes_price, signal)
        w = self.win_score(model_probability, signal)
        final = abs(edge) * d * p * w
        return PriorityScore(
            day_score=d,
            price_score=p,
            win_score=w,
            edge=edge,
            final=final,
        )
