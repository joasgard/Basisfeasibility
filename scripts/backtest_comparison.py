"""
Drift vs Hyperliquid: Side-by-Side Delta-Neutral Backtest Comparison

Runs the same capital-rebalance simulation on both venues with identical
Asgard long-leg parameters, SOL candles, and Kamino lending/borrowing data.

Key venue differences:
  - Bridge cost: HL=$3 (Arbitrum↔Solana), Drift=$0.001 (same-chain)
  - Maintenance margin: HL=5%, Drift=3%
  - Liquidation penalty: HL=50% of equity, Drift=2.5% of notional
  - Taker fee: both 3.5 bps
  - Funding rates: venue-specific (fetched separately)
"""

import json
import os
from datetime import datetime, timezone
from collections import defaultdict

# ── Load data ───────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

def load_json(filename):
    with open(os.path.join(DATA_DIR, filename)) as f:
        return json.load(f)

def parse_date(ts): return ts[:10]

print("Loading data...")
sol_data = load_json("kamino_sol_lending_rates.json")
usdc_data = load_json("kamino_usdc_borrowing_rates.json")
hl_funding_raw = load_json("sol_funding_history.json")
drift_funding_raw = load_json("drift_sol_funding_history.json")
candles = load_json("sol_daily_candles.json")

# Index rates by date
sol_by_date = {}
for rec in sol_data:
    d = parse_date(rec["timestamp"])
    sol_by_date[d] = {"lend": rec.get("apyBase") or 0.0}

usdc_by_date = {}
for rec in usdc_data:
    d = parse_date(rec["timestamp"])
    usdc_by_date[d] = {"borrow": rec.get("apyBaseBorrow") or 0.0}

# HL funding: aggregate hourly → daily
hl_daily = defaultdict(list)
for rec in hl_funding_raw:
    ts_ms = rec.get("time", rec.get("timestamp", 0))
    d = ts_ms[:10] if isinstance(ts_ms, str) else datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    hl_daily[d].append(float(rec.get("fundingRate", 0)))
hl_by_date = {d: sum(rates) for d, rates in hl_daily.items()}

# Drift funding: already daily-aggregated and HL-normalized
drift_by_date = {rec["date"]: rec["rate"] for rec in drift_funding_raw}

# Price candles
price_by_date = {}
for c in candles:
    dt = datetime.fromtimestamp(c['t']/1000, tz=timezone.utc)
    d = dt.strftime("%Y-%m-%d")
    price_by_date[d] = {"close": float(c['c']), "high": float(c['h']), "low": float(c['l'])}

# Use dates common to ALL data sources
all_dates = sorted(
    set(sol_by_date) & set(usdc_by_date) & set(hl_by_date)
    & set(drift_by_date) & set(price_by_date)
)
print(f"Aligned: {len(all_dates)} days ({all_dates[0]} to {all_dates[-1]})")
print(f"SOL: ${price_by_date[all_dates[0]]['close']:.2f} → ${price_by_date[all_dates[-1]]['close']:.2f} "
      f"({(price_by_date[all_dates[-1]]['close']/price_by_date[all_dates[0]]['close']-1)*100:+.1f}%)")

# ── Venue Configs ─────────────────────────────────────────────────

ASGARD_FEE_BPS = 15       # 0.15% on notional (open only)
GAS_COST = 2.0            # per on-chain operation

VENUE_HL = {
    "name": "Hyperliquid",
    "fee_bps": 3.5,
    "maintenance_margin": 0.05,
    "liq_penalty_model": "equity",    # 50% of remaining equity
    "liq_penalty_pct": 0.50,
    "bridge_cost": 3.0,               # Arbitrum ↔ Solana
    "funding_by_date": hl_by_date,
}

VENUE_DRIFT = {
    "name": "Drift",
    "fee_bps": 3.5,
    "maintenance_margin": 0.03,
    "liq_penalty_model": "notional",  # 2.5% of position notional
    "liq_penalty_pct": 0.025,
    "bridge_cost": 0.001,             # Same-chain (just gas)
    "funding_by_date": drift_by_date,
}

