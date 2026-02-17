"""
Shared data loading for delta-neutral basis trading backtest.

Extracts the data-loading logic from backtest_comparison.py so that
both CLI scripts and the Streamlit app share a single source of truth.

Data sources:
  - DeFi Llama: Kamino SOL/USDC rates (legacy, Nov 2023+)
  - Asgard yieldscan API: Kamino, Drift, Marginfi lending/borrowing
    rates at hourly granularity (Apr-Jun 2025+), JitoSOL staking,
    Jupiter Perps borrow rates
  - Hyperliquid: SOL perp funding rates (hourly)
  - Drift: SOL perp funding rates (daily)
  - Hyperliquid: SOL daily price candles (OHLC)

For Kamino rates, Asgard data takes priority over DeFi Llama where
both exist for the same date.
"""

import json
import os
from datetime import datetime, timezone
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


def _load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def _parse_date(ts):
    return ts[:10]


def _asgard_hourly_to_daily(records, lend_key="avgLendingRate", borr_key="avgBorrowingRate"):
    """
    Aggregate Asgard hourly records to daily averages.

    Returns {date: {"lend": float, "borrow": float}}.
    """
    by_day = defaultdict(list)
    for rec in records:
        d = _parse_date(rec["hourBucket"])
        by_day[d].append(rec)

    result = {}
    for d, recs in by_day.items():
        avg_lend = sum(r.get(lend_key) or 0 for r in recs) / len(recs)
        avg_borr = sum(r.get(borr_key) or 0 for r in recs) / len(recs)
        result[d] = {"lend": avg_lend, "borrow": avg_borr}
    return result


