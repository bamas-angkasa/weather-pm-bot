"""
Weather Polymarket Bot — main entry point.

Architecture (market-first):
  1. Scan Polymarket for active weather markets
  2. Parse each market question → city, date, threshold, condition
  3. Fetch ensemble weather forecast only for relevant markets
  4. Compute P(condition) from ensemble distribution
  5. Compare to market price → calculate edge
  6. Apply fractional Kelly sizing + risk limits
  7. Execute trade (dry-run or live)

Usage:
  python main.py              # runs continuously every 5 minutes
  python main.py --once       # runs a single cycle then exits
  python main.py --dry-run    # override config to force dry-run mode
"""
import argparse
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

import yaml
from dotenv import load_dotenv
from loguru import logger

from market.polymarket_client import PolymarketClient, MarketOpportunity
from market.market_parser import MarketParser, ParsedMarket
from weather.forecast import WeatherForecaster
from weather.probability import ProbabilityEngine
from trading.edge_detector import EdgeDetector
from trading.position_sizer import PositionSizer
from trading.priority_scorer import PriorityScorer, MIN_DAY_SCORE, MIN_PRICE_SCORE, MAX_LEGS_PER_EVENT
from trading.risk_manager import RiskManager
from execution.trader import Trader
from utils.logger import setup_logger


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run_cycle(
    poly_client: PolymarketClient,
    parser: MarketParser,
    forecaster: WeatherForecaster,
    probability_engine: ProbabilityEngine,
    edge_detector: EdgeDetector,
    position_sizer: PositionSizer,
    risk_manager: RiskManager,
    trader: Trader,
    config: dict,
    bankroll: float,
):
    """Run one full bot cycle: scan → forecast → trade."""
    cfg_weather = config["weather"]
    max_forecast_days = cfg_weather.get("max_forecast_days", 14)

    logger.info("=" * 60)
    logger.info("Starting new bot cycle")

    # Step 1: Scan Polymarket for weather markets
    markets = poly_client.fetch_weather_markets()
    logger.info(f"Fetched {len(markets)} weather markets")

    if not markets:
        logger.warning("No weather markets found — is Polymarket accessible?")
        return

    # Group legs by event_id so we only take the best edge per event
    events: dict[str, list[MarketOpportunity]] = defaultdict(list)
    for m in markets:
        key = m.event_id if m.event_id else m.market_id
        events[key].append(m)

    logger.info(f"Grouped into {len(events)} events")

    traded = 0
    skipped = 0
    errors = 0

    for event_id, legs in events.items():
        try:
            result = process_event(
                legs=legs,
                parser=parser,
                forecaster=forecaster,
                probability_engine=probability_engine,
                edge_detector=edge_detector,
                position_sizer=position_sizer,
                risk_manager=risk_manager,
                trader=trader,
                config=config,
                bankroll=bankroll,
                max_forecast_days=max_forecast_days,
            )
            if result == "traded":
                traded += 1
            elif result == "skipped":
                skipped += 1
            elif result == "error":
                errors += 1
        except Exception as e:
            logger.error(f"Unexpected error processing event {event_id}: {e}")
            errors += 1

    logger.info(
        f"Cycle complete: {traded} trades, {skipped} skipped, {errors} errors | "
        f"Total exposure: ${risk_manager.state.total_exposure:.2f} | "
        f"Daily PnL: ${risk_manager.state.daily_pnl:.2f}"
    )


_priority_scorer = PriorityScorer()