# ── Simulation ─────────────────────────────────────────────────────

def run_simulation(
    initial_capital: float,
    target_leverage: float,
    venue: dict,
    rebalance_lev_trigger: float = 0.0,
    asgard_fee_bps: float = ASGARD_FEE_BPS,
):
    """
    Simulate delta-neutral strategy with cross-leg capital rebalancing.
    Parameterized by venue config for short leg.
    """
    half = initial_capital / 2
    p0 = price_by_date[all_dates[0]]["close"]

    fee_bps = venue["fee_bps"]
    maint_margin = venue["maintenance_margin"]
    bridge_cost = venue["bridge_cost"]
    funding_data = venue["funding_by_date"]

    # ── Open positions ──
    notional = half * target_leverage

    sol_qty = notional / p0
    usdc_debt = half * (target_leverage - 1)

    short_contracts = notional / p0
    short_entry = p0
    short_margin = half

    open_fees = (notional * asgard_fee_bps / 10000 +
                 notional * fee_bps / 10000 +
                 GAS_COST)
    usdc_debt += open_fees / 2
    short_margin -= open_fees / 2

    if rebalance_lev_trigger <= 0:
        rebalance_lev_trigger = target_leverage * 2

    # ── Tracking ──
    total_fees = open_fees
    total_carry = 0.0
    total_funding = 0.0
    total_liq_penalty = 0.0
    total_bridge_costs = 0.0
    capital_rebalances = 0
    full_rotations = 0
    liquidations = 0
    events = []
    daily_equity_log = []

    long_eq = sol_qty * p0 - usdc_debt
    short_eq = short_margin

    events.append({
        "date": all_dates[0], "type": "OPEN",
        "price": p0, "long_eq": long_eq, "short_eq": short_eq,
        "notional": notional, "fees": open_fees,
    })

    # ── Daily loop ──
    for i, date in enumerate(all_dates):
        p = price_by_date[date]
        sol_close = p["close"]
        sol_low = p["low"]
        sol_high = p["high"]

        # 1. Daily yield
        sol_lend = (sol_by_date.get(date, {}).get("lend", 0) / 100) / 365
        usdc_borr = (usdc_by_date.get(date, {}).get("borrow", 0) / 100) / 365
        funding = funding_data.get(date, 0)

        sol_earned = sol_qty * sol_lend
        debt_increase = usdc_debt * usdc_borr
        carry = sol_earned * sol_close - debt_increase

        sol_qty += sol_earned
        usdc_debt += debt_increase

        fund_income = funding * short_contracts * sol_close
        short_margin += fund_income

        total_carry += carry
        total_funding += fund_income

        # 2. Derive equity
        long_eq = sol_qty * sol_close - usdc_debt
        short_unrealized = short_contracts * (short_entry - sol_close)
        short_eq = short_margin + short_unrealized

        # 3. Check intraday liquidation
        long_eq_at_low = sol_qty * sol_low - usdc_debt
        long_notional_at_low = sol_qty * sol_low

        short_eq_at_high = short_margin + short_contracts * (short_entry - sol_high)
        short_notional_at_high = short_contracts * sol_high

        got_liquidated = False
        liq_leg = ""

        if long_eq_at_low <= long_notional_at_low * maint_margin:
            got_liquidated = True
            liq_leg = "LONG"
        if short_eq_at_high <= short_notional_at_high * maint_margin:
            liq_leg = "BOTH" if got_liquidated else "SHORT"
            got_liquidated = True

        if got_liquidated:
            liquidations += 1

            # Liquidation penalty: venue-specific model
            liq_penalty = 0
            if venue["liq_penalty_model"] == "equity":
                # HL model: lose X% of remaining equity
                if liq_leg in ("LONG", "BOTH"):
                    liq_penalty += max(long_eq * venue["liq_penalty_pct"], 0)
                    long_eq = max(long_eq * (1 - venue["liq_penalty_pct"]), 0)
                if liq_leg in ("SHORT", "BOTH"):
                    liq_penalty += max(short_eq * venue["liq_penalty_pct"], 0)
                    short_eq = max(short_eq * (1 - venue["liq_penalty_pct"]), 0)
            else:
                # Drift model: lose X% of position notional
                if liq_leg in ("LONG", "BOTH"):
                    notional_at_liq = sol_qty * sol_close
                    penalty = notional_at_liq * venue["liq_penalty_pct"]
                    penalty = min(penalty, max(long_eq, 0))  # Can't lose more than equity
                    liq_penalty += penalty
                    long_eq = max(long_eq - penalty, 0)
                if liq_leg in ("SHORT", "BOTH"):
                    notional_at_liq = short_contracts * sol_close
                    penalty = notional_at_liq * venue["liq_penalty_pct"]
                    penalty = min(penalty, max(short_eq, 0))
                    liq_penalty += penalty
                    short_eq = max(short_eq - penalty, 0)

            total_liq_penalty += liq_penalty

            events.append({
                "date": date, "type": "LIQUIDATION", "leg": liq_leg,
                "price": sol_close, "penalty": liq_penalty,
                "long_eq": long_eq, "short_eq": short_eq,
            })

            # Full close + reopen
            total_eq = long_eq + short_eq
            old_short_notional = short_contracts * sol_close
            new_half = total_eq / 2
            new_notional = new_half * target_leverage

            reopen_fees = (new_notional * asgard_fee_bps / 10000 +
                          new_notional * fee_bps / 10000 +
                          old_short_notional * fee_bps / 10000 +
                          GAS_COST)
            total_fees += reopen_fees
            total_eq -= reopen_fees

            new_half = total_eq / 2
            sol_qty = new_half * target_leverage / sol_close
            usdc_debt = new_half * (target_leverage - 1)
            short_contracts = new_half * target_leverage / sol_close
            short_entry = sol_close
            short_margin = new_half

            full_rotations += 1

            long_eq = sol_qty * sol_close - usdc_debt
            short_eq = short_margin

            events.append({
                "date": date, "type": "REOPEN_AFTER_LIQ",
                "price": sol_close,
                "long_eq": long_eq, "short_eq": short_eq,
                "notional": new_half * target_leverage,
                "fees": reopen_fees,
            })
        else:
            # 4. Check capital rebalance trigger
            long_notional_now = sol_qty * sol_close
            eff_lev_long = long_notional_now / max(long_eq, 1) if long_eq > 0 else 999
            short_notional_now = short_contracts * sol_close
            eff_lev_short = short_notional_now / max(short_eq, 1) if short_eq > 0 else 999

            if eff_lev_long > rebalance_lev_trigger or eff_lev_short > rebalance_lev_trigger:
                total_eq = long_eq + short_eq
                target_eq = total_eq / 2
                transfer = abs(long_eq - target_eq)

                if transfer > 5:
                    if long_eq < target_eq:
                        add = target_eq - long_eq
                        usdc_debt -= add
                        short_margin -= add
                    else:
                        add = target_eq - short_eq
                        usdc_debt += add
                        short_margin += add

                    usdc_debt += bridge_cost / 2
                    short_margin -= bridge_cost / 2
                    total_fees += bridge_cost
                    total_bridge_costs += bridge_cost
                    capital_rebalances += 1

                    long_eq = sol_qty * sol_close - usdc_debt
                    short_eq = short_margin + short_contracts * (short_entry - sol_close)

                    events.append({
                        "date": date, "type": "CAPITAL_REBALANCE",
                        "price": sol_close, "transfer": transfer,
                        "long_eq": long_eq, "short_eq": short_eq,
                        "eff_lev_before": max(eff_lev_long, eff_lev_short),
                        "fee": bridge_cost,
                    })

        total_eq = long_eq + short_eq
        daily_equity_log.append({"date": date, "equity": total_eq})

    # ── Final close fee ──
    final_price = price_by_date[all_dates[-1]]["close"]
    close_fee = short_contracts * final_price * fee_bps / 10000
    total_fees += close_fee

    final_capital = long_eq + short_eq - close_fee
    total_return = final_capital - initial_capital
    ann_return = (total_return / initial_capital) * (365 / len(all_dates)) * 100

    # Max drawdown
    peak = initial_capital
    max_dd = 0
    for de in daily_equity_log:
        peak = max(peak, de["equity"])
        dd = (peak - de["equity"]) / peak
        max_dd = max(max_dd, dd)

    gross = total_carry + total_funding
    mtm_drag = total_return - (gross - total_fees - total_liq_penalty)

    return {
        "venue": venue["name"],
        "initial": initial_capital,
        "leverage": target_leverage,
        "final": final_capital,
        "return_pct": total_return / initial_capital * 100,
        "ann_return": ann_return,
        "total_fees": total_fees,
        "bridge_costs": total_bridge_costs,
        "liq_penalty": total_liq_penalty,
        "carry": total_carry,
        "funding": total_funding,
        "gross": gross,
        "fees_pct_gross": total_fees / max(abs(gross), 0.01) * 100,
        "mtm_drag": mtm_drag,
        "capital_rebalances": capital_rebalances,
        "full_rotations": full_rotations,
        "liquidations": liquidations,
        "max_dd_pct": max_dd * 100,
        "events": events,
        "daily_equity": daily_equity_log,
    }


