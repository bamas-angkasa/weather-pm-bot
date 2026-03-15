"""
Test scan: fetch markets, group by event_id, show what the bot would evaluate.
Does NOT execute trades or fetch forecasts — just validates the scan + grouping logic.
"""
from collections import defaultdict
from market.polymarket_client import PolymarketClient

client = PolymarketClient()
markets = client.fetch_weather_markets()

print(f"\n=== Fetched {len(markets)} individual market legs ===\n")

# Group by event_id (same logic as run_cycle)
events = defaultdict(list)
for m in markets:
    key = m.event_id if m.event_id else m.market_id
    events[key].append(m)

print(f"=== Grouped into {len(events)} events ===\n")

for event_id, legs in events.items():
    # Show event title from first leg's question (strip the specific threshold part)
    sample_q = legs[0].question
    print(f"EVENT [{event_id}]  legs={len(legs)}")
    for leg in legs:
        print(
            f"  [{leg.market_id}] YES={leg.yes_price:.3f}  "
            f"liq=${leg.liquidity:.0f}  vol24h=${leg.volume_24h:.0f}  "
            f"| {leg.question[:70]}"
        )
    print()

print(f"Total events: {len(events)}  |  Total legs: {len(markets)}")
