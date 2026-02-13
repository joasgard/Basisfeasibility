"""
Shared data loading for delta-neutral basis trading backtest.

Extracts the data-loading logic from backtest_comparison.py so that
both CLI scripts and the Streamlit app share a single source of truth.
"""

import json
import os
from datetime import datetime, timezone
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


def _load_json(filename):
    with open(os.path.join(DATA_DIR, filename)) as f:
        return json.load(f)


def _parse_date(ts):
    return ts[:10]


def load_data():
    """
    Load and align all data sources.

    Returns dict with keys:
        all_dates      - sorted list of date strings common to all sources
        sol_by_date    - {date: {"lend": float}}
        usdc_by_date   - {date: {"borrow": float}}
        hl_by_date     - {date: daily_funding_rate}
        drift_by_date  - {date: daily_funding_rate}
        price_by_date  - {date: {"close": float, "high": float, "low": float}}
    """
    sol_data = _load_json("kamino_sol_lending_rates.json")
    usdc_data = _load_json("kamino_usdc_borrowing_rates.json")
    hl_funding_raw = _load_json("sol_funding_history.json")
    drift_funding_raw = _load_json("drift_sol_funding_history.json")
    candles = _load_json("sol_daily_candles.json")

    # Index rates by date
    sol_by_date = {}
    for rec in sol_data:
        d = _parse_date(rec["timestamp"])
        sol_by_date[d] = {"lend": rec.get("apyBase") or 0.0}

    usdc_by_date = {}
    for rec in usdc_data:
        d = _parse_date(rec["timestamp"])
        usdc_by_date[d] = {"borrow": rec.get("apyBaseBorrow") or 0.0}

    # HL funding: aggregate hourly -> daily
    hl_daily = defaultdict(list)
    for rec in hl_funding_raw:
        ts_ms = rec.get("time", rec.get("timestamp", 0))
        if isinstance(ts_ms, str):
            d = ts_ms[:10]
        else:
            d = datetime.fromtimestamp(
                ts_ms / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d")
        hl_daily[d].append(float(rec.get("fundingRate", 0)))
    hl_by_date = {d: sum(rates) for d, rates in hl_daily.items()}

    # Drift funding: already daily-aggregated and HL-normalized
    drift_by_date = {rec["date"]: rec["rate"] for rec in drift_funding_raw}

    # Price candles
    price_by_date = {}
    for c in candles:
        dt = datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc)
        d = dt.strftime("%Y-%m-%d")
        price_by_date[d] = {
            "close": float(c["c"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
        }

    # Use dates common to ALL data sources
    all_dates = sorted(
        set(sol_by_date)
        & set(usdc_by_date)
        & set(hl_by_date)
        & set(drift_by_date)
        & set(price_by_date)
    )

    return {
        "all_dates": all_dates,
        "sol_by_date": sol_by_date,
        "usdc_by_date": usdc_by_date,
        "hl_by_date": hl_by_date,
        "drift_by_date": drift_by_date,
        "price_by_date": price_by_date,
    }