# ── Run comparison ─────────────────────────────────────────────────

N = len(all_dates)
p_start = price_by_date[all_dates[0]]['close']
p_end = price_by_date[all_dates[-1]]['close']

print(f"\n{'='*100}")
print(f"DRIFT vs HYPERLIQUID: DELTA-NEUTRAL BACKTEST COMPARISON")
print(f"Period: {all_dates[0]} to {all_dates[-1]} ({N} days)")
print(f"SOL: ${p_start:.2f} → ${p_end:.2f} ({(p_end/p_start-1)*100:+.1f}%)")
print(f"{'='*100}")

# ── 0. Venue Parameter Comparison ──
print(f"\n### 0. Venue Parameter Comparison")
print(f"\n{'Parameter':<30} {'Hyperliquid':>15} {'Drift':>15} {'Impact':>20}")
print("-" * 82)
print(f"{'Taker fee (bps)':<30} {'3.5':>15} {'3.5':>15} {'Identical':>20}")
print(f"{'Maintenance margin':<30} {'5.0%':>15} {'3.0%':>15} {'Drift survives more':>20}")
print(f"{'Liquidation penalty':<30} {'50% of equity':>15} {'2.5% of notional':>15} {'Drift much lower':>20}")
print(f"{'Bridge/transfer cost':<30} {'$3.00':>15} {'$0.001':>15} {'Drift 3000x cheaper':>20}")
print(f"{'Avg daily funding':<30} {sum(hl_by_date.get(d,0) for d in all_dates)/N*365*100:>+14.2f}% {sum(drift_by_date.get(d,0) for d in all_dates)/N*365*100:>+14.2f}% {'HL pays shorts more':>20}")
print(f"{'Positive funding days':<30} {sum(1 for d in all_dates if hl_by_date.get(d,0)>0):>14}/{N} {sum(1 for d in all_dates if drift_by_date.get(d,0)>0):>14}/{N} {'HL more favorable':>20}")

