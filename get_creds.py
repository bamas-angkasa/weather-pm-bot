import os
from py_clob_client.client import ClobClient

poly_private_key = os.getenv("POLY_PRIVATE_KEY")

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=poly_private_key,
)

creds = client.create_or_derive_api_creds()
print(f"POLY_API_KEY={creds.api_key}")
print(f"POLY_API_SECRET={creds.api_secret}")
print(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
