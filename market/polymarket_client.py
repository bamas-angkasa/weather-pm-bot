"""
Fetches active weather markets from Polymarket Gamma API.
Markets first — we only forecast weather for markets that exist.
"""
import time
from dataclasses import dataclass, field
from typing import Optional
import requests
from loguru import logger

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

@dataclass
class MarketOpportunity:
    market_id: str
    question: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    yes_price: float    # mid-price of YES token (0.0–1.0)
    no_price: float
    volume_24h: float
    liquidity: float


class PolymarketClient:
    """Fetches weather markets from Polymarket and returns structured opportunities."""

    WEATHER_TAGS = ["weather"]
    WEATHER_KEYWORDS = [
        "temperature", "temp", "celsius", "fahrenheit",
        "snow", "snowfall", "precipitation", "rain",
        "high", "low", "warm", "cold", "freeze",
    ]

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "weather-polymarket-bot/1.0"})

    def fetch_weather_markets(self, limit: int = 200) -> list[MarketOpportunity]:
        """Scan Polymarket for all active weather markets."""
        markets = []

        # Primary: fetch by weather tag
        tag_markets = self._fetch_by_tag("weather", limit=limit)
        markets.extend(tag_markets)

        # Deduplicate by market_id
        seen = {m.market_id for m in markets}
        logger.info(f"Found {len(markets)} weather markets on Polymarket")
        return markets

    def _fetch_by_tag(self, tag_slug: str, limit: int = 200) -> list[MarketOpportunity]:
        """Fetch markets by tag slug from Gamma API."""
        results = []
        offset = 0

        while True:
            try:
                resp = self.session.get(
                    f"{GAMMA_API}/markets",
                    params={
                        "tag_slug": tag_slug,
                        "active": "true",
                        "closed": "false",
                        "limit": min(limit, 100),
                        "offset": offset,
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                logger.error(f"Gamma API error: {e}")
                break

            if not data:
                break

            for raw in data:
                opportunity = self._parse_gamma_market(raw)
                if opportunity:
                    results.append(opportunity)

            if len(data) < 100:
                break  # last page
            offset += 100
            time.sleep(0.2)

        return results

    def _parse_gamma_market(self, raw: dict) -> Optional[MarketOpportunity]:
        """Parse a raw Gamma API market into a MarketOpportunity."""
        try:
            market_id = raw.get("id", "")
            question = raw.get("question", "")
            condition_id = raw.get("conditionId", "")
            tokens = raw.get("tokens", [])

            if len(tokens) < 2:
                return None

            # Extract YES/NO token IDs
            yes_token_id = ""
            no_token_id = ""
            for token in tokens:
                outcome = token.get("outcome", "").lower()
                if outcome == "yes":
                    yes_token_id = token.get("token_id", "")
                elif outcome == "no":
                    no_token_id = token.get("token_id", "")

            if not yes_token_id or not no_token_id:
                return None

            # Get prices from outcomePrices field (JSON string or list)
            outcome_prices = raw.get("outcomePrices", ["0.5", "0.5"])
            if isinstance(outcome_prices, str):
                import json
                outcome_prices = json.loads(outcome_prices)

            yes_price = float(outcome_prices[0]) if outcome_prices else 0.5
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 1.0 - yes_price

            volume = float(raw.get("volume", 0) or 0)
            liquidity = float(raw.get("liquidity", 0) or 0)

            return MarketOpportunity(
                market_id=market_id,
                question=question,
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                yes_price=yes_price,
                no_price=no_price,
                volume_24h=volume,
                liquidity=liquidity,
            )

        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"Failed to parse market: {e}")
            return None

    def get_mid_price(self, token_id: str) -> Optional[float]:
        """Get the current mid-price for a token from CLOB API."""
        try:
            resp = self.session.get(
                f"{CLOB_API}/mid-point",
                params={"token_id": token_id},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("mid", 0.5))
        except Exception as e:
            logger.debug(f"CLOB mid-price error for {token_id}: {e}")
            return None
