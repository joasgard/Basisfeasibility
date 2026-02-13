"""
Delta-Neutral Backtest with Cross-Leg Capital Rebalancing

Key insight: In a delta-neutral strategy, price moves create opposing equity changes
on the two legs. When SOL drops, the long leg loses equity while the short leg gains
by the same amount. Instead of closing both legs (expensive), transfer capital between
them (cheap — just a bridge fee).

State model:
  Long leg (Asgard):
    - sol_qty: SOL held (grows daily from lending yield)
    - usdc_debt: USDC owed (grows daily from borrow interest)
    - equity = sol_qty × price − usdc_debt  (always derived, never stored)

  Short leg (Hyperliquid):
    - short_contracts: SOL-denominated size (fixed until position reset)
    - short_entry: average entry price (fixed until position reset)
    - short_margin: USD cash balance (grows from funding settlements)
    - equity = short_margin + short_contracts × (short_entry − price)

  Capital rebalance: adjust usdc_debt and short_margin. Positions stay same size.
  Full close+reopen: only after actual liquidation. Resets all state at current price.
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
hl_funding = load_json("sol_funding_history.json")
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

hl_daily = defaultdict(list)
for rec in hl_funding:
    ts_ms = rec.get("time", rec.get("timestamp", 0))
    d = ts_ms[:10] if isinstance(ts_ms, str) else datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    hl_daily[d].append(float(rec.get("fundingRate", 0)))
hl_by_date = {d: sum(rates) for d, rates in hl_daily.items()}

price_by_date = {}
for c in candles:
    dt = datetime.fromtimestamp(c['t']/1000, tz=timezone.utc)
    d = dt.strftime("%Y-%m-%d")
    price_by_date[d] = {"close": float(c['c']), "high": float(c['h']), "low": float(c['l'])}

all_dates = sorted(set(sol_by_date) & set(usdc_by_date) & set(hl_by_date) & set(price_by_date))
print(f"Aligned: {len(all_dates)} days ({all_dates[0]} to {all_dates[-1]})")
print(f"SOL: ${price_by_date[all_dates[0]]['close']:.2f} → ${price_by_date[all_dates[-1]]['close']:.2f} "
      f"({(price_by_date[all_dates[-1]]['close']/price_by_date[all_dates[0]]['close']-1)*100:+.1f}%)")

# ── Constants ──────────────────────────────────────────────────────

ASGARD_FEE_BPS = 15       # 0.15% on notional (open only)
HL_FEE_BPS = 3.5          # 0.035% per side
GAS_COST = 2.0            # per on-chain operation
BRIDGE_COST = 3.0         # Arbitrum ↔ Solana bridge + gas
MAINTENANCE_MARGIN = 0.05 # 5% maintenance margin

# ── Simulation ─────────────────────────────────────────────────────

def run_simulation(
    initial_capital: float,
    target_leverage: float,
    rebalance_lev_trigger: float = 0.0,
    asgard_fee_bps: float = ASGARD_FEE_BPS,
):
    """
    Simulate delta-neutral strategy with cross-leg capital rebalancing.

    State variables (the "real" state — equity is always derived from these):
      Long:  sol_qty, usdc_debt
      Short: short_contracts, short_entry, short_margin
    """
    half = initial_capital / 2
    p0 = price_by_date[all_dates[0]]["close"]

    # ── Open positions ──
    notional = half * target_leverage

    # Long leg: deposit SOL collateral, borrow USDC
    sol_qty = notional / p0                    # SOL held
    usdc_debt = half * (target_leverage - 1)   # USDC borrowed

    # Short leg: short SOL perp on HL
    short_contracts = notional / p0            # SOL contracts (matched to long)
    short_entry = p0
    short_margin = half                        # USD margin deposited

    # Opening fees: Asgard open + HL open (one side) + gas
    open_fees = (notional * asgard_fee_bps / 10000 +
                 notional * HL_FEE_BPS / 10000 +
                 GAS_COST)
    # Deduct from each leg's equity
    usdc_debt += open_fees / 2       # increase debt = reduce long equity
    short_margin -= open_fees / 2    # reduce margin = reduce short equity

    # Default rebalance trigger: 2× target leverage
    if rebalance_lev_trigger <= 0:
        rebalance_lev_trigger = target_leverage * 2

    # ── Tracking ──
    total_fees = open_fees
    total_carry = 0.0
    total_funding = 0.0
    total_liq_penalty = 0.0
    capital_rebalances = 0
    full_rotations = 0
    liquidations = 0
    events = []
    daily_equity_log = []

    long_eq = sol_qty * p0 - usdc_debt
    short_eq = short_margin  # no unrealized PnL yet

    events.append({
        "date": all_dates[0], "type": "OPEN",
        "price": p0,
        "long_eq": long_eq, "short_eq": short_eq,
        "notional": notional, "fees": open_fees,
    })

    # ── Daily loop ──
    for i, date in enumerate(all_dates):
        p = price_by_date[date]
        sol_close = p["close"]
        sol_low = p["low"]
        sol_high = p["high"]

        # ── 1. Daily yield: compound into state variables ──
        sol_lend = (sol_by_date.get(date, {}).get("lend", 0) / 100) / 365
        usdc_borr = (usdc_by_date.get(date, {}).get("borrow", 0) / 100) / 365
        funding = hl_by_date.get(date, 0)

        # Long: SOL balance grows from lending, USDC debt grows from borrowing
        sol_earned = sol_qty * sol_lend
        debt_increase = usdc_debt * usdc_borr
        carry = sol_earned * sol_close - debt_increase

        sol_qty += sol_earned
        usdc_debt += debt_increase

        # Short: funding settles to margin (on mark-to-market notional)
        fund_income = funding * short_contracts * sol_close
        short_margin += fund_income

        total_carry += carry
        total_funding += fund_income

        # ── 2. Derive current equity from state ──
        long_eq = sol_qty * sol_close - usdc_debt
        short_unrealized = short_contracts * (short_entry - sol_close)
        short_eq = short_margin + short_unrealized

        # ── 3. Check intraday liquidation (daily high/low) ──
        long_eq_at_low = sol_qty * sol_low - usdc_debt
        long_notional_at_low = sol_qty * sol_low

        short_eq_at_high = short_margin + short_contracts * (short_entry - sol_high)
        short_notional_at_high = short_contracts * sol_high

        got_liquidated = False
        liq_leg = ""

        if long_eq_at_low <= long_notional_at_low * MAINTENANCE_MARGIN:
            got_liquidated = True
            liq_leg = "LONG"
        if short_eq_at_high <= short_notional_at_high * MAINTENANCE_MARGIN:
            liq_leg = "BOTH" if got_liquidated else "SHORT"
            got_liquidated = True

        if got_liquidated:
            liquidations += 1

            # Liquidation penalty: lose 50% of remaining equity on liquidated leg
            liq_penalty = 0
            if liq_leg in ("LONG", "BOTH"):
                liq_penalty += max(long_eq * 0.5, 0)
                long_eq = max(long_eq * 0.5, 0)
            if liq_leg in ("SHORT", "BOTH"):
                liq_penalty += max(short_eq * 0.5, 0)
                short_eq = max(short_eq * 0.5, 0)
            total_liq_penalty += liq_penalty

            events.append({
                "date": date, "type": "LIQUIDATION", "leg": liq_leg,
                "price": sol_close,
                "long_eq": long_eq, "short_eq": short_eq,
            })

            # Full close + reopen at current price
            total_eq = long_eq + short_eq

            # Close old HL position (taker fee) + open new positions
            old_short_notional = short_contracts * sol_close
            new_half = total_eq / 2
            new_notional = new_half * target_leverage

            reopen_fees = (new_notional * asgard_fee_bps / 10000 +   # Asgard open
                          new_notional * HL_FEE_BPS / 10000 +        # HL open
                          old_short_notional * HL_FEE_BPS / 10000 +  # HL close old
                          GAS_COST)
            total_fees += reopen_fees
            total_eq -= reopen_fees

            # Reset all state at current price
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
            # ── 4. Check capital rebalance trigger ──
            long_notional_now = sol_qty * sol_close
            eff_lev_long = long_notional_now / max(long_eq, 1) if long_eq > 0 else 999
            short_notional_now = short_contracts * sol_close
            eff_lev_short = short_notional_now / max(short_eq, 1) if short_eq > 0 else 999

            if eff_lev_long > rebalance_lev_trigger or eff_lev_short > rebalance_lev_trigger:
                total_eq = long_eq + short_eq
                target_eq = total_eq / 2
                transfer = abs(long_eq - target_eq)

                if transfer > 5:  # min $5 to be worth bridging
                    if long_eq < target_eq:
                        # Transfer from short to long: reduce debt, withdraw margin
                        add = target_eq - long_eq
                        usdc_debt -= add
                        short_margin -= add
                    else:
                        # Transfer from long to short: increase debt, deposit margin
                        add = target_eq - short_eq
                        usdc_debt += add
                        short_margin += add

                    # Bridge fee: split across legs
                    usdc_debt += BRIDGE_COST / 2
                    short_margin -= BRIDGE_COST / 2
                    total_fees += BRIDGE_COST
                    capital_rebalances += 1

                    # Recompute equity after rebalance
                    long_eq = sol_qty * sol_close - usdc_debt
                    short_eq = short_margin + short_contracts * (short_entry - sol_close)

                    events.append({
                        "date": date, "type": "CAPITAL_REBALANCE",
                        "price": sol_close,
                        "transfer": transfer,
                        "long_eq": long_eq, "short_eq": short_eq,
                        "eff_lev_before": max(eff_lev_long, eff_lev_short),
                        "fee": BRIDGE_COST,
                    })

        total_eq = long_eq + short_eq
        daily_equity_log.append({"date": date, "equity": total_eq})

    # ── Final: include HL close fee ──
    final_price = price_by_date[all_dates[-1]]["close"]
    hl_close_fee = short_contracts * final_price * HL_FEE_BPS / 10000
    total_fees += hl_close_fee

    final_capital = long_eq + short_eq - hl_close_fee
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

    # P&L reconciliation:
    # total_carry and total_funding are USD-at-time-of-accrual.
    # SOL lending yield is earned in SOL — when SOL drops, those SOL are worth
    # less at period end than when earned. This "MTM drag" is the balancing item.
    mtm_drag = total_return - (gross - total_fees - total_liq_penalty)

    return {
        "initial": initial_capital,
        "leverage": target_leverage,
        "final": final_capital,
        "return_pct": total_return / initial_capital * 100,
        "ann_return": ann_return,
        "total_fees": total_fees,
        "liq_penalty": total_liq_penalty,
        "carry": total_carry,
        "funding": total_funding,
        "gross": gross,
        "fees_pct_gross": total_fees / max(gross, 0.01) * 100,
        "mtm_drag": mtm_drag,
        "capital_rebalances": capital_rebalances,
        "full_rotations": full_rotations,
        "liquidations": liquidations,
        "max_dd_pct": max_dd * 100,
        "events": events,
        "daily_equity": daily_equity_log,
    }


# ── Run scenarios ──────────────────────────────────────────────────

N = len(all_dates)
print(f"\n{'='*90}")
print(f"DELTA-NEUTRAL BACKTEST WITH CROSS-LEG CAPITAL REBALANCING")
print(f"Period: {all_dates[0]} to {all_dates[-1]} ({N} days)")
print(f"SOL: ${price_by_date[all_dates[0]]['close']:.2f} → ${price_by_date[all_dates[-1]]['close']:.2f}")
print(f"Rebalance cost: ${BRIDGE_COST:.0f} (bridge+gas) vs full rotation (0.15% + 0.035%×2 + gas)")
print(f"{'='*90}")

CAPITALS = [1_000, 10_000, 100_000]
LEVERAGES = [2.0, 3.0, 4.0]

# 1. Main results matrix
print(f"\n### 1. Performance Summary")
print(f"\n{'Cap':>10} {'Lev':>4} {'Ann Ret':>10} {'Gross':>10} {'Fees':>10} {'Fee%':>8} "
      f"{'Cap Rebals':>12} {'Full Rots':>10} {'Liqs':>6} {'Max DD':>8}")
print("-" * 100)
for cap in CAPITALS:
    for lev in LEVERAGES:
        r = run_simulation(cap, lev)
        print(f"${cap:>8,} {lev:>3.0f}x {r['ann_return']:>+8.1f}% ${r['gross']:>8,.0f} "
              f"${r['total_fees']:>8,.0f} {r['fees_pct_gross']:>6.1f}% "
              f"{r['capital_rebalances']:>10} {r['full_rotations']:>8} "
              f"{r['liquidations']:>5} {r['max_dd_pct']:>6.1f}%")

# 2. Optimal rebalance trigger per leverage — $10k
print(f"\n### 2. Optimal Rebalance Trigger — $10k")
print(f"(Trigger = rebalance when effective leverage on either leg exceeds this)")
for lev in LEVERAGES:
    print(f"\n  {lev:.0f}x leverage:")
    print(f"  {'Trigger':>10} {'Ann Ret':>10} {'Rebals':>8} {'Rots':>6} {'Liqs':>6} {'Fees':>8} {'Liq Pen':>9}")
    print(f"  {'-'*63}")
    best_ret = -999
    best_trig = 0
    for trigger in [3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 999.0]:
        if trigger < lev * 1.3:
            continue  # skip triggers below ~1.3× leverage (would rebalance constantly)
        label = f"{trigger:.0f}x" if trigger < 100 else "never"
        r = run_simulation(10_000, lev, rebalance_lev_trigger=trigger)
        marker = ""
        if r['ann_return'] > best_ret:
            best_ret = r['ann_return']
            best_trig = trigger
        print(f"    {label:>8} {r['ann_return']:>+8.1f}% {r['capital_rebalances']:>6} "
              f"{r['full_rotations']:>5} {r['liquidations']:>5} ${r['total_fees']:>6,.0f} "
              f"${r['liq_penalty']:>7,.0f}")
    trig_label = f"{best_trig:.0f}x" if best_trig < 100 else "never"
    print(f"  → Best: {trig_label} trigger ({best_ret:+.1f}%)")

# 3. Fee breakdown comparison
print(f"\n### 3. Rebalance Model — Fee Breakdown")
print(f"\n{'Cap':>10} {'Lev':>4} │ {'Ann Ret':>10} {'Fees':>10} {'Rebals':>8} │ Note")
print("-" * 75)
for cap in [1_000, 10_000]:
    for lev in LEVERAGES:
        r = run_simulation(cap, lev)
        print(f"${cap:>8,} {lev:>3.0f}x │ {r['ann_return']:>+8.1f}% ${r['total_fees']:>8.0f} "
              f"{r['capital_rebalances']:>6} cap │ "
              f"{r['full_rotations']} full rotations, {r['liquidations']} liquidations")

# 4. Detail: events for $10k at 2x and 3x
for show_lev in [2.0, 3.0]:
    print(f"\n### 4{'a' if show_lev == 2 else 'b'}. Event Log — $10k, {show_lev:.0f}x")
    r = run_simulation(10_000, show_lev)
    for ev in r['events']:
        if ev['type'] == 'OPEN':
            print(f"  {ev['date']}  OPEN   @ ${ev['price']:.2f}  "
                  f"long_eq=${ev['long_eq']:.0f} short_eq=${ev['short_eq']:.0f}  "
                  f"notional=${ev['notional']:.0f}  fees=${ev['fees']:.0f}")
        elif ev['type'] == 'CAPITAL_REBALANCE':
            print(f"  {ev['date']}  REBAL  @ ${ev['price']:.2f}  "
                  f"transfer=${ev['transfer']:.0f}  eff_lev={ev['eff_lev_before']:.1f}x  "
                  f"→ long_eq=${ev['long_eq']:.0f} short_eq=${ev['short_eq']:.0f}  fee=${ev['fee']:.0f}")
        elif ev['type'] == 'LIQUIDATION':
            print(f"  {ev['date']}  **LIQ** @ ${ev['price']:.2f}  leg={ev['leg']}  "
                  f"long_eq=${ev['long_eq']:.0f} short_eq=${ev['short_eq']:.0f}")
        elif ev['type'] == 'REOPEN_AFTER_LIQ':
            print(f"  {ev['date']}  REOPEN @ ${ev['price']:.2f}  "
                  f"long_eq=${ev['long_eq']:.0f} short_eq=${ev['short_eq']:.0f}  "
                  f"notional=${ev['notional']:.0f}  fees=${ev['fees']:.0f}")

# 5. Asgard fee sensitivity with rebalance model
print(f"\n### 5. Asgard Fee Sensitivity — $10k, 3x (rebalance model)")
print(f"\n{'Fee':>8} {'Ann Ret':>10} {'Total Fees':>12}")
print("-" * 32)
for fee_bps in [15, 10, 5, 0]:
    r = run_simulation(10_000, 3.0, asgard_fee_bps=fee_bps)
    print(f"  {fee_bps/100:.2f}% {r['ann_return']:>+8.1f}% ${r['total_fees']:>10,.0f}")

# 6. P&L waterfall — shows where every dollar went
print(f"\n### 6. P&L Waterfall — $10k")
print(f"  (Carry is SOL-denominated yield valued at daily price; MTM drag = revaluation")
print(f"   of accumulated SOL when SOL price falls. Funding settles in USD, no drag.)")
print(f"\n{'Lev':>4} {'Carry':>10} {'Funding':>10} {'Gross':>10} {'Fees':>8} {'Liq Pen':>9} {'MTM Drag':>10} {'Return':>10}")
print("-" * 77)
for lev in LEVERAGES:
    r = run_simulation(10_000, lev)
    print(f" {lev:.0f}x  ${r['carry']:>8,.0f} ${r['funding']:>8,.0f} ${r['gross']:>8,.0f} "
          f"${r['total_fees']:>6,.0f} ${r['liq_penalty']:>7,.0f} ${r['mtm_drag']:>8,.0f}  "
          f"${r['final'] - r['initial']:>+8,.0f} ({r['ann_return']:>+.1f}%)")

# 7. Delta-neutral sanity check: show equity stability
print(f"\n### 7. Delta-Neutral Sanity Check — $10k, 3x")
r = run_simulation(10_000, 3.0)
eq = r['daily_equity']
eq_values = [e['equity'] for e in eq]
print(f"  Start equity:  ${eq_values[0]:,.0f}")
print(f"  End equity:    ${eq_values[-1]:,.0f}")
print(f"  Min equity:    ${min(eq_values):,.0f}  (on {eq[eq_values.index(min(eq_values))]['date']})")
print(f"  Max equity:    ${max(eq_values):,.0f}  (on {eq[eq_values.index(max(eq_values))]['date']})")
print(f"  Std dev:       ${(sum((v - sum(eq_values)/len(eq_values))**2 for v in eq_values)/len(eq_values))**0.5:,.0f}")
print(f"  SOL moved:     {(price_by_date[all_dates[-1]]['close']/price_by_date[all_dates[0]]['close']-1)*100:+.1f}%")
print(f"  Equity moved:  {(eq_values[-1]/eq_values[0]-1)*100:+.1f}%")
print(f"  → Strategy is {'delta-neutral ✓' if abs(eq_values[-1]/eq_values[0]-1) < 0.15 else 'NOT delta-neutral ✗'}")

print(f"\n{'='*90}")
print("DONE")
print(f"{'='*90}")