def score_leg(
    market: MarketOpportunity,
    parser: MarketParser,
    forecaster: WeatherForecaster,
    probability_engine: ProbabilityEngine,
    edge_detector: EdgeDetector,
    position_sizer: PositionSizer,
    config: dict,
    bankroll: float,
    max_forecast_days: int,
) -> Optional[dict]:
    """
    Score a single market leg. Returns a scoring dict if tradeable, else None.

    Phase 1 (local, no API): liquidity, date, price filters.
    Phase 2 (after forecast fetch): edge, win probability, final priority score.
    """
    cfg = config["trading"]

    # --- Phase 1: local filters (free) ---
    if market.liquidity < cfg.get("min_liquidity", 0):
        return None
    if market.volume_24h < cfg.get("min_volume", 0):
        return None

    parsed = parser.parse(market.question)
    if not parsed:
        return None

    today = date.today()
    days_ahead = (parsed.target_date - today).days

    # Day score filter — skip before fetching forecast
    if _priority_scorer.day_score(days_ahead) < MIN_DAY_SCORE:
        logger.debug(f"Day filter: {days_ahead}d ahead score too low — skip")
        return None

    # Price score filter — skip extremes before fetching forecast
    best_price_s = max(
        _priority_scorer.price_score(market.yes_price, "BUY_YES"),
        _priority_scorer.price_score(market.yes_price, "BUY_NO"),
    )
    if best_price_s < MIN_PRICE_SCORE:
        logger.debug(f"Price filter: YES={market.yes_price:.3f} score too low — skip")
        return None

    # --- Phase 2: fetch forecast (costs API call) ---
    ensemble = forecaster.get_ensemble(
        city=parsed.city,
        target_date=parsed.target_date,
        variable=parsed.variable,
    )
    if not ensemble:
        return None

    model_probability = probability_engine.compute(
        ensemble=ensemble,
        threshold=parsed.threshold,
        condition=parsed.condition,
    )
    if model_probability is None:
        return None

    edge_result = edge_detector.compute(
        model_probability=model_probability,
        market_yes_price=market.yes_price,
        ensemble=ensemble,
    )
    if edge_result.signal == "PASS":
        return None

    position = position_sizer.compute(
        model_probability=model_probability,
        market_price=market.yes_price,
        bankroll=bankroll,
        signal=edge_result.signal,
    )
    if position.usdc_size <= 0:
        return None

    priority = _priority_scorer.score(
        edge=edge_result.edge,
        days_ahead=days_ahead,
        yes_price=market.yes_price,
        signal=edge_result.signal,
        model_probability=model_probability,
    )

    # Discard if win probability < 50%
    if priority.win_score == 0.0:
        logger.debug(f"Win filter: model={model_probability:.2f} signal={edge_result.signal} — skip")
        return None

    return {
        "market": market,
        "parsed": parsed,
        "ensemble": ensemble,
        "model_probability": model_probability,
        "edge_result": edge_result,
        "position": position,
        "priority": priority,
    }


def process_event(
    legs: list[MarketOpportunity],
    parser: MarketParser,
    forecaster: WeatherForecaster,
    probability_engine: ProbabilityEngine,
    edge_detector: EdgeDetector,
    position_sizer: PositionSizer,
    risk_manager: RiskManager,
    trader: Trader,
    config: dict,
    bankroll: float,
    max_forecast_days: int,
) -> str:
    """Score all legs of an event, pick the single best-edge leg, execute it."""

    # Phase 1 cap: rank legs locally, only pass top N to forecast API
    def _local_rank(leg):
        parsed = parser.parse(leg.question)
        if not parsed:
            return 0.0
        days_ahead = (date.today() - parsed.target_date).days * -1
        return _priority_scorer.local_rank(leg.yes_price, days_ahead)

    top_legs = sorted(legs, key=_local_rank, reverse=True)[:MAX_LEGS_PER_EVENT]

    scored = []
    for leg in top_legs:
        score = score_leg(
            market=leg,
            parser=parser,
            forecaster=forecaster,
            probability_engine=probability_engine,
            edge_detector=edge_detector,
            position_sizer=position_sizer,
            config=config,
            bankroll=bankroll,
            max_forecast_days=max_forecast_days,
        )
        if score:
            scored.append(score)

    if not scored:
        return "skipped"

    # Pick the leg with the highest priority score
    best = max(scored, key=lambda s: s["priority"].final)

    market = best["market"]
    parsed = best["parsed"]
    edge_result = best["edge_result"]
    position = best["position"]
    model_probability = best["model_probability"]

    if len(scored) > 1:
        logger.info(
            f"Event has {len(legs)} legs, {len(scored)} with edge — "
            f"best: {market.question!r} edge={edge_result.edge:+.3f}"
        )

    if edge_result.signal == "BUY_YES":
        token_id = market.yes_token_id
        token_price = market.yes_price
        side = "YES"
    else:
        token_id = market.no_token_id
        token_price = market.no_price
        side = "NO"

    # Risk check
    approved, reason, capped_size = risk_manager.check(
        market_id=market.market_id,
        city=parsed.city,
        proposed_size=position.usdc_size,
        bankroll=bankroll,
    )
    if not approved:
        logger.info(f"Trade rejected by risk manager: {reason}")
        return "skipped"

    priority = best["priority"]
    logger.info(
        f"TRADE SIGNAL: BUY {side} | "
        f"city={parsed.city} date={parsed.target_date} "
        f"threshold={parsed.threshold}{parsed.unit[0].upper()} | "
        f"model={model_probability:.1%} market={market.yes_price:.1%} "
        f"edge={edge_result.edge:+.1%} | "
        f"score={priority.final:.4f} "
        f"[day={priority.day_score:.1f} price={priority.price_score:.1f} win={priority.win_score:.1f}] | "
        f"size=${capped_size:.2f} @ {token_price:.3f}"
    )

    result = trader.buy(
        market_id=market.market_id,
        token_id=token_id,
        side=side,
        size=capped_size,
        price=token_price,
    )

    if result.success:
        risk_manager.record_trade(
            market_id=market.market_id,
            city=parsed.city,
            side=side,
            size=capped_size,
            price=token_price,
            token_id=token_id,
        )
        logger.success(f"Trade executed: {side} ${capped_size:.2f} on {market.question[:60]}...")
        return "traded"
    else:
        logger.error(f"Trade failed: {result.message}")
        return "error"