# ── 1. Side-by-Side Performance Matrix ──
CAPITALS = [1_000, 10_000, 100_000]
LEVERAGES = [2.0, 3.0, 4.0]

print(f"\n### 1. Side-by-Side Performance Matrix")
print(f"\n{'':>10} {'':>4}  │ {'--- Hyperliquid ---':^32} │ {'--- Drift ---':^32}")
print(f"{'Cap':>10} {'Lev':>4}  │ {'Ann Ret':>8} {'Fees':>8} {'Liqs':>5} {'MaxDD':>7} │ {'Ann Ret':>8} {'Fees':>8} {'Liqs':>5} {'MaxDD':>7}  │ {'Δ Ret':>7}")
print("-" * 105)

results_cache = {}
for cap in CAPITALS:
    for lev in LEVERAGES:
        rh = run_simulation(cap, lev, VENUE_HL)
        rd = run_simulation(cap, lev, VENUE_DRIFT)
        results_cache[(cap, lev, "HL")] = rh
        results_cache[(cap, lev, "Drift")] = rd

        delta = rd['ann_return'] - rh['ann_return']
        winner = "D" if delta > 0 else "H"
        print(f"${cap:>8,} {lev:>3.0f}x  │ {rh['ann_return']:>+7.1f}% ${rh['total_fees']:>6,.0f} "
              f"{rh['liquidations']:>4} {rh['max_dd_pct']:>5.1f}% │ "
              f"{rd['ann_return']:>+7.1f}% ${rd['total_fees']:>6,.0f} "
              f"{rd['liquidations']:>4} {rd['max_dd_pct']:>5.1f}%  │ {delta:>+6.1f}% {winner}")

