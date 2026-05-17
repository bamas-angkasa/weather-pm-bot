import os
from dotenv import load_dotenv
from eth_account import Account
from py_clob_client.client import ClobClient

load_dotenv()
poly_private_key = os.getenv("POLY_PRIVATE_KEY")
address = Account.from_key(poly_private_key).address

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=poly_private_key,
    signature_type=0,   # EOA — must match what you use for trading
    funder=address,
)

creds = client.create_or_derive_api_creds()
print(f"POLY_API_KEY={creds.api_key}")
print(f"POLY_API_SECRET={creds.api_secret}")
print(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
