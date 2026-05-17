"""
One-time setup: approve Polymarket CLOB contracts to spend your USDC on Polygon.
Uses signature_type=2 (proxy wallet) — same as Polymarket web app.
"""
import os
from dotenv import load_dotenv
from eth_account import Account
load_dotenv()

private_key    = os.getenv("POLY_PRIVATE_KEY")
api_key        = os.getenv("POLY_API_KEY")
api_secret     = os.getenv("POLY_API_SECRET")
api_passphrase = os.getenv("POLY_API_PASSPHRASE")

address = Account.from_key(private_key).address
print(f"EOA address: {address}")

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, AssetType, BalanceAllowanceParams

creds  = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=private_key,
    creds=creds,
    signature_type=2,
    funder=address,
)

print("\n=== Current allowances (proxy wallet) ===")
try:
    info = client.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  COLLATERAL (USDC): {info}")
except Exception as e:
    print(f"  COLLATERAL: {e}")

print("\n=== Setting USDC allowance ===")
try:
    result = client.update_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"  OK: {result}")
except Exception as e:
    print(f"  FAIL: {e}")

print("\nDone. Run: python main.py --once")