# ── 2. Trigger Optimization — $10k ──
print(f"\n### 2. Optimal Rebalance Trigger — $10k")
for lev in LEVERAGES:
    print(f"\n  {lev:.0f}x leverage:")
    print(f"  {'Trigger':>10} │ {'HL Ret':>8} {'HL Liqs':>8} │ {'DR Ret':>8} {'DR Liqs':>8} │ {'Δ':>7}")
    print(f"  {'-'*65}")
    best_hl = -999
    best_dr = -999
    best_trig_hl = 0
    best_trig_dr = 0
    for trigger in [3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 999.0]:
        if trigger < lev * 1.3:
            continue
        label = f"{trigger:.0f}x" if trigger < 100 else "never"
        rh = run_simulation(10_000, lev, VENUE_HL, rebalance_lev_trigger=trigger)
        rd = run_simulation(10_000, lev, VENUE_DRIFT, rebalance_lev_trigger=trigger)
        delta = rd['ann_return'] - rh['ann_return']
        if rh['ann_return'] > best_hl:
            best_hl = rh['ann_return']
            best_trig_hl = trigger
        if rd['ann_return'] > best_dr:
            best_dr = rd['ann_return']
            best_trig_dr = trigger
        print(f"    {label:>8} │ {rh['ann_return']:>+7.1f}% {rh['liquidations']:>6} │ "
              f"{rd['ann_return']:>+7.1f}% {rd['liquidations']:>6} │ {delta:>+6.1f}%")
    hl_label = f"{best_trig_hl:.0f}x" if best_trig_hl < 100 else "never"
    dr_label = f"{best_trig_dr:.0f}x" if best_trig_dr < 100 else "never"
    print(f"  → HL best: {hl_label} ({best_hl:+.1f}%)  Drift best: {dr_label} ({best_dr:+.1f}%)")

# ── 3. Fee/Cost Comparison ──
print(f"\n### 3. Fee & Cost Comparison — $10k")
print(f"\n{'Lev':>4} │ {'--- Hyperliquid ---':^35} │ {'--- Drift ---':^35}")
print(f"{'':>4} │ {'Trd Fees':>9} {'Bridge':>8} {'Liq Pen':>9} {'Total':>9} │ "
      f"{'Trd Fees':>9} {'Bridge':>8} {'Liq Pen':>9} {'Total':>9}")
print("-" * 85)
for lev in LEVERAGES:
    rh = results_cache[(10_000, lev, "HL")]
    rd = results_cache[(10_000, lev, "Drift")]
    hl_trade = rh['total_fees'] - rh['bridge_costs']
    dr_trade = rd['total_fees'] - rd['bridge_costs']
    print(f" {lev:.0f}x  │ ${hl_trade:>7,.0f} ${rh['bridge_costs']:>6,.0f} ${rh['liq_penalty']:>7,.0f} "
          f"${rh['total_fees']+rh['liq_penalty']:>7,.0f} │ "
          f"${dr_trade:>7,.0f} ${rd['bridge_costs']:>6,.2f} ${rd['liq_penalty']:>7,.0f} "
          f"${rd['total_fees']+rd['liq_penalty']:>7,.0f}")

# ── 4. P&L Waterfall Comparison ──
print(f"\n### 4. P&L Waterfall Comparison — $10k")
print(f"\n{'':>4}  │ {'--- Hyperliquid ---':^50} │ {'--- Drift ---':^50}")
print(f"{'Lev':>4}  │ {'Carry':>8} {'Funding':>9} {'Gross':>8} {'Fees':>7} {'LiqPen':>8} {'MTM':>8} {'Return':>10} │ "
      f"{'Carry':>8} {'Funding':>9} {'Gross':>8} {'Fees':>7} {'LiqPen':>8} {'MTM':>8} {'Return':>10}")