def main():
    parser = argparse.ArgumentParser(description="Weather Polymarket Trading Bot")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    # Load environment
    load_dotenv()

    # Load config
    config = load_config(args.config)

    # Setup logging
    setup_logger(
        level=config["bot"].get("log_level", "INFO"),
        log_file="bot.log",
    )

    logger.info("Weather Polymarket Bot starting up")

    # Override dry_run from CLI
    if args.dry_run:
        config["trading"]["dry_run"] = True
        logger.info("Dry-run mode forced by CLI flag")

    dry_run = config["trading"].get("dry_run", True)
    bankroll = float(os.getenv("BANKROLL", "1000"))

    logger.info(
        f"Config: dry_run={dry_run} bankroll=${bankroll:.2f} "
        f"edge_threshold={config['trading']['edge_threshold']:.0%} "
        f"kelly_fraction={config['trading']['kelly_fraction']:.0%}"
    )

    # Initialize components
    poly_client = PolymarketClient()
    market_parser = MarketParser()
    forecaster = WeatherForecaster(
        models=config["weather"].get("ensemble_models", ["ecmwf_ifs04"])
    )
    probability_engine = ProbabilityEngine()
    edge_detector = EdgeDetector(
        edge_threshold=config["trading"]["edge_threshold"]
    )
    position_sizer = PositionSizer(
        kelly_fraction=config["trading"]["kelly_fraction"]
    )
    risk_manager = RiskManager(
        min_trade_size=config["risk"].get("min_trade_size", 5.0),
        max_per_market=config["risk"]["max_exposure_per_market"],
        max_per_city=config["risk"]["max_exposure_per_city"],
        max_daily_loss=config["risk"]["max_daily_loss"],
        max_total_exposure=config["risk"]["max_portfolio_exposure"],
        state_file=config["bot"].get("state_file", "state.json"),
    )
    trader = Trader(dry_run=dry_run)

    if args.once:
        run_cycle(
            poly_client, market_parser, forecaster, probability_engine,
            edge_detector, position_sizer, risk_manager, trader, config, bankroll
        )
        return

    # Continuous loop
    interval = config["bot"].get("interval_seconds", 300)
    logger.info(f"Bot running every {interval}s. Press Ctrl+C to stop.")

    while True:
        try:
            run_cycle(
                poly_client, market_parser, forecaster, probability_engine,
                edge_detector, position_sizer, risk_manager, trader, config, bankroll
            )
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Cycle error: {e}")

        logger.info(f"Sleeping {interval}s until next cycle...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
