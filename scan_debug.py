"""
Scan debug: shows exactly which filter drops each leg.
"""
from collections import defaultdict, Counter
from datetime import date

from market.polymarket_client import PolymarketClient
from market.market_parser import MarketParser
from trading.priority_scorer import PriorityScorer, MIN_DAY_SCORE, MIN_PRICE_SCORE, MAX_LEGS_PER_EVENT

client = PolymarketClient()
parser = MarketParser()
scorer = PriorityScorer()
today = date.today()

markets = client.fetch_weather_markets()
print(f"\n=== Fetched {len(markets)} legs  (today={today}) ===")
print(f"    Thresholds: day>={MIN_DAY_SCORE}  price>={MIN_PRICE_SCORE}  cap={MAX_LEGS_PER_EVENT}/event\n")

drop_reasons = Counter()
days_dist = Counter()
price_dist = Counter()

passing = []

for leg in markets:
    parsed = parser.parse(leg.question)
    if not parsed:
        drop_reasons["parse_fail"] += 1
        continue

    days_ahead = (parsed.target_date - today).days
    day_s = scorer.day_score(days_ahead)
    price_s = max(
        scorer.price_score(leg.yes_price, "BUY_YES"),
        scorer.price_score(leg.yes_price, "BUY_NO"),
    )

    days_dist[f"{days_ahead}d → day_score={day_s}"] += 1
    price_dist[f"YES={leg.yes_price:.2f} → price_score={price_s}"] += 1

    if day_s < MIN_DAY_SCORE:
        drop_reasons[f"day_score={day_s} (<{MIN_DAY_SCORE})"] += 1
    elif price_s < MIN_PRICE_SCORE:
        drop_reasons[f"price_score={price_s} (<{MIN_PRICE_SCORE})"] += 1
    else:
        passing.append((leg, parsed, days_ahead, day_s, price_s))

print("=== Drop reasons ===")
for reason, count in drop_reasons.most_common():
    print(f"  {count:3d}  {reason}")

print("\n=== Days distribution (all legs) ===")
for k, v in sorted(days_dist.items()):
    print(f"  {v:3d}  {k}")

print("\n=== Price distribution sample (first 20 unique) ===")
for k, v in list(price_dist.most_common(20)):
    print(f"  {v:3d}  {k}")

print(f"\n=== After thresholds: {len(passing)} legs pass ===")
if passing:
    # Group and cap
    events = defaultdict(list)
    for leg, parsed, days_ahead, day_s, price_s in passing:
        events[leg.event_id or leg.market_id].append((leg, parsed, days_ahead, day_s, price_s))

    total_after_cap = 0
    for event_id, legs in events.items():
        legs.sort(key=lambda x: x[3] * x[4], reverse=True)
        capped = legs[:MAX_LEGS_PER_EVENT]
        total_after_cap += len(capped)
        print(f"  EVENT {event_id}: {len(capped)} leg(s) → forecast")
        for leg, parsed, days_ahead, day_s, price_s in capped:
            print(f"    YES={leg.yes_price:.3f}  day={day_s}  price={price_s}  days={days_ahead}  {leg.question[:60]}")

    print(f"\n=== Final: {total_after_cap} forecast API calls ===")
