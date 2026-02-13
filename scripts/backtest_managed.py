"""
Managed Position Backtest — models liquidation risk, rebalancing, and APY-based exits.

Key differences from the naive backtest:
1. Tracks SOL price movement and margin on both legs
2. Rebalances (close + reopen) when either leg is within 10% of liquidation
3. Exits when trailing 7-day combined APY drops below a threshold
4. Re-enters when conditions improve
5. Charges full round-trip fees on every rotation

This is critical because SOL dropped 58% ($189 → $80) during the period,
which would have liquidated an unmanaged 3x long at -33%.
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from collections import defaultdict

# ── Load data ───────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

def load_or_fetch(filename, pool_id=None, endpoint="chartLendBorrow"):
    """Load from local data dir if available, otherwise fetch from DefiLlama."""
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
candles = load_or_fetch("sol_daily_candles.json")

# Index rates by date
sol_by_date = {}
for rec in sol_data:
    d = parse_date(rec["timestamp"])
    sol_by_date[d] = {"lend": rec.get("apyBase") or 0.0}

usdc_by_date = {}
for rec in usdc_data:
    d = parse_date(rec["timestamp"])
    usdc_by_date[d] = {"borrow": rec.get("apyBaseBorrow") or 0.0}

hl_daily = defaultdict(list)
for rec in hl_funding:
    ts_ms = rec.get("time", rec.get("timestamp", 0))
    d = ts_ms[:10] if isinstance(ts_ms, str) else datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    hl_daily[d].append(float(rec.get("fundingRate", 0)))
hl_by_date = {d: sum(rates) for d, rates in hl_daily.items()}

# Index prices by date
price_by_date = {}
for c in candles:
    dt = datetime.fromtimestamp(c['t']/1000, tz=timezone.utc)
    d = dt.strftime("%Y-%m-%d")
    price_by_date[d] = {
        "close": float(c['c']),
        "high": float(c['h']),
        "low": float(c['l']),
    }

all_dates = sorted(set(sol_by_date) & set(usdc_by_date) & set(hl_by_date) & set(price_by_date))
print(f"Aligned: {len(all_dates)} days ({all_dates[0]} to {all_dates[-1]})")
print(f"SOL price: ${price_by_date[all_dates[0]]['close']:.2f} → ${price_by_date[all_dates[-1]]['close']:.2f} "
      f"({(price_by_date[all_dates[-1]]['close']/price_by_date[all_dates[0]]['close']-1)*100:+.1f}%)")

# ── Simulation engine ───────────────────────────────────────────────

ASGARD_FEE_BPS = 15
HL_FEE_BPS = 3.5
GAS_COST = 2.0

# Liquidation thresholds (simplified)
# At Nx leverage, liquidation occurs at ~1/N price move from entry
# Maintenance margin buffer ~5%
MAINTENANCE_MARGIN = 0.05

def run_simulation(
    initial_capital: float,
    leverage: float,
    liq_distance_trigger: float = 0.10,  # rebalance when within 10% of liq price
    min_apy_threshold: float = 0.0,      # 0 = no APY-based exit
    use_intraday: bool = True,           # check high/low for liquidation
):
    """Run a managed position simulation.

    Returns dict with performance metrics and event log.
    """
    capital = initial_capital
    collateral = capital / 2

    # Fee calculator
    def calc_fees(notional):
        asgard = notional * (ASGARD_FEE_BPS / 10000)
        hl = notional * (HL_FEE_BPS / 10000) * 2  # open + close
        return asgard + hl + GAS_COST

    # Position state
    in_position = False
    entry_price = 0.0
    entry_date = ""
    position_capital = 0.0  # capital allocated to current position

    # Liquidation prices
    long_liq_price = 0.0    # Asgard long liquidates below this
    short_liq_price = 0.0   # HL short liquidates above this

    # Tracking
    total_fees_paid = 0.0
    total_carry_earned = 0.0
    total_funding_earned = 0.0
    rotations = 0  # number of close+reopen cycles
    forced_rebalances = 0  # due to liquidation proximity
    apy_exits = 0
    liquidations = 0  # positions that would have been liquidated
    daily_equity = []
    events = []
    trailing_apy = []  # trailing 7d daily returns for APY calc

    for i, date in enumerate(all_dates):
        price = price_by_date[date]
        sol_close = price["close"]
        sol_high = price["high"]
        sol_low = price["low"]

        # If not in position, check if we should enter
        if not in_position:
            # Enter if we have capital and (no APY filter or conditions look ok)
            if capital > 100:  # minimum viable
                # Open position
                in_position = True
                entry_price = sol_close
                position_capital = capital
                notional = (position_capital / 2) * leverage
                fees = calc_fees(notional)
                capital -= fees
                position_capital = capital
                total_fees_paid += fees
                rotations += 1

                # Calculate liquidation prices
                # Long: liq when price drops by 1/leverage (minus maintenance buffer)
                long_liq_price = entry_price * (1 - (1 - MAINTENANCE_MARGIN) / leverage)
                # Short: liq when price rises by 1/leverage (minus maintenance buffer)
                short_liq_price = entry_price * (1 + (1 - MAINTENANCE_MARGIN) / leverage)

                entry_date = date
                events.append({"date": date, "type": "OPEN", "price": sol_close,
                              "capital": capital, "fees": fees,
                              "long_liq": long_liq_price, "short_liq": short_liq_price})

            daily_equity.append({"date": date, "equity": capital})
            continue

        # We are in position — calculate today's yield
        coll = position_capital / 2
        notional = coll * leverage

        sol_lend = (sol_by_date.get(date, {}).get("lend", 0) / 100) / 365
        usdc_borr = (usdc_by_date.get(date, {}).get("borrow", 0) / 100) / 365
        funding = hl_by_date.get(date, 0)

        carry = notional * sol_lend - (notional - coll) * usdc_borr
        fund_income = funding * notional
        daily_return = carry + fund_income

        total_carry_earned += carry
        total_funding_earned += fund_income
        capital += daily_return
        position_capital += daily_return

        # Track trailing APY
        daily_return_pct = daily_return / (position_capital - daily_return) if position_capital > daily_return else 0
        trailing_apy.append(daily_return_pct)
        if len(trailing_apy) > 7:
            trailing_apy.pop(0)

        # ── Check liquidation proximity ──
        need_rebalance = False
        rebalance_reason = ""

        check_low = sol_low if use_intraday else sol_close
        check_high = sol_high if use_intraday else sol_close

        # Long leg: check if low approaches liquidation
        if check_low <= long_liq_price:
            # Would have been liquidated!
            liquidations += 1
            need_rebalance = True
            rebalance_reason = f"LIQUIDATION_LONG (low=${check_low:.2f} <= liq=${long_liq_price:.2f})"
        elif long_liq_price > 0:
            long_distance = (check_low - long_liq_price) / check_low
            if long_distance < liq_distance_trigger:
                need_rebalance = True
                rebalance_reason = f"MARGIN_LONG ({long_distance*100:.1f}% from liq)"

        # Short leg: check if high approaches liquidation
        if check_high >= short_liq_price:
            liquidations += 1
            need_rebalance = True
            rebalance_reason = f"LIQUIDATION_SHORT (high=${check_high:.2f} >= liq=${short_liq_price:.2f})"
        elif short_liq_price > 0:
            short_distance = (short_liq_price - check_high) / check_high
            if short_distance < liq_distance_trigger:
                need_rebalance = True
                rebalance_reason = f"MARGIN_SHORT ({short_distance*100:.1f}% from liq)"

        # ── Check APY threshold ──
        if min_apy_threshold > 0 and len(trailing_apy) >= 7:
            avg_daily = sum(trailing_apy) / len(trailing_apy)
            trailing_annual = avg_daily * 365 * 100
            if trailing_annual < min_apy_threshold:
                need_rebalance = True
                rebalance_reason = f"LOW_APY ({trailing_annual:.1f}% < {min_apy_threshold}%)"
                apy_exits += 1

        if need_rebalance:
            # Close position (no close fee on Asgard, but HL close fee already in calc_fees)
            # The net equity is current capital (already includes daily P&L)
            events.append({"date": date, "type": "CLOSE", "price": sol_close,
                          "capital": capital, "reason": rebalance_reason})

            if "LIQUIDATION" in rebalance_reason:
                # If actually liquidated, we lose more — estimate 5% penalty
                liq_penalty = position_capital * 0.05
                capital -= liq_penalty
                events.append({"date": date, "type": "LIQ_PENALTY", "amount": -liq_penalty})

            in_position = False
            forced_rebalances += 1

            # Re-enter on next day (skip today to avoid same-day issues)
            # The entry logic at the top of the loop will handle it

        daily_equity.append({"date": date, "equity": capital})

    # Close any remaining position at the end
    if in_position:
        events.append({"date": all_dates[-1], "type": "FINAL_CLOSE", "capital": capital})

    # Compute metrics
    total_return = capital - initial_capital
    total_return_pct = total_return / initial_capital * 100
    ann_return = total_return_pct * (365 / len(all_dates))

    # Drawdown
    peak = initial_capital
    max_dd = 0
    for de in daily_equity:
        peak = max(peak, de["equity"])
        dd = (peak - de["equity"]) / peak
        max_dd = max(max_dd, dd)

    return {
        "initial_capital": initial_capital,
        "leverage": leverage,
        "final_capital": capital,
        "total_return": total_return,
        "return_pct": total_return_pct,
        "ann_return": ann_return,
        "total_fees": total_fees_paid,
        "carry_earned": total_carry_earned,
        "funding_earned": total_funding_earned,
        "gross_income": total_carry_earned + total_funding_earned,
        "rotations": rotations,
        "forced_rebalances": forced_rebalances,
        "liquidations": liquidations,
        "apy_exits": apy_exits,
        "max_drawdown_pct": max_dd * 100,
        "fees_per_gross_pct": total_fees_paid / max(total_carry_earned + total_funding_earned, 0.01) * 100,
        "events": events,
        "daily_equity": daily_equity,
        "liq_trigger": liq_distance_trigger,
        "apy_threshold": min_apy_threshold,
    }


# ── Run scenarios ───────────────────────────────────────────────────

print("\n" + "=" * 85)
print("MANAGED POSITION BACKTEST")
print(f"Period: {all_dates[0]} to {all_dates[-1]} ({len(all_dates)} days)")
print(f"SOL: ${price_by_date[all_dates[0]]['close']:.2f} → ${price_by_date[all_dates[-1]]['close']:.2f} ({(price_by_date[all_dates[-1]]['close']/price_by_date[all_dates[0]]['close']-1)*100:+.1f}%)")
print("=" * 85)

# Scenario matrix
CAPITALS = [1_000, 10_000, 100_000]
LEVERAGES = [2.0, 3.0, 4.0]

# First: show naive vs managed at $1k to highlight the difference
print("\n### 1. Naive (buy-and-hold) vs Managed — $1k capital")
print(f"\n{'Strategy':<30} {'2x':>16} {'3x':>16} {'4x':>16}")
print("-" * 78)

for label, liq_trigger, apy_thresh in [
    ("Naive (no management)", 0.0, 0.0),
    ("Managed (10% liq buffer)", 0.10, 0.0),
    ("Managed + APY exit (<0%)", 0.10, 0.0),  # will set separately
    ("Managed + APY exit (<10%)", 0.10, 10.0),
]:
    row = [f"{label:<30}"]
    for lev in LEVERAGES:
        if label == "Naive (no management)":
            r = run_simulation(1000, lev, liq_distance_trigger=0.0, min_apy_threshold=0.0)
        elif label == "Managed + APY exit (<0%)":
            r = run_simulation(1000, lev, liq_distance_trigger=0.10, min_apy_threshold=0.0)
        else:
            r = run_simulation(1000, lev, liq_distance_trigger=liq_trigger, min_apy_threshold=apy_thresh)
        row.append(f"{r['ann_return']:>+8.1f}% ({r['rotations']:>2}r)")
    print("".join(row))

# Detailed comparison at 3x
print(f"\n### 2. Detailed Comparison — 3x leverage, $1k capital")
print(f"\n{'Metric':<28} {'Naive':>14} {'Managed':>14} {'Mgd+APY10%':>14}")
print("-" * 70)

naive = run_simulation(1000, 3.0, liq_distance_trigger=0.0, min_apy_threshold=0.0)
managed = run_simulation(1000, 3.0, liq_distance_trigger=0.10, min_apy_threshold=0.0)
managed_apy = run_simulation(1000, 3.0, liq_distance_trigger=0.10, min_apy_threshold=10.0)

metrics = [
    ("Final capital", "final_capital", "${:.2f}"),
    ("Annualized return", "ann_return", "{:+.2f}%"),
    ("Total fees paid", "total_fees", "${:.2f}"),
    ("Gross income (carry+fund)", "gross_income", "${:.2f}"),
    ("Fees as % of gross", "fees_per_gross_pct", "{:.1f}%"),
    ("Rotations (open+close)", "rotations", "{}"),
    ("Forced rebalances", "forced_rebalances", "{}"),
    ("Liquidations (would-be)", "liquidations", "{}"),
    ("Max drawdown", "max_drawdown_pct", "{:.1f}%"),
]
for label, key, fmt in metrics:
    nv = fmt.format(naive[key])
    mv = fmt.format(managed[key])
    mav = fmt.format(managed_apy[key])
    print(f"{label:<28} {nv:>14} {mv:>14} {mav:>14}")

# Show rebalance events for managed 3x
print(f"\n### 3. Rebalance Events — 3x, $1k, managed")
for ev in managed['events']:
    if ev['type'] in ('CLOSE', 'OPEN', 'LIQ_PENALTY'):
        reason = ev.get('reason', '')
        if ev['type'] == 'OPEN':
            print(f"  {ev['date']}  OPEN   @ ${ev['price']:.2f}  capital=${ev['capital']:.2f}  "
                  f"liq_range=[${ev['long_liq']:.2f}, ${ev['short_liq']:.2f}]")
        elif ev['type'] == 'LIQ_PENALTY':
            print(f"  {ev['date']}  LIQ_PENALTY  ${ev['amount']:+.2f}")
        else:
            print(f"  {ev['date']}  CLOSE  @ ${ev['price']:.2f}  capital=${ev['capital']:.2f}  reason: {reason}")

# Full matrix: managed with 10% liq buffer
print(f"\n### 4. Managed Performance Matrix (10% liquidation buffer)")
print(f"\n{'':>10} {'$1k':>24} {'$10k':>24} {'$100k':>24}")
print(f"{'':>10} {'Ann':>8} {'Fees':>8} {'Rot':>6} {'Ann':>8} {'Fees':>8} {'Rot':>6} {'Ann':>8} {'Fees':>8} {'Rot':>6}")
print("-" * 82)
for lev in LEVERAGES:
    row = [f"  {lev:.0f}x     "]
    for cap in CAPITALS:
        r = run_simulation(cap, lev, liq_distance_trigger=0.10, min_apy_threshold=0.0)
        row.append(f"{r['ann_return']:>+7.1f}% ${r['total_fees']:>6.0f} {r['rotations']:>5}")
        row.append(" ")
    print("".join(row))

# Compare naive vs managed at all sizes
print(f"\n### 5. Naive vs Managed — Annualized Return")
print(f"\n{'Cap':>10} {'Lev':>4} {'Naive':>10} {'Managed':>10} {'Delta':>10} {'Rotations':>10} {'Liq Events':>12}")
print("-" * 68)
for cap in CAPITALS:
    for lev in LEVERAGES:
        naive_r = run_simulation(cap, lev, liq_distance_trigger=0.0, min_apy_threshold=0.0)
        mgd_r = run_simulation(cap, lev, liq_distance_trigger=0.10, min_apy_threshold=0.0)
        delta = mgd_r['ann_return'] - naive_r['ann_return']
        print(f"${cap:>8,} {lev:>3.0f}x {naive_r['ann_return']:>+8.1f}% {mgd_r['ann_return']:>+8.1f}% {delta:>+8.1f}pp {mgd_r['rotations']:>8} {mgd_r['liquidations']:>10}")

# What about different liq buffer levels?
print(f"\n### 6. Liquidation Buffer Sensitivity — $10k, 3x")
print(f"\n{'Buffer':>8} {'Ann Return':>12} {'Rotations':>12} {'Total Fees':>12} {'Fees/Gross':>12} {'Max DD':>10}")
print("-" * 68)
for buffer in [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]:
    r = run_simulation(10_000, 3.0, liq_distance_trigger=buffer, min_apy_threshold=0.0)
    print(f"  {buffer*100:>4.0f}%   {r['ann_return']:>+10.1f}% {r['rotations']:>10} ${r['total_fees']:>10.0f} {r['fees_per_gross_pct']:>10.1f}% {r['max_drawdown_pct']:>8.1f}%")

# Fee impact with rotations
print(f"\n### 7. Fee Impact with Rotations — $10k, 3x, managed (10% buffer)")
print(f"\n{'Asgard Fee':>12} {'Ann Return':>12} {'Total Fees':>12} {'Delta vs 0.15%':>16}")
print("-" * 54)
for fee_bps in [15, 10, 5, 0]:
    # Temporarily override fee
    import copy
    old_fee = ASGARD_FEE_BPS
    ASGARD_FEE_BPS = fee_bps
    r = run_simulation(10_000, 3.0, liq_distance_trigger=0.10, min_apy_threshold=0.0)
    ASGARD_FEE_BPS = old_fee
    baseline_r = run_simulation(10_000, 3.0, liq_distance_trigger=0.10, min_apy_threshold=0.0)
    delta = r['ann_return'] - baseline_r['ann_return']
    print(f"  {fee_bps/100:>6.2f}%    {r['ann_return']:>+10.1f}% ${r['total_fees']:>10.0f} {delta:>+14.2f}pp")

print(f"\n{'='*85}")
print("DONE")
print(f"{'='*85}")
