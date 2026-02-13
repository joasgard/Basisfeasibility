"""
Breakeven Analysis: Delta-Neutral Basis Strategy

Core formula (annualized, as % of capital):

  Gross APY = L/2 × (R_lend + R_fund − R_borr)  +  R_borr/2
                     ─────────────────────────       ────────
                     "spread" — amplified by L/2     constant

  Net APY = Gross APY − Fee Drag − Liq Drag

This script computes:
  1. Theoretical breakeven spread for each venue × leverage × capital
  2. Breakeven funding rate (given observed lending/borrowing rates)
  3. Actual spread and margin of safety from the backtest period
  4. Max SOL move before liquidation per venue × leverage
  5. Sensitivity tables
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

sol_data = load_json("kamino_sol_lending_rates.json")
usdc_data = load_json("kamino_usdc_borrowing_rates.json")
hl_funding_raw = load_json("sol_funding_history.json")
drift_funding_raw = load_json("drift_sol_funding_history.json")
candles = load_json("sol_daily_candles.json")

# Index by date
sol_by_date = {}
for rec in sol_data:
    d = rec["timestamp"][:10]
    sol_by_date[d] = (rec.get("apyBase") or 0.0) / 100  # as fraction

usdc_by_date = {}
for rec in usdc_data:
    d = rec["timestamp"][:10]
    usdc_by_date[d] = (rec.get("apyBaseBorrow") or 0.0) / 100

hl_daily = defaultdict(list)
for rec in hl_funding_raw:
    ts_ms = rec.get("time", rec.get("timestamp", 0))
    d = ts_ms[:10] if isinstance(ts_ms, str) else datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    hl_daily[d].append(float(rec.get("fundingRate", 0)))
hl_by_date = {d: sum(rates) for d, rates in hl_daily.items()}

drift_by_date = {rec["date"]: rec["rate"] for rec in drift_funding_raw}

price_by_date = {}
for c in candles:
    dt = datetime.fromtimestamp(c['t']/1000, tz=timezone.utc)
    d = dt.strftime("%Y-%m-%d")
    price_by_date[d] = float(c['c'])

all_dates = sorted(
    set(sol_by_date) & set(usdc_by_date) & set(hl_by_date)
    & set(drift_by_date) & set(price_by_date)
)
N = len(all_dates)

# ── Observed rates (annualized) ────────────────────────────────────

R_lend = sum(sol_by_date[d] for d in all_dates) / N   # daily avg, as fraction
R_borr = sum(usdc_by_date[d] for d in all_dates) / N
R_fund_hl = sum(hl_by_date[d] for d in all_dates) / N * 365
R_fund_drift = sum(drift_by_date[d] for d in all_dates) / N * 365
# R_lend and R_borr are already annualized APY fractions from the data
# R_fund needs annualizing (daily sum × 365)

R_lend_ann = R_lend  # already annual (apyBase is annual %)
R_borr_ann = R_borr

# ── Venue parameters ──────────────────────────────────────────────

VENUES = {
    "Hyperliquid": {
        "fee_bps": 3.5,
        "maintenance_margin": 0.05,
        "bridge_cost": 3.0,
        "R_fund": R_fund_hl,
    },
    "Drift": {
        "fee_bps": 3.5,
        "maintenance_margin": 0.03,
        "bridge_cost": 0.001,
        "R_fund": R_fund_drift,
    },
}

ASGARD_FEE_BPS = 15
GAS_COST = 2.0

CAPITALS = [1_000, 5_000, 10_000, 50_000, 100_000]
LEVERAGES = [2.0, 3.0, 3.8, 4.0]

# ── Fee calculations ──────────────────────────────────────────────

def round_trip_fee_pct(capital, leverage, venue):
    """Total open+close trading fees as % of capital (one-time, not annualized)."""
    notional = capital / 2 * leverage
    open_fee = notional * (ASGARD_FEE_BPS + venue["fee_bps"]) / 10000
    close_fee = notional * venue["fee_bps"] / 10000
    gas = GAS_COST
    return (open_fee + close_fee + gas) / capital * 100

def annual_fee_drag_pct(capital, leverage, venue, holding_days=365):
    """Annualized fee drag as % of capital, assuming one round-trip per holding period."""
    rt = round_trip_fee_pct(capital, leverage, venue)
    return rt * (365 / holding_days)

def bridge_cost_annual_pct(capital, venue, n_rebalances_per_year=6):
    """Annual bridge cost as % of capital."""
    return venue["bridge_cost"] * n_rebalances_per_year / capital * 100

# ── Breakeven formulas ────────────────────────────────────────────

def gross_apy(leverage, spread):
    """Gross APY (%) given leverage and annualized spread."""
    return leverage / 2 * spread * 100

def breakeven_spread(capital, leverage, venue, holding_days=365, n_rebals=6):
    """
    Minimum annualized spread (R_lend + R_fund − R_borr) for Net APY ≥ 0.

    Net APY = L/2 × spread − fee_drag − bridge_drag = 0
    → spread = 2/L × (fee_drag + bridge_drag)
    """
    fee_drag = annual_fee_drag_pct(capital, leverage, venue, holding_days) / 100
    bridge_drag = bridge_cost_annual_pct(capital, venue, n_rebals) / 100
    return 2 / leverage * (fee_drag + bridge_drag)

def breakeven_funding(capital, leverage, venue, holding_days=365, n_rebals=6):
    """
    Minimum annualized funding rate (as fraction) for breakeven,
    given observed lending/borrowing rates.

    spread = R_lend + R_fund − R_borr ≥ breakeven_spread
    → R_fund ≥ breakeven_spread − R_lend + R_borr
    """
    be_spread = breakeven_spread(capital, leverage, venue, holding_days, n_rebals)
    return be_spread - R_lend_ann + R_borr_ann

def max_adverse_move_pct(leverage, maintenance_margin):
    """
    Max % SOL price move (against the weaker leg) before liquidation.
    For a short leg: price rises by X% → equity drops.
    Liq when equity ≤ maintenance_margin × notional.

    equity = margin + contracts × (entry − price)
    margin = notional / leverage = N/L
    Liq when: N/L + N/P₀ × (P₀ − P₁) = mm × N/P₀ × P₁
    Simplifying: move% = (1 − mm) / (L − 1 + mm)    [for short leg]
    For long leg:  move% = (1 − mm × L) / L          [price drops]
    """
    short_move = (1 - maintenance_margin) / (leverage - 1 + maintenance_margin)
    long_move = (1 - maintenance_margin * leverage) / leverage
    return min(short_move, long_move), short_move, long_move


# ═══════════════════════════════════════════════════════════════════
# Output
# ═══════════════════════════════════════════════════════════════════

print(f"{'='*100}")
print(f"BREAKEVEN ANALYSIS: DELTA-NEUTRAL BASIS STRATEGY")
print(f"Period: {all_dates[0]} to {all_dates[-1]} ({N} days)")
print(f"{'='*100}")

# ── 1. Observed Rates ─────────────────────────────────────────────

print(f"\n### 1. Observed Annualized Rates")
print(f"\n  SOL lending APY (Kamino):     {R_lend_ann*100:>+7.2f}%")
print(f"  USDC borrowing APY (Kamino):  {R_borr_ann*100:>+7.2f}%")
print(f"  Carry spread (lend − borr):   {(R_lend_ann - R_borr_ann)*100:>+7.2f}%")
print(f"  HL funding rate:              {R_fund_hl*100:>+7.2f}%")
print(f"  Drift funding rate:           {R_fund_drift*100:>+7.2f}%")
print(f"\n  Net spread = R_lend + R_fund − R_borr:")
hl_spread = R_lend_ann + R_fund_hl - R_borr_ann
dr_spread = R_lend_ann + R_fund_drift - R_borr_ann
print(f"    Hyperliquid: {R_lend_ann*100:+.2f}% + ({R_fund_hl*100:+.2f}%) − {R_borr_ann*100:.2f}% = {hl_spread*100:>+7.2f}%")
print(f"    Drift:       {R_lend_ann*100:+.2f}% + ({R_fund_drift*100:+.2f}%) − {R_borr_ann*100:.2f}% = {dr_spread*100:>+7.2f}%")

# ── 2. Gross APY by Leverage ─────────────────────────────────────

print(f"\n### 2. Theoretical Gross APY (before fees/liqs)")
print(f"  Formula: Gross APY = L/2 × spread")
print(f"\n  {'Leverage':>8}  │ {'Hyperliquid':>12} {'Drift':>12}")
print(f"  {'-'*38}")
for lev in LEVERAGES:
    hl_gross = lev / 2 * hl_spread * 100
    dr_gross = lev / 2 * dr_spread * 100
    print(f"  {lev:>7.1f}x  │ {hl_gross:>+11.2f}% {dr_gross:>+11.2f}%")

# ── 3. Fee Drag ──────────────────────────────────────────────────

print(f"\n### 3. Round-Trip Fee Drag (open + close + gas)")
print(f"  Formula: fees = notional × (asgard_bps + venue_bps) / 10000 + close + gas")
print(f"\n  {'Capital':>10} {'Lev':>4}  │ {'HL fee%':>9} {'DR fee%':>9}  │ {'HL $':>8} {'DR $':>8}")
print(f"  {'-'*58}")
for cap in CAPITALS:
    for lev in [2.0, 3.0, 4.0]:
        hl_pct = round_trip_fee_pct(cap, lev, VENUES["Hyperliquid"])
        dr_pct = round_trip_fee_pct(cap, lev, VENUES["Drift"])
        hl_abs = hl_pct / 100 * cap
        dr_abs = dr_pct / 100 * cap
        print(f"  ${cap:>8,} {lev:>3.0f}x  │ {hl_pct:>8.3f}% {dr_pct:>8.3f}%  │ ${hl_abs:>6.1f} ${dr_abs:>6.1f}")

# ── 4. Breakeven Spread ─────────────────────────────────────────

print(f"\n### 4. Breakeven Spread (min R_lend + R_fund − R_borr to cover fees)")
print(f"  Assumes: 1 round-trip/year, ~6 rebalances/year")
print(f"\n  {'Capital':>10} {'Lev':>4}  │ {'HL BE':>10} {'DR BE':>10}  │ {'HL actual':>10} {'DR actual':>10}  │ {'HL margin':>10} {'DR margin':>10}")
print(f"  {'-'*100}")
for cap in CAPITALS:
    for lev in [2.0, 3.0, 4.0]:
        hl_be = breakeven_spread(cap, lev, VENUES["Hyperliquid"])
        dr_be = breakeven_spread(cap, lev, VENUES["Drift"])
        hl_margin = hl_spread - hl_be
        dr_margin = dr_spread - dr_be
        hl_ok = "ok" if hl_margin > 0 else "UNDER"
        dr_ok = "ok" if dr_margin > 0 else "UNDER"
        print(f"  ${cap:>8,} {lev:>3.0f}x  │ {hl_be*100:>+9.3f}% {dr_be*100:>+9.3f}%  │ "
              f"{hl_spread*100:>+9.3f}% {dr_spread*100:>+9.3f}%  │ "
              f"{hl_margin*100:>+9.3f}% {dr_margin*100:>+9.3f}%  {hl_ok:>5} / {dr_ok}")

# ── 5. Breakeven Funding Rate ───────────────────────────────────

print(f"\n### 5. Breakeven Funding Rate (min R_fund to net zero)")
print(f"  Formula: R_fund_min = BE_spread − R_lend + R_borr")
print(f"  Given: R_lend={R_lend_ann*100:.2f}%, R_borr={R_borr_ann*100:.2f}%")
print(f"\n  {'Capital':>10} {'Lev':>4}  │ {'HL min R_fund':>14} {'DR min R_fund':>14}  │ {'HL actual':>10} {'DR actual':>10}  │ Status")
print(f"  {'-'*95}")
for cap in CAPITALS:
    for lev in [2.0, 3.0, 4.0]:
        hl_bf = breakeven_funding(cap, lev, VENUES["Hyperliquid"])
        dr_bf = breakeven_funding(cap, lev, VENUES["Drift"])
        hl_ok = "ok" if R_fund_hl >= hl_bf else "NEED MORE"
        dr_ok = "ok" if R_fund_drift >= dr_bf else "NEED MORE"
        print(f"  ${cap:>8,} {lev:>3.0f}x  │ {hl_bf*100:>+13.2f}% {dr_bf*100:>+13.2f}%  │ "
              f"{R_fund_hl*100:>+9.2f}% {R_fund_drift*100:>+9.2f}%  │ HL:{hl_ok:>9}  DR:{dr_ok}")

# ── 6. Liquidation Distance ─────────────────────────────────────

print(f"\n### 6. Max SOL Move Before Liquidation (either leg)")
print(f"  Short leg liq: SOL rises by > X%    Long leg liq: SOL drops by > X%")
print(f"\n  {'Leverage':>8} {'Venue':>12}  │ {'Short liq':>10} {'Long liq':>10} {'Binding':>10}  │ {'$ range @ $189':>20}")
print(f"  {'-'*80}")
p0 = price_by_date[all_dates[0]]
for lev in LEVERAGES:
    for vname, venue in VENUES.items():
        binding, short_pct, long_pct = max_adverse_move_pct(lev, venue["maintenance_margin"])
        binding_leg = "short" if short_pct < long_pct else "long"
        price_up = p0 * (1 + short_pct)
        price_dn = p0 * (1 - long_pct)
        print(f"  {lev:>7.1f}x {vname:>12}  │ {short_pct*100:>+9.1f}% {-long_pct*100:>+9.1f}% "
              f"{'↑'+binding_leg:>10}  │ ${price_dn:.0f} – ${price_up:.0f}")

# ── 7. Combined Breakeven Table ──────────────────────────────────

print(f"\n### 7. Full Breakeven Summary ($10k)")
print(f"\n  {'Lev':>4} {'Venue':>12}  │ {'Gross':>7} {'Fees':>7} {'Bridge':>7} {'Net':>7}  │ {'Liq dist':>9} {'BE Fund':>9} {'Act Fund':>9}")
print(f"  {'-'*90}")
for lev in LEVERAGES:
    for vname, venue in VENUES.items():
        cap = 10_000
        spread = R_lend_ann + venue["R_fund"] - R_borr_ann
        gr = lev / 2 * spread * 100
        fee = annual_fee_drag_pct(cap, lev, venue)
        brg = bridge_cost_annual_pct(cap, venue)
        net = gr - fee - brg
        binding, _, _ = max_adverse_move_pct(lev, venue["maintenance_margin"])
        bf = breakeven_funding(cap, lev, venue)
        print(f"  {lev:>3.1f}x {vname:>12}  │ {gr:>+6.2f}% {fee:>6.2f}% {brg:>6.3f}% {net:>+6.2f}%  │ "
              f"{binding*100:>+8.1f}% {bf*100:>+8.2f}% {venue['R_fund']*100:>+8.2f}%")

# ── 8. Sensitivity: What Funding Rate Makes Each Combo Profitable? ─

print(f"\n### 8. Required vs Actual Funding Rate")
print(f"  Net APY = 0 when R_fund = R_borr − R_lend + 2/L × fee_drag")
print(f"  Shaded cells: actual funding meets requirement")
print(f"\n  {'Lev':>4}  │ {'Capital':>10} │ {'HL required':>12} {'HL actual':>10} {'':>3} │ {'DR required':>12} {'DR actual':>10} {'':>3}")
print(f"  {'-'*82}")
for lev in [2.0, 3.0, 3.8, 4.0]:
    for cap in [1_000, 10_000, 100_000]:
        hl_bf = breakeven_funding(cap, lev, VENUES["Hyperliquid"])
        dr_bf = breakeven_funding(cap, lev, VENUES["Drift"])
        hl_met = "<<" if R_fund_hl >= hl_bf else ""
        dr_met = "<<" if R_fund_drift >= dr_bf else ""
        print(f"  {lev:>3.1f}x │ ${cap:>8,} │ {hl_bf*100:>+11.2f}% {R_fund_hl*100:>+9.2f}% {hl_met:>3} │ "
              f"{dr_bf*100:>+11.2f}% {R_fund_drift*100:>+9.2f}% {dr_met:>3}")

# ── 9. Breakeven at Different Holding Periods ────────────────────

print(f"\n### 9. Effect of Holding Period on Breakeven ($10k)")
print(f"  Shorter holds → open/close fees amortized over fewer days → higher drag")
print(f"\n  {'Hold':>8} {'Lev':>4}  │ {'HL Net APY':>11} {'DR Net APY':>11}  │ {'HL BE Spread':>13} {'DR BE Spread':>13}")
print(f"  {'-'*70}")
for days in [90, 180, 365, 730]:
    for lev in [2.0, 3.0, 4.0]:
        hl_be = breakeven_spread(10_000, lev, VENUES["Hyperliquid"], holding_days=days)
        dr_be = breakeven_spread(10_000, lev, VENUES["Drift"], holding_days=days)
        hl_fee = annual_fee_drag_pct(10_000, lev, VENUES["Hyperliquid"], holding_days=days)
        dr_fee = annual_fee_drag_pct(10_000, lev, VENUES["Drift"], holding_days=days)
        hl_brg = bridge_cost_annual_pct(10_000, VENUES["Hyperliquid"])
        dr_brg = bridge_cost_annual_pct(10_000, VENUES["Drift"])
        hl_net = lev / 2 * hl_spread * 100 - hl_fee - hl_brg
        dr_net = lev / 2 * dr_spread * 100 - dr_fee - dr_brg
        label = f"{days}d"
        print(f"  {label:>8} {lev:>3.0f}x  │ {hl_net:>+10.2f}% {dr_net:>+10.2f}%  │ "
              f"{hl_be*100:>+12.3f}% {dr_be*100:>+12.3f}%")

# ── 10. Key Takeaways ───────────────────────────────────────────

print(f"\n### 10. Key Takeaways")
print(f"""
  The formula:  Net APY = L/2 × (R_lend + R_fund − R_borr) − fee_drag(L, C) − bridge_drag(C)

  What leverage amplifies:
    • The spread (R_lend + R_fund − R_borr) is multiplied by L/2
    • If spread > 0: higher leverage = higher gross income
    • If spread < 0: higher leverage = deeper losses
    • HL spread = {hl_spread*100:+.2f}%  →  2x: {2/2*hl_spread*100:+.2f}%,  3x: {3/2*hl_spread*100:+.2f}%,  4x: {4/2*hl_spread*100:+.2f}% gross
    • DR spread = {dr_spread*100:+.2f}%  →  2x: {2/2*dr_spread*100:+.2f}%,  3x: {3/2*dr_spread*100:+.2f}%,  4x: {4/2*dr_spread*100:+.2f}% gross

  What fees cost:
    • Fee drag scales as ~L/C (higher leverage and smaller capital = more drag)
    • At $1k/2x: {annual_fee_drag_pct(1000, 2, VENUES['Hyperliquid']):.2f}% drag.  At $100k/2x: {annual_fee_drag_pct(100000, 2, VENUES['Hyperliquid']):.3f}% drag.
    • Bridge cost matters only at small capital: $1k = {bridge_cost_annual_pct(1000, VENUES['Hyperliquid']):.2f}%/yr,  $100k = {bridge_cost_annual_pct(100000, VENUES['Hyperliquid']):.4f}%/yr

  What kills the strategy:
    • Liquidation events (not modeled here — see backtest_comparison.py)
    • The breakeven analysis assumes no liquidations. In practice, 3x+ leverage
      hits liquidation events that dominate the P&L.
    • Liquidation distance at 3x: HL={max_adverse_move_pct(3, 0.05)[0]*100:.0f}% move, Drift={max_adverse_move_pct(3, 0.03)[0]*100:.0f}% move
    • SOL moved {(price_by_date[all_dates[-1]]/price_by_date[all_dates[0]]-1)*100:.0f}% during this period — well beyond 3x liq distance
""")

print(f"{'='*100}")
print("DONE")
print(f"{'='*100}")
