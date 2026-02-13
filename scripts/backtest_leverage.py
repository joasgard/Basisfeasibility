"""
Multi-leverage backtest — 2x, 3x, 4x
Uses same data sources as the full backtest.
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
print(f"  SOL: {len(sol_data)}, USDC: {len(usdc_data)}, HL: {len(hl_funding)}")

# ── Align by date ───────────────────────────────────────────────────

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
print(f"Overlapping: {len(all_dates)} days ({all_dates[0]} to {all_dates[-1]})\n")

# ── Run simulation at each leverage ────────────────────────────────

CAPITAL = 1000
LEVERAGES = [2.0, 3.0, 4.0]
GAS_COST = 2.0
HL_FEE_BPS = 3.5
ASGARD_FEE_BPS = 15

results = {}

for lev in LEVERAGES:
    collateral = CAPITAL / 2
    notional = collateral * lev
    hl_notional = notional

    asgard_open = notional * (ASGARD_FEE_BPS / 10000)
    hl_open = hl_notional * (HL_FEE_BPS / 10000)
    hl_close = hl_open
    total_fees = asgard_open + hl_open + hl_close + GAS_COST

    daily_pnl = []
    for d in all_dates:
        sol_lend_daily = (sol_by_date[d]["lend"] / 100) / 365
        usdc_borr_daily = (usdc_by_date[d]["borrow"] / 100) / 365
        funding_daily = hl_by_date[d]

        asgard_earn = notional * sol_lend_daily
        asgard_pay = (notional - collateral) * usdc_borr_daily
        hl_pnl = funding_daily * hl_notional

        daily_pnl.append({
            "date": d,
            "asgard_net": asgard_earn - asgard_pay,
            "hl_pnl": hl_pnl,
            "net": asgard_earn - asgard_pay + hl_pnl,
            "sol_lend": sol_by_date[d]["lend"],
            "usdc_borr": usdc_by_date[d]["borrow"],
        })

    # Overall
    total_asgard = sum(d["asgard_net"] for d in daily_pnl)
    total_hl = sum(d["hl_pnl"] for d in daily_pnl)
    gross = total_asgard + total_hl
    net = gross - total_fees
    ann = (net / CAPITAL) * (365 / len(daily_pnl)) * 100
    pos_days = sum(1 for d in daily_pnl if d["net"] > 0)

    # Net carry APY (annualized, on collateral)
    avg_carry_apy = total_asgard / len(daily_pnl) * 365 / collateral * 100

    # Hold duration win rates
    hold_results = {}
    for hold in [7, 14, 30, 60, 90]:
        wins = 0
        total = 0
        returns = []
        for si in range(len(daily_pnl)):
            if si + hold > len(daily_pnl):
                break
            cum = -total_fees
            for di in range(si, si + hold):
                cum += daily_pnl[di]["net"]
            total += 1
            if cum > 0:
                wins += 1
            returns.append(cum)
        hold_results[hold] = {
            "win_rate": wins / total * 100 if total else 0,
            "avg": sum(returns) / len(returns) if returns else 0,
            "median": sorted(returns)[len(returns)//2] if returns else 0,
            "best": max(returns) if returns else 0,
            "worst": min(returns) if returns else 0,
            "total": total,
        }

    # Breakeven
    be_days = []
    never_be = 0
    for si in range(len(daily_pnl)):
        cum = -total_fees
        found = False
        for off in range(len(daily_pnl) - si):
            cum += daily_pnl[si + off]["net"]
            if cum > 0:
                be_days.append(off + 1)
                found = True
                break
        if not found:
            never_be += 1

    # Streaks
    streaks = []
    cur = 0
    for d in daily_pnl:
        if d["net"] > 0:
            cur += 1
        else:
            if cur > 0: streaks.append(cur)
            cur = 0
    if cur > 0: streaks.append(cur)

    # Monthly
    monthly = defaultdict(list)
    for d in daily_pnl:
        monthly[d["date"][:7]].append(d)

    monthly_data = []
    cum_pnl = -total_fees
    for month in sorted(monthly):
        days = monthly[month]
        m_asgard = sum(d["asgard_net"] for d in days)
        m_hl = sum(d["hl_pnl"] for d in days)
        m_total = m_asgard + m_hl
        cum_pnl += m_total
        m_carry_apy = m_asgard / len(days) * 365 / collateral * 100
        m_hl_apy = sum(hl_by_date[d["date"]] for d in days) / len(days) * 365 * 100
        m_strat_apy = m_carry_apy + (m_hl / len(days) * 365 / collateral * 100)
        monthly_data.append({
            "month": month,
            "sol_lend": sum(d["sol_lend"] for d in days) / len(days),
            "usdc_borr": sum(d["usdc_borr"] for d in days) / len(days),
            "carry_apy": m_carry_apy,
            "hl_fund_apy": m_hl_apy,
            "strat_apy": m_strat_apy,
            "cum_pnl": cum_pnl,
        })

    # Smart entry
    smart_wins = 0
    smart_total = 0
    for si in range(1, len(daily_pnl)):
        if hl_by_date[daily_pnl[si-1]["date"]] <= 0:
            continue
        if si + 30 > len(daily_pnl):
            continue
        cum = -total_fees
        for di in range(si, si + 30):
            cum += daily_pnl[di]["net"]
        smart_total += 1
        if cum > 0:
            smart_wins += 1

    results[lev] = {
        "leverage": lev,
        "notional": notional,
        "total_fees": total_fees,
        "total_asgard": total_asgard,
        "total_hl": total_hl,
        "gross": gross,
        "net": net,
        "ann": ann,
        "pos_days": pos_days,
        "pos_day_pct": pos_days / len(daily_pnl) * 100,
        "avg_carry_apy": avg_carry_apy,
        "hold": hold_results,
        "be_median": sorted(be_days)[len(be_days)//2] if be_days else 999,
        "be_avg": sum(be_days) / len(be_days) if be_days else 999,
        "be_pct": len(be_days) / (len(be_days) + never_be) * 100,
        "be_p10": sorted(be_days)[len(be_days)//10] if be_days else 999,
        "be_p90": sorted(be_days)[9*len(be_days)//10] if be_days else 999,
        "streak_avg": sum(streaks) / len(streaks) if streaks else 0,
        "streak_max": max(streaks) if streaks else 0,
        "monthly": monthly_data,
        "smart_30d_wr": smart_wins / smart_total * 100 if smart_total else 0,
        "naive_30d_wr": hold_results[30]["win_rate"],
        "asgard_pct": total_asgard / max(abs(total_asgard) + abs(total_hl), 0.01) * 100,
        "hl_pct": total_hl / max(abs(total_asgard) + abs(total_hl), 0.01) * 100,
    }

# ── Output ──────────────────────────────────────────────────────────

print("=" * 75)
print("MULTI-LEVERAGE BACKTEST — 2x / 3x / 4x")
print(f"Period: {all_dates[0]} to {all_dates[-1]} ({len(all_dates)} days)")
print(f"Capital: ${CAPITAL}")
print("=" * 75)

# Summary comparison
print("\n### Overall Performance Comparison")
print(f"{'':20} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 62)
print(f"{'Notional/leg':20} ${results[2]['notional']:>12,.0f} ${results[3]['notional']:>12,.0f} ${results[4]['notional']:>12,.0f}")
print(f"{'Round-trip fees':20} ${results[2]['total_fees']:>12.2f} ${results[3]['total_fees']:>12.2f} ${results[4]['total_fees']:>12.2f}")
print(f"{'Asgard leg P&L':20} ${results[2]['total_asgard']:>+12.2f} ${results[3]['total_asgard']:>+12.2f} ${results[4]['total_asgard']:>+12.2f}")
print(f"{'HL leg P&L':20} ${results[2]['total_hl']:>+12.2f} ${results[3]['total_hl']:>+12.2f} ${results[4]['total_hl']:>+12.2f}")
print(f"{'Gross P&L':20} ${results[2]['gross']:>+12.2f} ${results[3]['gross']:>+12.2f} ${results[4]['gross']:>+12.2f}")
print(f"{'Net P&L':20} ${results[2]['net']:>+12.2f} ${results[3]['net']:>+12.2f} ${results[4]['net']:>+12.2f}")
print(f"{'Return on capital':20} {results[2]['net']/CAPITAL*100:>+12.2f}% {results[3]['net']/CAPITAL*100:>+12.2f}% {results[4]['net']/CAPITAL*100:>+12.2f}%")
print(f"{'Annualized':20} {results[2]['ann']:>+12.2f}% {results[3]['ann']:>+12.2f}% {results[4]['ann']:>+12.2f}%")
print(f"{'Positive days':20} {results[2]['pos_day_pct']:>11.1f}% {results[3]['pos_day_pct']:>11.1f}% {results[4]['pos_day_pct']:>11.1f}%")

# P&L decomposition
print(f"\n### P&L Decomposition")
print(f"{'':20} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 62)
print(f"{'Asgard carry share':20} {results[2]['asgard_pct']:>12.1f}% {results[3]['asgard_pct']:>12.1f}% {results[4]['asgard_pct']:>12.1f}%")
print(f"{'HL funding share':20} {results[2]['hl_pct']:>12.1f}% {results[3]['hl_pct']:>12.1f}% {results[4]['hl_pct']:>12.1f}%")
print(f"{'Avg carry APY':20} {results[2]['avg_carry_apy']:>+12.2f}% {results[3]['avg_carry_apy']:>+12.2f}% {results[4]['avg_carry_apy']:>+12.2f}%")

# Breakeven
print(f"\n### Breakeven")
print(f"{'':20} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 62)
print(f"{'% that break even':20} {results[2]['be_pct']:>12.1f}% {results[3]['be_pct']:>12.1f}% {results[4]['be_pct']:>12.1f}%")
print(f"{'Median breakeven':20} {results[2]['be_median']:>10} d {results[3]['be_median']:>10} d {results[4]['be_median']:>10} d")
print(f"{'Average breakeven':20} {results[2]['be_avg']:>10.1f} d {results[3]['be_avg']:>10.1f} d {results[4]['be_avg']:>10.1f} d")
print(f"{'10th pctile (fast)':20} {results[2]['be_p10']:>10} d {results[3]['be_p10']:>10} d {results[4]['be_p10']:>10} d")
print(f"{'90th pctile (slow)':20} {results[2]['be_p90']:>10} d {results[3]['be_p90']:>10} d {results[4]['be_p90']:>10} d")

# Hold duration
print(f"\n### Win Rate by Hold Duration")
print(f"{'Hold':8} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 50)
for h in [7, 14, 30, 60, 90]:
    print(f"{h:>5} d   {results[2]['hold'][h]['win_rate']:>12.1f}% {results[3]['hold'][h]['win_rate']:>12.1f}% {results[4]['hold'][h]['win_rate']:>12.1f}%")

print(f"\n### Average Return by Hold Duration")
print(f"{'Hold':8} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 50)
for h in [7, 14, 30, 60, 90]:
    print(f"{h:>5} d   ${results[2]['hold'][h]['avg']:>+11.2f} ${results[3]['hold'][h]['avg']:>+11.2f} ${results[4]['hold'][h]['avg']:>+11.2f}")

print(f"\n### Worst Return by Hold Duration")
print(f"{'Hold':8} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 50)
for h in [7, 14, 30, 60, 90]:
    print(f"{h:>5} d   ${results[2]['hold'][h]['worst']:>+11.2f} ${results[3]['hold'][h]['worst']:>+11.2f} ${results[4]['hold'][h]['worst']:>+11.2f}")

# Streaks
print(f"\n### Profitable Streaks")
print(f"{'':20} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 62)
print(f"{'Avg streak':20} {results[2]['streak_avg']:>11.1f} d {results[3]['streak_avg']:>11.1f} d {results[4]['streak_avg']:>11.1f} d")
print(f"{'Max streak':20} {results[2]['streak_max']:>10} d {results[3]['streak_max']:>10} d {results[4]['streak_max']:>10} d")

# Smart entry
print(f"\n### Smart Entry (30-day win rate)")
print(f"{'':20} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 62)
print(f"{'Blind entry':20} {results[2]['naive_30d_wr']:>12.1f}% {results[3]['naive_30d_wr']:>12.1f}% {results[4]['naive_30d_wr']:>12.1f}%")
print(f"{'Smart entry':20} {results[2]['smart_30d_wr']:>12.1f}% {results[3]['smart_30d_wr']:>12.1f}% {results[4]['smart_30d_wr']:>12.1f}%")

# Monthly comparison
print(f"\n### Monthly Strategy APY (annualized)")
print(f"{'Month':10} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 52)
for i in range(len(results[2.0]["monthly"])):
    m2 = results[2.0]["monthly"][i]
    m3 = results[3.0]["monthly"][i]
    m4 = results[4.0]["monthly"][i]
    print(f"{m2['month']:10} {m2['strat_apy']:>+12.1f}% {m3['strat_apy']:>+12.1f}% {m4['strat_apy']:>+12.1f}%")

# Monthly cumulative P&L
print(f"\n### Cumulative P&L by Month")
print(f"{'Month':10} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 52)
for i in range(len(results[2.0]["monthly"])):
    m2 = results[2.0]["monthly"][i]
    m3 = results[3.0]["monthly"][i]
    m4 = results[4.0]["monthly"][i]
    print(f"{m2['month']:10} ${m2['cum_pnl']:>+11.2f} ${m3['cum_pnl']:>+11.2f} ${m4['cum_pnl']:>+11.2f}")

# Fee sensitivity at each leverage
print(f"\n### Fee Sensitivity (30-day win rate)")
print(f"{'Asgard Fee':12} {'2x':>14} {'3x':>14} {'4x':>14}")
print("-" * 54)
for fee_bps in [15, 10, 5, 0]:
    row = []
    for lev in LEVERAGES:
        coll = CAPITAL / 2
        not_ = coll * lev
        af = not_ * (fee_bps / 10000)
        hf = not_ * (HL_FEE_BPS / 10000)
        tf = af + hf + hf + GAS_COST
        wins = 0; tot = 0
        for si in range(len(all_dates)):
            if si + 30 > len(all_dates): break
            cum = -tf
            for di in range(si, si + 30):
                d = all_dates[di]
                sl = (sol_by_date[d]["lend"]/100)/365
                ub = (usdc_by_date[d]["borrow"]/100)/365
                fd = hl_by_date[d]
                cum += not_*sl - (not_-coll)*ub + fd*not_
            tot += 1
            if cum > 0: wins += 1
        row.append(wins/tot*100 if tot else 0)
    print(f"  {fee_bps/100:.2f}%      {row[0]:>12.1f}% {row[1]:>12.1f}% {row[2]:>12.1f}%")

print("\n" + "=" * 75)
print("DONE")
print("=" * 75)
