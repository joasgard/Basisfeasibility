"""Fetch Drift SOL-PERP funding rate history and normalize to HL-compatible format.

Drift Data API returns hourly funding records per day. Each record has:
  - fundingRate: rate in USDC per SOL per hour (already divided by FUNDING_RATE_PRECISION)
  - oraclePriceTwap: oracle price for the period

We normalize to HL format (rate as fraction of notional) by dividing each hourly
rate by its oracle price, then sum per day. This gives daily rates directly comparable
to HL's fundingRate field, usable with: fund_income = daily_rate * contracts * price.
"""

import json
import os
import time
import requests
from datetime import datetime, timedelta, timezone

API_BASE = "https://data.api.drift.trade/market/SOL-PERP/fundingRates"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "drift_sol_funding_history.json")

# Match HL data range
START_DATE = datetime(2024, 12, 31)
END_DATE = datetime(2026, 2, 13)


def fetch_day(dt):
    """Fetch all hourly funding records for a single day."""
    url = f"{API_BASE}/{dt.year}/{dt.month}/{dt.day}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        return []
    return data.get("records", [])


def main():
    all_daily = []
    current = START_DATE
    days_fetched = 0
    days_missing = 0

    print(f"Fetching Drift SOL-PERP funding: {START_DATE.date()} to {END_DATE.date()}")

    while current <= END_DATE:
        try:
            records = fetch_day(current)
        except Exception as e:
            print(f"  {current.date()}: ERROR - {e}")
            days_missing += 1
            current += timedelta(days=1)
            time.sleep(0.5)
            continue

        if not records:
            print(f"  {current.date()}: no records")
            days_missing += 1
            current += timedelta(days=1)
            time.sleep(0.2)
            continue

        # Normalize each hourly rate to fraction-of-notional (HL-compatible)
        hourly_rates = []
        for rec in records:
            rate = float(rec["fundingRate"])
            price = float(rec["oraclePriceTwap"])
            if price > 0:
                hourly_rates.append(rate / price)

        daily_rate = sum(hourly_rates)
        date_str = current.strftime("%Y-%m-%d")

        all_daily.append({
            "date": date_str,
            "rate": daily_rate,
            "n_records": len(records),
        })

        days_fetched += 1
        if days_fetched % 30 == 0:
            print(f"  {date_str}: {len(records)} records, daily_rate={daily_rate:.8f} ({days_fetched} days done)")

        current += timedelta(days=1)
        time.sleep(0.15)  # Rate limit

    # Save
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_daily, f, indent=2)

    print(f"\nDone: {days_fetched} days fetched, {days_missing} days missing")
    print(f"Saved to {OUTPUT_FILE}")

    # Summary stats
    rates = [d["rate"] for d in all_daily]
    avg = sum(rates) / len(rates)
    positive = sum(1 for r in rates if r > 0)
    print(f"Date range: {all_daily[0]['date']} to {all_daily[-1]['date']}")
    print(f"Avg daily rate: {avg:.8f} ({avg * 365 * 100:.2f}% annualized)")
    print(f"Positive days: {positive}/{len(rates)} ({positive/len(rates)*100:.1f}%)")


if __name__ == "__main__":
    main()