def load_data():
    """
    Load and align all data sources.

    Returns dict with keys:
        all_dates      - sorted list of date strings common to all sources
        sol_by_date    - {date: {"lend": float}}  (Kamino SOL lending APY %)
        usdc_by_date   - {date: {"borrow": float}}  (Kamino USDC borrowing APY %)
        hl_by_date     - {date: daily_funding_rate}
        drift_by_date  - {date: daily_funding_rate}
        price_by_date  - {date: {"close", "high", "low"}}
        lending_protocols - {name: {date: {"lend", "borrow"}}} multi-protocol data
    """
    # -- Legacy DeFi Llama data (Kamino) --
    sol_data = _load_json("kamino_sol_lending_rates.json")
    usdc_data = _load_json("kamino_usdc_borrowing_rates.json")

    # -- Asgard hourly data --
    asgard_kamino_sol = _load_json("asgard_kamino_sol_hourly.json")
    asgard_kamino_usdc = _load_json("asgard_kamino_usdc_hourly.json")
    asgard_drift_sol = _load_json("asgard_drift_sol_hourly.json")
    asgard_drift_usdc = _load_json("asgard_drift_usdc_hourly.json")
    asgard_marginfi_sol = _load_json("asgard_marginfi_sol_hourly.json")
    asgard_marginfi_usdc = _load_json("asgard_marginfi_usdc_hourly.json")
    asgard_jitosol = _load_json("asgard_jitosol_staking_hourly.json")
    asgard_jup_perps = _load_json("asgard_jup_perps_sol_hourly.json")

    # -- Funding & price data --
    hl_funding_raw = _load_json("sol_funding_history.json")
    drift_funding_raw = _load_json("drift_sol_funding_history.json")
    candles = _load_json("sol_daily_candles.json")

    # ── Kamino SOL lending: DeFi Llama base, Asgard overlay ──
    sol_by_date = {}
    for rec in sol_data:
        d = _parse_date(rec["timestamp"])
        sol_by_date[d] = {"lend": rec.get("apyBase") or 0.0}

    if asgard_kamino_sol:
        asgard_sol_daily = _asgard_hourly_to_daily(asgard_kamino_sol)
        for d, vals in asgard_sol_daily.items():
            sol_by_date[d] = {"lend": vals["lend"]}

    # ── Kamino USDC borrowing: DeFi Llama base, Asgard overlay ──
    usdc_by_date = {}
    for rec in usdc_data:
        d = _parse_date(rec["timestamp"])
        usdc_by_date[d] = {"borrow": rec.get("apyBaseBorrow") or 0.0}

    if asgard_kamino_usdc:
        asgard_usdc_daily = _asgard_hourly_to_daily(asgard_kamino_usdc)
        for d, vals in asgard_usdc_daily.items():
            usdc_by_date[d] = {"borrow": vals["borrow"]}

    # ── HL funding: aggregate hourly -> daily ──
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

    # ── Drift perp funding: already daily-aggregated ──
    drift_by_date = {rec["date"]: rec["rate"] for rec in drift_funding_raw}

    # ── Price candles ──
    price_by_date = {}
    for c in candles:
        dt = datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc)
        d = dt.strftime("%Y-%m-%d")
        price_by_date[d] = {
            "close": float(c["c"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
        }

    # ── Multi-protocol lending data from Asgard ──
    lending_protocols = {}

    if asgard_kamino_sol and asgard_kamino_usdc:
        kamino_sol_d = _asgard_hourly_to_daily(asgard_kamino_sol)
        kamino_usdc_d = _asgard_hourly_to_daily(asgard_kamino_usdc)
        kamino_combined = {}
        for d in set(kamino_sol_d) & set(kamino_usdc_d):
            kamino_combined[d] = {
                "lend": kamino_sol_d[d]["lend"],
                "borrow": kamino_usdc_d[d]["borrow"],
            }
        lending_protocols["Kamino"] = kamino_combined

    if asgard_drift_sol and asgard_drift_usdc:
        drift_sol_d = _asgard_hourly_to_daily(asgard_drift_sol)
        drift_usdc_d = _asgard_hourly_to_daily(asgard_drift_usdc)
        drift_combined = {}
        for d in set(drift_sol_d) & set(drift_usdc_d):
            drift_combined[d] = {
                "lend": drift_sol_d[d]["lend"],
                "borrow": drift_usdc_d[d]["borrow"],
            }
        lending_protocols["Drift"] = drift_combined

    if asgard_marginfi_sol and asgard_marginfi_usdc:
        mfi_sol_d = _asgard_hourly_to_daily(asgard_marginfi_sol)
        mfi_usdc_d = _asgard_hourly_to_daily(asgard_marginfi_usdc)
        mfi_combined = {}
        for d in set(mfi_sol_d) & set(mfi_usdc_d):
            mfi_combined[d] = {
                "lend": mfi_sol_d[d]["lend"],
                "borrow": mfi_usdc_d[d]["borrow"],
            }
        lending_protocols["Marginfi"] = mfi_combined

    # ── JitoSOL staking yields ──
    jitosol_by_date = {}
    if asgard_jitosol:
        jito_daily = defaultdict(list)
        for rec in asgard_jitosol:
            d = _parse_date(rec["hourBucket"])
            # avgApy is in decimal form (0.06 = 6%), convert to %
            jito_daily[d].append((rec.get("avgApy") or 0) * 100)
        for d, rates in jito_daily.items():
            jitosol_by_date[d] = sum(rates) / len(rates)

    # ── Jupiter Perps SOL borrow rates ──
    jup_perps_by_date = {}
    if asgard_jup_perps:
        jup_daily = defaultdict(list)
        for rec in asgard_jup_perps:
            d = _parse_date(rec["hourBucket"])
            jup_daily[d].append(rec.get("avgHourlyBorrowRate") or 0)
        for d, rates in jup_daily.items():
            # avgHourlyBorrowRate is hourly %; annualize: avg_hourly * 8760
            jup_perps_by_date[d] = (sum(rates) / len(rates)) * 8760

    # ── Date alignment: common to core sources ──
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
        "lending_protocols": lending_protocols,
        "jitosol_by_date": jitosol_by_date,
        "jup_perps_by_date": jup_perps_by_date,
    }


def apply_lending_protocol(data, protocol):
    """
    Return a copy of *data* with sol_by_date, usdc_by_date, and all_dates
    swapped to reflect the chosen lending protocol.

    For "Kamino" the data is returned unchanged (full DeFi Llama + Asgard
    merged range).  For "Drift" or "Marginfi" the Asgard-only daily
    averages are used, and all_dates is re-intersected with funding + price
    data so the date range naturally shortens.
    """
    if protocol == "Kamino":
        return data

    proto_data = data["lending_protocols"].get(protocol)
    if not proto_data:
        return data

    sol_by_date = {d: {"lend": v["lend"]} for d, v in proto_data.items()}
    usdc_by_date = {d: {"borrow": v["borrow"]} for d, v in proto_data.items()}

    all_dates = sorted(
        set(sol_by_date)
        & set(usdc_by_date)
        & set(data["hl_by_date"])
        & set(data["drift_by_date"])
        & set(data["price_by_date"])
    )

    return {**data, "sol_by_date": sol_by_date, "usdc_by_date": usdc_by_date, "all_dates": all_dates}
