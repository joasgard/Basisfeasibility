"""
Full Historical Backtest — Basis Trading Strategy
Uses real data from:
  - DefiLlama: Kamino SOL lending rates + USDC borrowing rates
  - Hyperliquid: SOL perpetual funding rates

Strategy: Long SOL on Asgard (leveraged lending) + Short SOL perp on Hyperliquid
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from collections import defaultdict

# ──────────────────────────────────────────────────────────────────────
# 1. Load data (local files preferred, DefiLlama fallback)
# ──────────────────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
KAMINO_SOL_POOL = "525b2dab-ea6a-4cbc-a07f-84ce561d1f83"
KAMINO_USDC_POOL = "d2141a59-c199-4be7-8d4b-c8223954836b"

def load_or_fetch(filename, pool_id=None, endpoint="chartLendBorrow"):
    local = os.path.join(DATA_DIR, filename)
    if os.path.exists(local):
        with open(local) as f:
            return json.load(f)
    url = f"https://yields.llama.fi/{endpoint}/{pool_id}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())
    return data.get("data", data) if isinstance(data, dict) else data

print("Loading Kamino SOL lending/borrowing rates...")
sol_lending_data = load_or_fetch("kamino_sol_lending_rates.json", KAMINO_SOL_POOL)
print(f"  → {len(sol_lending_data)} records")

print("Loading Kamino USDC lending/borrowing rates...")
usdc_borrow_data = load_or_fetch("kamino_usdc_borrowing_rates.json", KAMINO_USDC_POOL)
print(f"  → {len(usdc_borrow_data)} records")

print("Loading Hyperliquid SOL funding rates...")
hl_funding = load_or_fetch("sol_funding_history.json")
print(f"  → {len(hl_funding)} hourly records")

# ──────────────────────────────────────────────────────────────────────
# 2. Align data by date
# ──────────────────────────────────────────────────────────────────────

def parse_date(ts: str) -> str:
    """Extract YYYY-MM-DD from ISO timestamp."""
    return ts[:10]

# Index DeFiLlama by date
sol_by_date = {}
for rec in sol_lending_data:
    d = parse_date(rec["timestamp"])
    sol_by_date[d] = {
        "sol_lending_apy": rec.get("apyBase") or 0.0,
        "sol_borrow_apy": rec.get("apyBaseBorrow") or 0.0,
    }

usdc_by_date = {}
for rec in usdc_borrow_data:
    d = parse_date(rec["timestamp"])
    usdc_by_date[d] = {
        "usdc_lending_apy": rec.get("apyBase") or 0.0,
        "usdc_borrow_apy": rec.get("apyBaseBorrow") or 0.0,
    }

# Aggregate HL funding to daily (sum of hourly rates → daily rate)
hl_daily = defaultdict(list)
for rec in hl_funding:
    ts_ms = rec.get("time", rec.get("timestamp", 0))
    if isinstance(ts_ms, str):
        d = ts_ms[:10]
    else:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        d = dt.strftime("%Y-%m-%d")
    rate = float(rec.get("fundingRate", 0))
    hl_daily[d].append(rate)

hl_by_date = {}
for d, rates in hl_daily.items():
    daily_sum = sum(rates)
    daily_annual = daily_sum * 365 * 100  # Convert to APY %
    hl_by_date[d] = {
        "funding_daily_sum": daily_sum,
        "funding_apy": daily_annual,
        "funding_count": len(rates),
        "avg_hourly": sum(rates) / len(rates),
    }

# Find overlapping dates
all_dates = sorted(set(sol_by_date) & set(usdc_by_date) & set(hl_by_date))
print(f"\nOverlapping dates with all 3 data sources: {len(all_dates)}")
if all_dates:
    print(f"  Range: {all_dates[0]} to {all_dates[-1]}")

# ──────────────────────────────────────────────────────────────────────
# 3. Strategy simulation parameters
# ──────────────────────────────────────────────────────────────────────

CAPITAL = 1000          # $1000 total
LEVERAGE = 3.0          # 3x on Asgard
ASGARD_FEE_BPS = 15     # 0.15%
HL_TAKER_FEE_BPS = 3.5  # 0.035% per trade
GAS_COST = 2.0          # ~$2 gas

# Split: 50/50 between legs
ASGARD_COLLATERAL = CAPITAL / 2    # $500
ASGARD_NOTIONAL = ASGARD_COLLATERAL * LEVERAGE  # $1500
HL_NOTIONAL = ASGARD_NOTIONAL      # $1500

# Entry costs (one-time)
ASGARD_OPEN_FEE = ASGARD_NOTIONAL * (ASGARD_FEE_BPS / 10000)  # $2.25
HL_OPEN_FEE = HL_NOTIONAL * (HL_TAKER_FEE_BPS / 10000)        # $0.525
HL_CLOSE_FEE = HL_NOTIONAL * (HL_TAKER_FEE_BPS / 10000)       # $0.525
TOTAL_FEES = ASGARD_OPEN_FEE + HL_OPEN_FEE + HL_CLOSE_FEE + GAS_COST

print(f"\n=== Strategy Parameters ===")
print(f"Capital: ${CAPITAL}")
print(f"Leverage: {LEVERAGE}x")
print(f"Asgard collateral: ${ASGARD_COLLATERAL}, notional: ${ASGARD_NOTIONAL}")
print(f"HL notional: ${HL_NOTIONAL}")
print(f"Total entry+exit fees: ${TOTAL_FEES:.2f}")

# ──────────────────────────────────────────────────────────────────────
# 4. Build daily P&L series
# ──────────────────────────────────────────────────────────────────────

daily_pnl = []

for d in all_dates:
    sol = sol_by_date[d]
    usdc = usdc_by_date[d]
    hl = hl_by_date[d]

    # Asgard leg daily P&L:
    # Earn: SOL lending APY on full notional ($1500)
    # Pay: USDC borrowing APY on borrowed amount ($1000 = notional - collateral)
    sol_lending_daily = (sol["sol_lending_apy"] / 100) / 365
    usdc_borrow_daily = (usdc["usdc_borrow_apy"] / 100) / 365

    asgard_earn = ASGARD_NOTIONAL * sol_lending_daily    # Earn on full position
    asgard_pay = (ASGARD_NOTIONAL - ASGARD_COLLATERAL) * usdc_borrow_daily  # Pay on borrowed

    # HL leg daily P&L:
    # In Hyperliquid, positive funding rate = longs pay shorts.
    # Short position earns when funding is positive (standard perp convention).
    # P&L for short = +funding_rate * notional
    hl_pnl = hl["funding_daily_sum"] * HL_NOTIONAL

    net_daily = asgard_earn - asgard_pay + hl_pnl

    daily_pnl.append({
        "date": d,
        "asgard_earn": asgard_earn,
        "asgard_pay": asgard_pay,
        "asgard_net": asgard_earn - asgard_pay,
        "hl_pnl": hl_pnl,
        "net_daily": net_daily,
        "sol_lending_apy": sol["sol_lending_apy"],
        "usdc_borrow_apy": usdc["usdc_borrow_apy"],
        "hl_funding_apy": hl["funding_apy"],
        "funding_daily": hl["funding_daily_sum"],
    })

print(f"\nDaily P&L computed for {len(daily_pnl)} days")

# ──────────────────────────────────────────────────────────────────────
# 5. Analysis
# ──────────────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("FULL HISTORICAL BACKTEST — Basis Trading Strategy")
print("=" * 70)

# --- 5a. Rate summary ---
print("\n### A. Rate Summary (daily averages across all overlapping dates)")
avg_sol = sum(d["sol_lending_apy"] for d in daily_pnl) / len(daily_pnl)
avg_usdc = sum(d["usdc_borrow_apy"] for d in daily_pnl) / len(daily_pnl)
avg_hl = sum(d["hl_funding_apy"] for d in daily_pnl) / len(daily_pnl)
avg_net_carry = sum(d["asgard_net"] for d in daily_pnl) / len(daily_pnl) * 365 / ASGARD_COLLATERAL * 100

print(f"  SOL lending APY (avg):    {avg_sol:.2f}%")
print(f"  USDC borrowing APY (avg): {avg_usdc:.2f}%")
print(f"  Net Asgard carry APY:     {avg_net_carry:.2f}%  (on collateral)")
print(f"  HL funding APY (avg):     {avg_hl:.2f}%  (positive = longs pay shorts)")
print(f"  → HL short P&L sign:      {'Favorable (shorts paid)' if avg_hl > 0 else 'Unfavorable (shorts pay)'}")

# --- 5b. Monthly breakdown ---
print("\n### B. Monthly Breakdown")
print(f"{'Month':<10} {'SOL Lend':>10} {'USDC Borr':>10} {'Net Carry':>10} {'HL Fund':>10} {'Strategy':>10} {'Cum P&L':>10}")
print("-" * 72)

monthly = defaultdict(list)
for d in daily_pnl:
    month = d["date"][:7]
    monthly[month].append(d)

cumulative_pnl = -TOTAL_FEES  # Start with entry fees deducted
for month in sorted(monthly):
    days = monthly[month]
    m_sol = sum(d["sol_lending_apy"] for d in days) / len(days)
    m_usdc = sum(d["usdc_borrow_apy"] for d in days) / len(days)
    m_asgard_net = sum(d["asgard_net"] for d in days)
    m_hl = sum(d["hl_pnl"] for d in days)
    m_total = m_asgard_net + m_hl
    m_carry_apy = m_asgard_net / len(days) * 365 / ASGARD_COLLATERAL * 100
    m_fund_apy = sum(d["hl_funding_apy"] for d in days) / len(days)
    m_strat_apy = (m_carry_apy + (m_hl / len(days) * 365 / (CAPITAL / 2) * 100))
    cumulative_pnl += m_total

    print(f"{month:<10} {m_sol:>9.2f}% {m_usdc:>9.2f}% {m_carry_apy:>9.1f}% {m_fund_apy:>+9.1f}% {m_strat_apy:>+9.1f}% ${cumulative_pnl:>8.2f}")

# --- 5c. Strategy P&L simulation ---
print("\n### C. Entry-Point Simulation")
print(f"Simulating entering on each possible day, holding for N days...")
print(f"Entry cost deducted: ${TOTAL_FEES:.2f}")

# For each possible entry day, compute cumulative P&L over holding periods
hold_periods = [7, 14, 30, 60, 90]
results = {h: {"profitable": 0, "total": 0, "returns": []} for h in hold_periods}

for start_idx in range(len(daily_pnl)):
    for hold in hold_periods:
        if start_idx + hold > len(daily_pnl):
            continue
        cum = -TOTAL_FEES
        for day_idx in range(start_idx, start_idx + hold):
            cum += daily_pnl[day_idx]["net_daily"]
        results[hold]["total"] += 1
        if cum > 0:
            results[hold]["profitable"] += 1
        results[hold]["returns"].append(cum)

print(f"\n{'Hold Days':<12} {'Win Rate':>10} {'Avg Return':>12} {'Med Return':>12} {'Best':>10} {'Worst':>10}")
print("-" * 70)
for h in hold_periods:
    r = results[h]
    if r["total"] == 0:
        continue
    wr = r["profitable"] / r["total"] * 100
    avg_r = sum(r["returns"]) / len(r["returns"])
    sorted_r = sorted(r["returns"])
    med_r = sorted_r[len(sorted_r) // 2]
    best = sorted_r[-1]
    worst = sorted_r[0]
    print(f"{h:>5} days   {wr:>8.1f}%   ${avg_r:>10.2f}   ${med_r:>10.2f}   ${best:>8.2f}   ${worst:>8.2f}")

# --- 5d. Breakeven analysis ---
print("\n### D. Days to Breakeven")
breakeven_days_list = []
never_breakeven = 0
for start_idx in range(len(daily_pnl)):
    cum = -TOTAL_FEES
    broke_even = False
    for day_offset in range(len(daily_pnl) - start_idx):
        cum += daily_pnl[start_idx + day_offset]["net_daily"]
        if cum > 0:
            breakeven_days_list.append(day_offset + 1)
            broke_even = True
            break
    if not broke_even:
        never_breakeven += 1

if breakeven_days_list:
    avg_be = sum(breakeven_days_list) / len(breakeven_days_list)
    sorted_be = sorted(breakeven_days_list)
    med_be = sorted_be[len(sorted_be) // 2]
    pct10 = sorted_be[len(sorted_be) // 10]
    pct90 = sorted_be[9 * len(sorted_be) // 10]
    print(f"  Entries that broke even: {len(breakeven_days_list)} / {len(breakeven_days_list) + never_breakeven} ({len(breakeven_days_list) / (len(breakeven_days_list) + never_breakeven) * 100:.1f}%)")
    print(f"  Never broke even (within data): {never_breakeven}")
    print(f"  Average breakeven: {avg_be:.1f} days")
    print(f"  Median breakeven: {med_be} days")
    print(f"  10th percentile:  {pct10} days (fast)")
    print(f"  90th percentile:  {pct90} days (slow)")
else:
    print("  No entries broke even within the data range!")

# --- 5e. Profitable streaks ---
print("\n### E. Profitable Streaks (consecutive profitable days)")
streak_lengths = []
current_streak = 0
for d in daily_pnl:
    if d["net_daily"] > 0:
        current_streak += 1
    else:
        if current_streak > 0:
            streak_lengths.append(current_streak)
        current_streak = 0
if current_streak > 0:
    streak_lengths.append(current_streak)

if streak_lengths:
    avg_streak = sum(streak_lengths) / len(streak_lengths)
    max_streak = max(streak_lengths)
    print(f"  Total streaks: {len(streak_lengths)}")
    print(f"  Average streak: {avg_streak:.1f} days")
    print(f"  Longest streak: {max_streak} days")
    print(f"  Days with positive net P&L: {sum(1 for d in daily_pnl if d['net_daily'] > 0)} / {len(daily_pnl)} ({sum(1 for d in daily_pnl if d['net_daily'] > 0) / len(daily_pnl) * 100:.1f}%)")

# --- 5f. Annualized strategy return ---
print("\n### F. Overall Strategy Performance")
total_asgard = sum(d["asgard_net"] for d in daily_pnl)
total_hl = sum(d["hl_pnl"] for d in daily_pnl)
total_gross = total_asgard + total_hl
total_net = total_gross - TOTAL_FEES
days_span = len(daily_pnl)
annual_return = (total_net / CAPITAL) * (365 / days_span) * 100

print(f"  Period: {daily_pnl[0]['date']} to {daily_pnl[-1]['date']} ({days_span} days)")
print(f"  Asgard leg total:   ${total_asgard:>+8.2f}")
print(f"  HL leg total:       ${total_hl:>+8.2f}")
print(f"  Gross P&L:          ${total_gross:>+8.2f}")
print(f"  Fees:               ${-TOTAL_FEES:>+8.2f}")
print(f"  Net P&L:            ${total_net:>+8.2f}")
print(f"  Return on capital:  {total_net / CAPITAL * 100:>+.2f}%")
print(f"  Annualized:         {annual_return:>+.2f}%")

# --- 5g. Asgard carry vs HL funding decomposition ---
print("\n### G. P&L Decomposition: What Drives Returns?")
asgard_pct = total_asgard / max(abs(total_asgard) + abs(total_hl), 0.01) * 100
hl_pct = total_hl / max(abs(total_asgard) + abs(total_hl), 0.01) * 100
print(f"  Asgard carry contribution: ${total_asgard:>+.2f} ({asgard_pct:>+.1f}% of gross)")
print(f"  HL funding contribution:   ${total_hl:>+.2f} ({hl_pct:>+.1f}% of gross)")

# What % of days did each leg contribute positively?
asgard_pos_days = sum(1 for d in daily_pnl if d["asgard_net"] > 0)
hl_pos_days = sum(1 for d in daily_pnl if d["hl_pnl"] > 0)
both_pos_days = sum(1 for d in daily_pnl if d["asgard_net"] > 0 and d["hl_pnl"] > 0)
print(f"  Asgard positive days: {asgard_pos_days}/{days_span} ({asgard_pos_days/days_span*100:.1f}%)")
print(f"  HL positive days:     {hl_pos_days}/{days_span} ({hl_pos_days/days_span*100:.1f}%)")
print(f"  Both positive days:   {both_pos_days}/{days_span} ({both_pos_days/days_span*100:.1f}%)")

# --- 5h. Fee sensitivity ---
print("\n### H. Fee Sensitivity Analysis")
print(f"{'Asgard Fee':<14} {'Total Fees':>12} {'Net P&L':>10} {'Annualized':>12} {'30d Win Rate':>14}")
print("-" * 65)

for fee_bps in [15, 10, 5, 0]:
    open_fee = ASGARD_NOTIONAL * (fee_bps / 10000)
    total_fee = open_fee + HL_OPEN_FEE + HL_CLOSE_FEE + GAS_COST
    net = total_gross - total_fee
    ann = (net / CAPITAL) * (365 / days_span) * 100

    # 30-day win rate
    wins = 0
    total_30 = 0
    for si in range(len(daily_pnl)):
        if si + 30 > len(daily_pnl):
            break
        cum = -total_fee
        for di in range(si, si + 30):
            cum += daily_pnl[di]["net_daily"]
        total_30 += 1
        if cum > 0:
            wins += 1
    wr30 = wins / total_30 * 100 if total_30 > 0 else 0

    print(f"  {fee_bps/100:.2f}%       ${total_fee:>10.2f}   ${net:>+8.2f}   {ann:>+10.2f}%   {wr30:>12.1f}%")

# --- 5i. Smart entry with real data ---
print("\n### I. Smart Entry Analysis")
print("Only enter when trailing 24h HL funding is positive (shorts get paid)")

# Compute trailing 24h funding
smart_entries = 0
smart_wins_30 = 0
smart_total_30 = 0

for si in range(1, len(daily_pnl)):
    # Check if previous day's funding was positive (favorable for shorts — they get paid)
    prev_funding = daily_pnl[si - 1]["funding_daily"]
    if prev_funding <= 0:
        continue  # Skip — negative funding means shorts pay (unfavorable)

    smart_entries += 1
    if si + 30 > len(daily_pnl):
        continue
    cum = -TOTAL_FEES
    for di in range(si, si + 30):
        cum += daily_pnl[di]["net_daily"]
    smart_total_30 += 1
    if cum > 0:
        smart_wins_30 += 1

smart_wr = smart_wins_30 / smart_total_30 * 100 if smart_total_30 > 0 else 0
print(f"  Total days with favorable entry signal: {smart_entries} / {len(daily_pnl)} ({smart_entries/len(daily_pnl)*100:.1f}%)")
print(f"  30-day win rate with smart entry: {smart_wr:.1f}%")
if results[30]["total"] > 0:
    naive_wr = results[30]["profitable"] / results[30]["total"] * 100
    print(f"  30-day win rate without filter:   {naive_wr:.1f}%")
    print(f"  Improvement: {smart_wr - naive_wr:+.1f} percentage points")

# --- 5j. Key insights ---
print("\n" + "=" * 70)
print("KEY INSIGHTS")
print("=" * 70)

insights = []

# Insight 1: Carry dominance
if abs(total_asgard) > abs(total_hl):
    insights.append(f"1. CARRY DOMINATES: Asgard lending carry (${total_asgard:+.2f}) is the primary profit driver, "
                    f"while HL funding (${total_hl:+.2f}) is {'a drag' if total_hl < 0 else 'a bonus'}.")
else:
    insights.append(f"1. FUNDING DOMINATES: HL funding (${total_hl:+.2f}) contributes more than Asgard carry (${total_asgard:+.2f}).")

# Insight 2: Strategy viability
if annual_return > 10:
    insights.append(f"2. VIABLE: Strategy annualizes at {annual_return:+.1f}% with real historical data.")
elif annual_return > 0:
    insights.append(f"2. MARGINAL: Strategy annualizes at only {annual_return:+.1f}% — may not justify gas/complexity.")
else:
    insights.append(f"2. UNPROFITABLE: Strategy loses money ({annual_return:+.1f}% annualized) with real data.")

# Insight 3: Fee impact
fee_pct = TOTAL_FEES / max(total_gross, 0.01) * 100
insights.append(f"3. FEE IMPACT: Entry fees (${TOTAL_FEES:.2f}) represent {fee_pct:.1f}% of gross profit over the period.")

# Insight 4: Consistency
pos_day_pct = sum(1 for d in daily_pnl if d["net_daily"] > 0) / len(daily_pnl) * 100
insights.append(f"4. CONSISTENCY: {pos_day_pct:.1f}% of days are net-profitable for the combined strategy.")

for i in insights:
    print(f"\n  {i}")

print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