print("-" * 125)
for lev in LEVERAGES:
    rh = results_cache[(10_000, lev, "HL")]
    rd = results_cache[(10_000, lev, "Drift")]
    print(f" {lev:.0f}x  │ "
          f"${rh['carry']:>6,.0f} ${rh['funding']:>7,.0f} ${rh['gross']:>6,.0f} "
          f"${rh['total_fees']:>5,.0f} ${rh['liq_penalty']:>6,.0f} ${rh['mtm_drag']:>6,.0f} "
          f"${rh['final']-rh['initial']:>+8,.0f} │ "
          f"${rd['carry']:>6,.0f} ${rd['funding']:>7,.0f} ${rd['gross']:>6,.0f} "
          f"${rd['total_fees']:>5,.0f} ${rd['liq_penalty']:>6,.0f} ${rd['mtm_drag']:>6,.0f} "
          f"${rd['final']-rd['initial']:>+8,.0f}")

# ── 5. Funding Rate Statistical Comparison ──
print(f"\n### 5. Funding Rate Comparison")
hl_rates = [hl_by_date.get(d, 0) for d in all_dates]
dr_rates = [drift_by_date.get(d, 0) for d in all_dates]

hl_mean = sum(hl_rates) / N
dr_mean = sum(dr_rates) / N
hl_std = (sum((r - hl_mean)**2 for r in hl_rates) / N) ** 0.5
dr_std = (sum((r - dr_mean)**2 for r in dr_rates) / N) ** 0.5

# Monthly breakdown
from collections import defaultdict as dd
hl_monthly = dd(list)
dr_monthly = dd(list)
for d in all_dates:
    month = d[:7]
    hl_monthly[month].append(hl_by_date.get(d, 0))
    dr_monthly[month].append(drift_by_date.get(d, 0))

print(f"\n{'Metric':<30} {'Hyperliquid':>15} {'Drift':>15}")
print("-" * 62)
print(f"{'Mean daily rate':<30} {hl_mean:>+15.8f} {dr_mean:>+15.8f}")
print(f"{'Std dev daily rate':<30} {hl_std:>15.8f} {dr_std:>15.8f}")
print(f"{'Annualized mean':<30} {hl_mean*365*100:>+14.2f}% {dr_mean*365*100:>+14.2f}%")
print(f"{'Positive days (shorts earn)':<30} {sum(1 for r in hl_rates if r > 0):>14} {sum(1 for r in dr_rates if r > 0):>14}")
print(f"{'Negative days (shorts pay)':<30} {sum(1 for r in hl_rates if r < 0):>14} {sum(1 for r in dr_rates if r < 0):>14}")
print(f"{'Max daily rate':<30} {max(hl_rates):>+15.8f} {max(dr_rates):>+15.8f}")
print(f"{'Min daily rate':<30} {min(hl_rates):>+15.8f} {min(dr_rates):>+15.8f}")

# Correlation
cov = sum((hl_rates[i] - hl_mean) * (dr_rates[i] - dr_mean) for i in range(N)) / N
corr = cov / (hl_std * dr_std) if hl_std > 0 and dr_std > 0 else 0
print(f"{'Cross-venue correlation':<30} {corr:>15.3f}")

print(f"\n  Monthly funding (annualized %):")
print(f"  {'Month':<10} {'HL':>10} {'Drift':>10} {'Δ':>10}")
print(f"  {'-'*42}")
for month in sorted(hl_monthly.keys()):
    hl_m = sum(hl_monthly[month]) / len(hl_monthly[month]) * 365 * 100
    dr_m = sum(dr_monthly[month]) / len(dr_monthly[month]) * 365 * 100
    print(f"  {month:<10} {hl_m:>+9.1f}% {dr_m:>+9.1f}% {dr_m-hl_m:>+9.1f}%")

