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
    event_id: str = ""   # groups all legs of the same negRisk event


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
        """Scan Polymarket for all active weather markets via search-v2."""
        results = []
        page = 1

        while len(results) < limit:
            try:
                resp = self.session.get(
                    f"{GAMMA_API}/search-v2",
                    params={
                        "q": "weather",
                        "type": "events",
                        "events_status": "active",
                        "limit_per_type": 20,
                        "page": page,
                        "optimized": "false",
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                logger.error(f"Gamma API error: {e}")
                break

            events = data.get("events", [])
            if not events:
                break

            for event in events:
                for raw_market in event.get("markets", []):
                    opportunity = self._parse_gamma_market(raw_market, event_id=event.get("id", ""))
                    if opportunity:
                        results.append(opportunity)

            if len(events) < 20:
                break  # last page
            page += 1
            time.sleep(0.2)

        logger.info(f"Found {len(results)} weather markets on Polymarket")
        return results

    def _parse_gamma_market(self, raw: dict, event_id: str = "") -> Optional[MarketOpportunity]:
        """Parse a raw search-v2 market into a MarketOpportunity."""
        import json as _json
        try:
            if not raw.get("active") or raw.get("closed"):
                return None

            market_id = raw.get("id", "")
            question = raw.get("question", "")
            condition_id = raw.get("conditionId", "")

            # Token IDs: clobTokenIds is a JSON string "[yes_id, no_id]"
            clob_token_ids = raw.get("clobTokenIds", "[]")
            if isinstance(clob_token_ids, str):
                clob_token_ids = _json.loads(clob_token_ids)
            if len(clob_token_ids) < 2:
                return None
            yes_token_id = clob_token_ids[0]
            no_token_id = clob_token_ids[1]

            # Prices: outcomePrices is a JSON string "[yes_price, no_price]"
            outcome_prices = raw.get("outcomePrices", "[\"0.5\", \"0.5\"]")
            if isinstance(outcome_prices, str):
                outcome_prices = _json.loads(outcome_prices)
            yes_price = float(outcome_prices[0]) if outcome_prices else 0.5
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 1.0 - yes_price

            volume = float(raw.get("volume24hr", 0) or 0)
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
                event_id=event_id,
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
