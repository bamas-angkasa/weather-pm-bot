"""
Scan debug: fetch markets, group by event, apply Phase 1 local filters,
show which legs would proceed to forecast fetch and their price/day scores.
No forecast API calls made here.
"""
from collections import defaultdict
from datetime import date

from market.polymarket_client import PolymarketClient
from market.market_parser import MarketParser
from trading.priority_scorer import PriorityScorer

client = PolymarketClient()
parser = MarketParser()
scorer = PriorityScorer()
today = date.today()

markets = client.fetch_weather_markets()
print(f"\n=== Fetched {len(markets)} legs ===\n")

# Group by event_id
events = defaultdict(list)
for m in markets:
    events[m.event_id or m.market_id].append(m)

print(f"=== {len(events)} events ===\n")

total_pass = 0
total_skip = 0

for event_id, legs in events.items():
    passing = []
    for leg in legs:
        parsed = parser.parse(leg.question)
        if not parsed:
            total_skip += 1
            continue

        days_ahead = (parsed.target_date - today).days
        day_s = scorer.day_score(days_ahead)
        price_s_yes = scorer.price_score(leg.yes_price, "BUY_YES")
        price_s_no  = scorer.price_score(leg.yes_price, "BUY_NO")
        best_price_s = max(price_s_yes, price_s_no)

        passes = day_s > 0.0 and best_price_s > 0.0
        if passes:
            passing.append((leg, parsed, days_ahead, day_s, best_price_s))
            total_pass += 1
        else:
            total_skip += 1

    if not passing:
        continue

    # Show event header from first passing leg
    sample = passing[0]
    print(f"EVENT [{event_id}]  legs={len(legs)}  passing={len(passing)}")
    for leg, parsed, days_ahead, day_s, price_s in passing:
        side = "YES" if leg.yes_price < 0.5 else "NO "
        token_price = leg.yes_price if side.strip() == "YES" else 1 - leg.yes_price
        print(
            f"  [{leg.market_id}] {side} @ {token_price:.3f}  "
            f"day={day_s:.1f}  price={price_s:.1f}  "
            f"days={days_ahead}  liq=${leg.liquidity:.0f}  "
            f"| {leg.question[:65]}"
        )
    print()

print(f"=== Phase 1 summary: {total_pass} pass forecast filter, {total_skip} skipped ===")
print(f"    (Only {total_pass} forecast API calls needed instead of {len(markets)})")
