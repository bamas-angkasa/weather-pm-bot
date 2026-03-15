"""
Trade execution layer.

Dry-run mode: logs trades without any real transactions.
Live mode: submits orders via py-clob-client to Polymarket CLOB.

Usage:
  Set DRY_RUN=true in config.yaml to test without real trades.
  Set POLY_PRIVATE_KEY, POLY_API_KEY, etc. in .env for live trading.
"""
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from loguru import logger


@dataclass
class TradeResult:
    success: bool
    order_id: Optional[str]
    market_id: str
    side: str           # "YES" or "NO"
    size: float
    price: float
    dry_run: bool
    message: str


class Trader:
    """Executes trades on Polymarket or logs them in dry-run mode."""

    CLOB_HOST = "https://clob.polymarket.com"
    CHAIN_ID = 137  # Polygon

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._client = None

        if not dry_run:
            self._init_live_client()

    def _init_live_client(self):
        """Initialize py-clob-client for live trading."""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            private_key = os.getenv("POLY_PRIVATE_KEY")
            api_key = os.getenv("POLY_API_KEY")
            api_secret = os.getenv("POLY_API_SECRET")
            api_passphrase = os.getenv("POLY_API_PASSPHRASE")

            if not private_key:
                raise ValueError("POLY_PRIVATE_KEY not set in environment")

            creds = None
            if api_key:
                creds = ApiCreds(
                    api_key=api_key,
                    api_secret=api_secret,
                    api_passphrase=api_passphrase,
                )

            self._client = ClobClient(
                host=self.CLOB_HOST,
                chain_id=self.CHAIN_ID,
                key=private_key,
                creds=creds,
            )
            logger.info("CLOB client initialized for live trading")

        except ImportError:
            logger.error("py-clob-client not installed. Run: pip install py-clob-client")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            raise

    def buy(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
    ) -> TradeResult:
        """
        Place a buy order.

        Args:
            market_id: Polymarket market ID
            token_id: YES or NO token ID
            side: "YES" or "NO"
            size: USDC amount to spend
            price: Limit price (0–1)

        Returns:
            TradeResult
        """
        if self.dry_run:
            return self._dry_run_trade(market_id, side, size, price, token_id)
        else:
            return self._live_trade(market_id, token_id, side, size, price)

    def _dry_run_trade(
        self,
        market_id: str,
        side: str,
        size: float,
        price: float,
        token_id: str,
    ) -> TradeResult:
        """Simulate a trade without execution."""
        logger.info(
            f"[DRY RUN] BUY {side} | market={market_id[:8]}... | "
            f"size=${size:.2f} @ {price:.3f} | "
            f"shares={size/price:.2f} | token={token_id[:8]}..."
        )
        return TradeResult(
            success=True,
            order_id=f"dry_run_{datetime.utcnow().timestamp():.0f}",
            market_id=market_id,
            side=side,
            size=size,
            price=price,
            dry_run=True,
            message="Dry run — no real trade executed",
        )

    def _live_trade(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
    ) -> TradeResult:
        """Execute a real trade via py-clob-client."""
        try:
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY

            # Add small buffer above mid to improve fill probability
            limit_price = min(price + 0.01, 0.99)

            order_args = OrderArgs(
                price=limit_price,
                size=size,
                side=BUY,
                token_id=token_id,
            )

            signed_order = self._client.create_order(order_args)
            response = self._client.post_order(signed_order)

            order_id = response.get("orderID", "unknown")
            status = response.get("status", "unknown")

            logger.info(
                f"[LIVE] BUY {side} | market={market_id[:8]}... | "
                f"size=${size:.2f} @ {limit_price:.3f} | "
                f"order_id={order_id} status={status}"
            )

            return TradeResult(
                success=True,
                order_id=order_id,
                market_id=market_id,
                side=side,
                size=size,
                price=limit_price,
                dry_run=False,
                message=f"Order placed: {status}",
            )

        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            return TradeResult(
                success=False,
                order_id=None,
                market_id=market_id,
                side=side,
                size=size,
                price=price,
                dry_run=False,
                message=str(e),
            )
