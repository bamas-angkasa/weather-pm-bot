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
    cfg_trading = config["trading"]
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

    traded = 0
    skipped = 0
    errors = 0

    for market in markets:
        try:
            result = process_market(
                market=market,
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
            logger.error(f"Unexpected error processing market {market.market_id}: {e}")
            errors += 1

    logger.info(
        f"Cycle complete: {traded} trades, {skipped} skipped, {errors} errors | "
        f"Total exposure: ${risk_manager.state.total_exposure:.2f} | "
        f"Daily PnL: ${risk_manager.state.daily_pnl:.2f}"
    )


def process_market(
    market: MarketOpportunity,
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
    """Process a single market. Returns 'traded', 'skipped', or 'error'."""
    cfg = config["trading"]

    logger.debug(f"Processing: {market.question!r}")

    # Filter: minimum liquidity and volume
    if market.liquidity < cfg.get("min_liquidity", 0):
        logger.debug(f"Skipping: liquidity {market.liquidity:.0f} < min {cfg['min_liquidity']}")
        return "skipped"

    if market.volume_24h < cfg.get("min_volume", 0):
        logger.debug(f"Skipping: volume {market.volume_24h:.0f} < min {cfg['min_volume']}")
        return "skipped"

    # Step 2: Parse market question
    parsed = parser.parse(market.question)
    if not parsed:
        logger.debug(f"Could not parse: {market.question!r}")
        return "skipped"

    # Step 3: Filter by date range
    today = date.today()
    days_ahead = (parsed.target_date - today).days

    if days_ahead < 0:
        logger.debug(f"Skipping: market date {parsed.target_date} is in the past")
        return "skipped"

    if days_ahead > max_forecast_days:
        logger.debug(
            f"Skipping: {parsed.target_date} is {days_ahead} days ahead "
            f"(max {max_forecast_days})"
        )
        return "skipped"

    logger.info(
        f"Market: {market.question!r} | "
        f"Parsed: city={parsed.city} date={parsed.target_date} "
        f"threshold={parsed.threshold}{parsed.unit[0].upper()} condition={parsed.condition}"
    )

    # Step 4: Fetch ensemble weather forecast for this city and date
    ensemble = forecaster.get_ensemble(
        city=parsed.city,
        target_date=parsed.target_date,
        variable=parsed.variable,
    )

    if not ensemble:
        logger.warning(f"No ensemble data for {parsed.city} on {parsed.target_date}")
        return "error"

    # Step 5: Compute probability from ensemble
    model_probability = probability_engine.compute(
        ensemble=ensemble,
        threshold=parsed.threshold,
        condition=parsed.condition,
    )

    if model_probability is None:
        logger.warning("Probability computation failed")
        return "error"

    # Step 6: Compute edge
    yes_price = market.yes_price
    edge_result = edge_detector.compute(
        model_probability=model_probability,
        market_yes_price=yes_price,
        ensemble=ensemble,
    )

    if edge_result.signal == "PASS":
        logger.debug(f"No edge ({edge_result.edge:+.3f}) — skipping")
        return "skipped"

    # Step 7: Determine which token to buy
    if edge_result.signal == "BUY_YES":
        token_id = market.yes_token_id
        token_price = yes_price
        side = "YES"
    else:  # BUY_NO
        token_id = market.no_token_id
        token_price = market.no_price
        side = "NO"

    # Step 8: Kelly position sizing
    position = position_sizer.compute(
        model_probability=model_probability,
        market_price=yes_price,
        bankroll=bankroll,
        signal=edge_result.signal,
    )

    if position.usdc_size <= 0:
        logger.debug("Position size is 0 — skipping")
        return "skipped"

    # Step 9: Risk checks
    approved, reason, capped_size = risk_manager.check(
        market_id=market.market_id,
        city=parsed.city,
        proposed_size=position.usdc_size,
        bankroll=bankroll,
    )

    if not approved:
        logger.info(f"Trade rejected by risk manager: {reason}")
        return "skipped"

    logger.info(
        f"TRADE SIGNAL: BUY {side} | "
        f"city={parsed.city} date={parsed.target_date} "
        f"threshold={parsed.threshold}{parsed.unit[0].upper()} | "
        f"model={model_probability:.1%} market={yes_price:.1%} edge={edge_result.edge:+.1%} | "
        f"size=${capped_size:.2f} @ {token_price:.3f}"
    )

    # Step 10: Execute trade
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
        logger.success(
            f"Trade executed: {side} ${capped_size:.2f} on {market.question[:60]}..."
        )
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