# ── 6. Delta-Neutral Verification ──
print(f"\n### 6. Delta-Neutral Verification — $10k, 3x")
for venue in [VENUE_HL, VENUE_DRIFT]:
    r = run_simulation(10_000, 3.0, venue)
    eq = r['daily_equity']
    eq_values = [e['equity'] for e in eq]
    sol_move = (p_end / p_start - 1) * 100
    eq_move = (eq_values[-1] / eq_values[0] - 1) * 100
    std = (sum((v - sum(eq_values)/len(eq_values))**2 for v in eq_values) / len(eq_values)) ** 0.5
    ok = "delta-neutral" if abs(eq_move) < 15 else "NOT delta-neutral"
    print(f"\n  {venue['name']}:")
    print(f"    Start: ${eq_values[0]:,.0f}  End: ${eq_values[-1]:,.0f}")
    print(f"    Min: ${min(eq_values):,.0f}  Max: ${max(eq_values):,.0f}  Std: ${std:,.0f}")
    print(f"    SOL moved: {sol_move:+.1f}%  Equity moved: {eq_move:+.1f}%  → {ok}")

# ── 7. Event Log Comparison — $10k, 3x ──
print(f"\n### 7. Event Log — $10k, 3x")
for venue in [VENUE_HL, VENUE_DRIFT]:
    r = run_simulation(10_000, 3.0, venue)
    print(f"\n  {venue['name']}:")
    for ev in r['events']:
        if ev['type'] == 'OPEN':
            print(f"    {ev['date']}  OPEN   @ ${ev['price']:.2f}  "
                  f"long=${ev['long_eq']:.0f} short=${ev['short_eq']:.0f}  "
                  f"notional=${ev['notional']:.0f}")
        elif ev['type'] == 'CAPITAL_REBALANCE':
            print(f"    {ev['date']}  REBAL  @ ${ev['price']:.2f}  "
                  f"xfer=${ev['transfer']:.0f}  lev={ev['eff_lev_before']:.1f}x  "
                  f"→ long=${ev['long_eq']:.0f} short=${ev['short_eq']:.0f}")
        elif ev['type'] == 'LIQUIDATION':
            print(f"    {ev['date']}  **LIQ** @ ${ev['price']:.2f}  leg={ev['leg']}  "
                  f"penalty=${ev['penalty']:.0f}  "
                  f"long=${ev['long_eq']:.0f} short=${ev['short_eq']:.0f}")
        elif ev['type'] == 'REOPEN_AFTER_LIQ':
            print(f"    {ev['date']}  REOPEN @ ${ev['price']:.2f}  "
                  f"long=${ev['long_eq']:.0f} short=${ev['short_eq']:.0f}  "
                  f"notional=${ev['notional']:.0f}")

# ── 8. Winner Summary ──
print(f"\n### 8. Winner Summary")
print(f"\n{'Cap':>10} {'Lev':>4} │ {'HL':>8} {'Drift':>8} │ Winner  Why")
print("-" * 70)
for cap in CAPITALS:
    for lev in LEVERAGES:
        rh = results_cache[(cap, lev, "HL")]
        rd = results_cache[(cap, lev, "Drift")]
        if rh['ann_return'] > rd['ann_return']:
            winner = "HL"
            why = "better funding"
            if rh['liquidations'] < rd['liquidations']:
                why = "fewer liqs + better funding"
            elif rd['liquidations'] < rh['liquidations']:
                why = "better funding overcomes fewer Drift liqs"
        else:
            winner = "Drift"
            why = "lower liq penalty"
            if rd['liquidations'] < rh['liquidations']:
                why = "fewer liqs + lower penalty"
            elif rh['liquidations'] == rd['liquidations'] == 0:
                why = "lower bridge cost"
                if rd['funding'] > rh['funding']:
                    why = "better funding"
        print(f"${cap:>8,} {lev:>3.0f}x │ {rh['ann_return']:>+7.1f}% {rd['ann_return']:>+7.1f}% │ {winner:<7} {why}")

print(f"\n{'='*100}")
print("DONE")
print(f"{'='*100}")
