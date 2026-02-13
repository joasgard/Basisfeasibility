"""
Scaled backtest — $1k, $10k, $100k capital at 2x, 3x, 4x leverage.

Slippage model based on current HL SOL-PERP orderbook snapshot:
  - Fit from observed data: ~0.06 bps for <$20k, scaling to ~1.7 bps at $200k
  - Applied on both entry and exit (round-trip)
  - Note: This is a single snapshot; historical liquidity may vary.
    We include 2x and 5x slippage multipliers as sensitivity.

Lending rate impact:
  - Kamino SOL pool: $261M supply — even $100k notional is 0.04% of pool
  - Kamino USDC pool: $203M supply — $100k borrow is 0.05% of pool
  - Rate impact: negligible at all tested sizes, not modeled
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from collections import defaultdict

# ── Load data ──────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

def load_or_fetch(filename, pool_id=None, endpoint="chartLendBorrow"):
    local = os.path.join(DATA_DIR, filename)
    if os.path.exists(local):
        with open(local) as f:
            return json.load(f)
    url = f"https://yields.llama.fi/{endpoint}/{pool_id}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())
    return data.get("data", data) if isinstance(data, dict) else data

def parse_date(ts): return ts[:10]

print("Loading data...")
sol_data = load_or_fetch("kamino_sol_lending_rates.json", "525b2dab-ea6a-4cbc-a07f-84ce561d1f83")
usdc_data = load_or_fetch("kamino_usdc_borrowing_rates.json", "d2141a59-c199-4be7-8d4b-c8223954836b")
hl_funding = load_or_fetch("sol_funding_history.json")

# Align by date
sol_by_date = {}
for rec in sol_data:
    d = parse_date(rec["timestamp"])
    sol_by_date[d] = {"lend": rec.get("apyBase") or 0.0, "borrow": rec.get("apyBaseBorrow") or 0.0}

usdc_by_date = {}
for rec in usdc_data:
    d = parse_date(rec["timestamp"])
    usdc_by_date[d] = {"lend": rec.get("apyBase") or 0.0, "borrow": rec.get("apyBaseBorrow") or 0.0}

hl_daily = defaultdict(list)
for rec in hl_funding:
    ts_ms = rec.get("time", rec.get("timestamp", 0))
    d = ts_ms[:10] if isinstance(ts_ms, str) else datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    hl_daily[d].append(float(rec.get("fundingRate", 0)))

hl_by_date = {d: sum(rates) for d, rates in hl_daily.items()}
all_dates = sorted(set(sol_by_date) & set(usdc_by_date) & set(hl_by_date))
print(f"Aligned: {len(all_dates)} days ({all_dates[0]} to {all_dates[-1]})")

# ── Slippage model ──────────────────────────────────────────────────
# Based on current HL SOL-PERP orderbook snapshot (Feb 2026):
#   $1k → 0.06 bps, $10k → 0.06 bps, $50k → 1.02 bps,
#   $100k → 1.35 bps, $200k → 1.66 bps
# Fit: piecewise — flat 0.1 bps below $20k, then ~0.008 bps per $10k above
#
# HL SOL-PERP 24h volume: $292M, OI: $271M
# Even $200k is 0.07% of daily volume — market impact is negligible.

def estimate_slippage_bps(notional_usd: float, multiplier: float = 1.0) -> float:
    """Estimate one-way slippage in bps based on HL orderbook model."""
    if notional_usd <= 20_000:
        base = 0.1
    else:
        excess = notional_usd - 20_000
        base = 0.1 + (excess / 10_000) * 0.09  # ~0.09 bps per $10k above $20k
    return base * multiplier

def slippage_cost(notional_usd: float, multiplier: float = 1.0) -> float:
    """Round-trip slippage cost in USD (entry + exit)."""
    bps = estimate_slippage_bps(notional_usd, multiplier)
    return notional_usd * bps / 10000 * 2  # x2 for round-trip

# ── Simulation ──────────────────────────────────────────────────────

CAPITALS = [1_000, 10_000, 100_000]
LEVERAGES = [2.0, 3.0, 4.0]
GAS_COST = 2.0
HL_FEE_BPS = 3.5
ASGARD_FEE_BPS = 15
SLIPPAGE_MULTIPLIERS = {"base": 1.0, "2x_slip": 2.0, "5x_slip": 5.0}

results = {}

for cap in CAPITALS:
    for lev in LEVERAGES:
        collateral = cap / 2
        notional = collateral * lev

        # Fees
        asgard_open = notional * (ASGARD_FEE_BPS / 10000)
        hl_open = notional * (HL_FEE_BPS / 10000)
        hl_close = hl_open
        platform_fees = asgard_open + hl_open + hl_close + GAS_COST

        # Slippage at different multipliers
        slip_base = slippage_cost(notional, 1.0)
        slip_2x = slippage_cost(notional, 2.0)
        slip_5x = slippage_cost(notional, 5.0)

        total_fees_base = platform_fees + slip_base
        total_fees_2x = platform_fees + slip_2x
        total_fees_5x = platform_fees + slip_5x

        # Daily P&L (same regardless of slippage — slippage only hits entry/exit)
        daily_pnl = []
        for d in all_dates:
            sol_lend_daily = (sol_by_date[d]["lend"] / 100) / 365
            usdc_borr_daily = (usdc_by_date[d]["borrow"] / 100) / 365
            funding_daily = hl_by_date[d]

            asgard_earn = notional * sol_lend_daily
            asgard_pay = (notional - collateral) * usdc_borr_daily
            hl_pnl = funding_daily * notional

            daily_pnl.append(asgard_earn - asgard_pay + hl_pnl)

        total_daily = sum(daily_pnl)

        # Compute metrics for each slippage scenario
        for slip_label, total_fees in [("base", total_fees_base), ("2x_slip", total_fees_2x), ("5x_slip", total_fees_5x)]:
            net = total_daily - total_fees
            ann = (net / cap) * (365 / len(all_dates)) * 100

            # Hold duration win rates
            hold_wr = {}
            hold_avg = {}
            hold_worst = {}
            for h in [7, 14, 30, 60, 90]:
                wins = 0; total = 0; rets = []
                for si in range(len(daily_pnl)):
                    if si + h > len(daily_pnl): break
                    cum = -total_fees + sum(daily_pnl[si:si+h])
                    total += 1
                    if cum > 0: wins += 1
                    rets.append(cum)
                hold_wr[h] = wins / total * 100 if total else 0
                hold_avg[h] = sum(rets) / len(rets) if rets else 0
                hold_worst[h] = min(rets) if rets else 0

            # Breakeven
            be_days = []
            for si in range(len(daily_pnl)):
                cum = -total_fees
                for off in range(len(daily_pnl) - si):
                    cum += daily_pnl[si + off]
                    if cum > 0:
                        be_days.append(off + 1)
                        break

            # Positive days
            pos_days = sum(1 for d in daily_pnl if d > 0)

            key = (cap, lev, slip_label)
            results[key] = {
                "cap": cap, "lev": lev, "slip": slip_label,
                "notional": notional,
                "platform_fees": platform_fees,
                "slippage": total_fees - platform_fees,
                "total_fees": total_fees,
                "gross": total_daily,
                "net": net,
                "ann": ann,
                "pos_day_pct": pos_days / len(daily_pnl) * 100,
                "hold_wr": hold_wr,
                "hold_avg": hold_avg,
                "hold_worst": hold_worst,
                "be_median": sorted(be_days)[len(be_days)//2] if be_days else 999,
                "be_pct": len(be_days) / len(all_dates) * 100,
            }

# ── Output ──────────────────────────────────────────────────────────

N = len(all_dates)
print(f"\n{'='*80}")
print(f"SCALED BACKTEST — $1k / $10k / $100k at 2x / 3x / 4x")
print(f"Period: {all_dates[0]} to {all_dates[-1]} ({N} days)")
print(f"{'='*80}")

# ── Fee & slippage structure ──
print(f"\n### Fee & Slippage Structure (per round-trip)")
print(f"{'Capital':>10} {'Lev':>4} {'Notional':>12} {'Platform':>10} {'Slip(1x)':>10} {'Slip(2x)':>10} {'Slip(5x)':>10} {'Total(1x)':>10} {'Gas%':>6}")
print("-" * 92)
for cap in CAPITALS:
    for lev in LEVERAGES:
        r = results[(cap, lev, "base")]
        r2 = results[(cap, lev, "2x_slip")]
        r5 = results[(cap, lev, "5x_slip")]
        gas_pct = GAS_COST / r["total_fees"] * 100
        print(f"${cap:>8,} {lev:>3.0f}x ${r['notional']:>10,.0f} ${r['platform_fees']:>8.2f} ${r['slippage']:>8.2f} ${r2['slippage']:>8.2f} ${r5['slippage']:>8.2f} ${r['total_fees']:>8.2f} {gas_pct:>5.1f}%")

# ── Annualized return ──
print(f"\n### Annualized Return (base slippage)")
print(f"{'':>10} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 52)
for cap in CAPITALS:
    row = [f"${cap:>8,}"]
    for lev in LEVERAGES:
        r = results[(cap, lev, "base")]
        row.append(f"{r['ann']:>+12.2f}%")
    print(f"{'':>0}".join(row))

# ── Net P&L ──
print(f"\n### Net P&L over {N} days (base slippage)")
print(f"{'':>10} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 52)
for cap in CAPITALS:
    row = [f"${cap:>8,}"]
    for lev in LEVERAGES:
        r = results[(cap, lev, "base")]
        row.append(f"${r['net']:>+11,.0f}")
    print(f"{'':>0}".join(row))

# ── Fees as % of gross ──
print(f"\n### Total Fees as % of Gross Profit")
print(f"{'':>10} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 52)
for cap in CAPITALS:
    row = [f"${cap:>8,}"]
    for lev in LEVERAGES:
        r = results[(cap, lev, "base")]
        pct = r["total_fees"] / r["gross"] * 100 if r["gross"] > 0 else 0
        row.append(f"{pct:>12.1f}%")
    print(f"{'':>0}".join(row))

# ── 30-day win rate ──
print(f"\n### 30-Day Win Rate (base slippage)")
print(f"{'':>10} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 52)
for cap in CAPITALS:
    row = [f"${cap:>8,}"]
    for lev in LEVERAGES:
        r = results[(cap, lev, "base")]
        row.append(f"{r['hold_wr'][30]:>12.1f}%")
    print(f"{'':>0}".join(row))

# ── 90-day win rate ──
print(f"\n### 90-Day Win Rate (base slippage)")
print(f"{'':>10} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 52)
for cap in CAPITALS:
    row = [f"${cap:>8,}"]
    for lev in LEVERAGES:
        r = results[(cap, lev, "base")]
        row.append(f"{r['hold_wr'][90]:>12.1f}%")
    print(f"{'':>0}".join(row))

# ── Worst-case returns ──
print(f"\n### Worst-Case Return (base slippage)")
print(f"{'Cap':>10} {'Hold':>6} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 58)
for cap in CAPITALS:
    for h in [30, 90]:
        row = [f"${cap:>8,}", f"{h:>4}d"]
        for lev in LEVERAGES:
            r = results[(cap, lev, "base")]
            row.append(f"${r['hold_worst'][h]:>+11,.0f}")
        print(f" ".join(row))

# ── Median breakeven ──
print(f"\n### Median Breakeven (days)")
print(f"{'':>10} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 52)
for cap in CAPITALS:
    row = [f"${cap:>8,}"]
    for lev in LEVERAGES:
        r = results[(cap, lev, "base")]
        row.append(f"{r['be_median']:>10} d")
    print(f"  ".join(row))

# ── Slippage sensitivity ──
print(f"\n### Slippage Sensitivity: Annualized Return")
print(f"{'Cap':>10} {'Lev':>4} {'Base (1x)':>12} {'Slip 2x':>12} {'Slip 5x':>12} {'Delta':>10}")
print("-" * 62)
for cap in CAPITALS:
    for lev in LEVERAGES:
        r1 = results[(cap, lev, "base")]
        r2 = results[(cap, lev, "2x_slip")]
        r5 = results[(cap, lev, "5x_slip")]
        delta = r5["ann"] - r1["ann"]
        print(f"${cap:>8,} {lev:>3.0f}x {r1['ann']:>+10.2f}% {r2['ann']:>+10.2f}% {r5['ann']:>+10.2f}% {delta:>+8.2f}pp")

print(f"\n### Slippage Sensitivity: 30-Day Win Rate")
print(f"{'Cap':>10} {'Lev':>4} {'Base (1x)':>12} {'Slip 2x':>12} {'Slip 5x':>12}")
print("-" * 52)
for cap in CAPITALS:
    for lev in LEVERAGES:
        r1 = results[(cap, lev, "base")]
        r2 = results[(cap, lev, "2x_slip")]
        r5 = results[(cap, lev, "5x_slip")]
        print(f"${cap:>8,} {lev:>3.0f}x {r1['hold_wr'][30]:>10.1f}% {r2['hold_wr'][30]:>10.1f}% {r5['hold_wr'][30]:>10.1f}%")

# ── Key insight ──
print(f"\n### Scaling Summary")
for cap in CAPITALS:
    r = results[(cap, 3.0, "base")]
    print(f"  ${cap:>8,} @ 3x:  net ${r['net']:>+10,.0f}  ann {r['ann']:>+.1f}%  fees/gross {r['total_fees']/r['gross']*100:.1f}%  30d-WR {r['hold_wr'][30]:.1f}%")

print(f"\n{'='*80}")
print("DONE")
print(f"{'='*80}")
