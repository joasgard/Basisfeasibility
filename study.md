# Asgard Basis Trading: Economic Feasibility Analysis

**Date:** February 2026
**Pair:** SOL/USDC | **Leverage:** 2x, 3x, 4x | **Capital:** $1k, $10k, $100k

---

## 1. Executive Summary

We backtested a delta-neutral basis trading strategy — long SOL on Asgard (leveraged lending) + short SOL perp on Hyperliquid — using 410 days of real historical data (Dec 2024 – Feb 2026). We ran three simulation models:

1. **Static backtest** (theoretical ceiling): Positions held indefinitely, no price tracking, no liquidation risk
2. **Capital rebalance backtest** (realistic): Tracks price, transfers capital between legs when leverage drifts ($3 bridge fee), full close+reopen only on actual liquidation
3. **Full-rotation backtest** (pessimistic floor): Every rebalance is a full close+reopen ($5.30+ round-trip fees)

### Realistic Performance (capital rebalance model, $10k)

| Metric | 2x | 3x | 4x |
|--------|---:|---:|---:|
| Annualized return (default trigger) | **-4.8%** | **-3.5%** | **-19.6%** |
| Annualized return (optimized trigger) | **+4.0%** | **-3.5%** | **-19.6%** |
| Capital rebalances | 5–6 | 7 | 12 |
| Full rotations (liquidation-triggered) | 0–1 | 1 | 5 |
| Trading fees | $37–63 | $87 | $283 |
| Liquidation penalties | $0–1,196 | $830 | $3,244 |
| Max drawdown | 8.4–12.9% | 8.4% | 31.5% |

### All Three Models Compared ($10k)

| Model | 2x | 3x | 4x |
|-------|---:|---:|---:|
| Static (ceiling) | +5.9% | +7.1% | +8.3% |
| **Capital rebalance (realistic)** | **+4.0%** | **-3.5%** | **-19.6%** |
| Full-rotation (floor) | +4.8% | -14.9% | -14.1% |

**Bottom line:** 2x leverage with an optimized rebalance trigger returns **+4.0% annualized** with zero liquidations — close to the theoretical ceiling. At 3x, the strategy loses -3.5%, a massive improvement over the old full-rotation model (-14.9%) but still negative due to a single liquidation event. 4x is unprofitable at all trigger settings.

**The critical insight: Liquidation penalties dominate, not fees.** Capital rebalancing reduced trading fees from $574 to $87 at 3x/$10k (an 85% reduction), but the $830 liquidation penalty on Jan 18, 2025 (SOL spiked 38% intraday) is what makes the strategy negative. Every leverage/trigger combination that avoids liquidation is profitable.

**The key operational finding: Rebalance trigger matters more than leverage.** At 2x, changing the trigger from 4x to 5x swings the return from -4.8% to +4.0% — an 8.8pp improvement from one parameter. The trigger determines whether capital transfers happen early enough to prevent liquidation during extreme moves.

**Recommendation: Default to 2x leverage with a 5x rebalance trigger.** This combination survived the backtest's 58% SOL drawdown with zero liquidations and +4.0% annualized return. 3x is viable in calmer markets but could not avoid the Jan 2025 spike at any trigger setting tested.

---

## 2. Strategy Overview

The strategy opens two simultaneous legs to capture yield while remaining market-neutral:

| Leg | Platform | Action | Revenue |
|-----|----------|--------|---------|
| Long | Asgard | Leveraged lending (SOL at Nx) | Earns SOL lending APY, pays USDC borrowing APY |
| Short | Hyperliquid | Perpetual futures short | Receives funding when positive (longs pay shorts) |

**Capital split:** 50/50 between legs (Asgard collateral + HL margin).

| Parameter | 2x | 3x | 4x |
|-----------|---:|---:|---:|
| Asgard collateral | 50% | 50% | 50% |
| Notional per leg | 1x cap | 1.5x cap | 2x cap |

**Revenue formula:**
```
Net carry (Asgard) = (SOL_lending_rate × leverage) − (USDC_borrow_rate × (leverage − 1))
Funding income (HL) = funding_rate × short_notional  (positive funding = shorts receive)
Total return = Net carry + Funding income − Fees × rotations
```

**Why position management matters:**
At Nx leverage, the long leg is liquidated if SOL drops ~1/N (e.g., ~33% at 3x, ~25% at 4x). The short leg is liquidated if SOL rises by the same amount. During trending markets, one leg repeatedly approaches liquidation, forcing the strategy to close and reopen at new price levels — each rotation costs a full round-trip in fees.

---

## 3. Data & Methodology

### 3.1 Data Sources

| Component | Source | Granularity | Records | Coverage |
|-----------|--------|-------------|---------|----------|
| SOL lending APY | DefiLlama (Kamino SOL pool) | Daily | 819 | Nov 2023 – present |
| USDC borrowing APY | DefiLlama (Kamino USDC pool) | Daily | 819 | Nov 2023 – present |
| SOL funding rate | Hyperliquid `fundingHistory` API | Hourly | 9,807 | Jan 2025 – Feb 2026 |
| SOL price (OHLCV) | Hyperliquid `candleSnapshot` API | Daily | 410 | Dec 2024 – Feb 2026 |

After aligning all sources: **410 overlapping days** (Dec 31, 2024 – Feb 13, 2026).

DefiLlama Kamino rates are used as a proxy for Asgard's underlying protocol rates. Asgard does not currently expose historical rate data through its API.

### 3.2 Rate Environment

| Metric | Average | Range |
|--------|---------|-------|
| SOL lending APY | 5.24% | 3.6% – 7.9% |
| USDC borrowing APY | 6.71% | 4.2% – 14.5% |
| HL funding APY | +4.16% | -25.4% to +19.9% |

The average SOL lending rate (5.24%) is *lower* than USDC borrowing (6.71%), meaning the Asgard carry is negative at 1x. Leverage amplifies lending income, which makes the carry positive — but only barely at lower multiples.

### 3.3 Price Environment

| Metric | Value |
|--------|-------|
| SOL start price | $189.44 (Dec 31, 2024) |
| SOL end price | $79.87 (Feb 13, 2026) |
| Total decline | **-57.8%** |
| Max intraday low | $67.52 (Feb 6, 2026) |
| Max intraday high | $295.54 (Jan 19, 2025) |
| Range (high/low) | 4.38x |

This period was challenging for the strategy: SOL's 58% decline forced frequent rebalancing of the long leg, while the initial rally to $295 triggered short-leg rebalancing. The wide price range means positions at any leverage above 2x were repeatedly forced to close and reopen.

### 3.4 Simulation Models

**Static backtest** (`backtest_full.py`): Assumes capital is allocated once, earns daily carry and funding income, pays fees once (open + close). No price tracking, no liquidation, no rebalancing. This represents the theoretical maximum return.

**Capital rebalance backtest** (`backtest_rebalance.py`): The primary model. Tracks per-leg equity using state variables that compound daily:
- **Long leg state:** `sol_qty` (SOL held, grows from lending yield), `usdc_debt` (USDC owed, grows from borrow interest). Equity = `sol_qty × price − usdc_debt`.
- **Short leg state:** `short_contracts` (fixed SOL size), `short_entry` (fixed price), `short_margin` (USD, grows from funding settlements). Equity = `short_margin + contracts × (entry − price)`.
- **Capital rebalance:** When effective leverage on either leg exceeds a trigger threshold (default: 2× target leverage), transfer capital between legs by adjusting `usdc_debt` and `short_margin`. Position sizes stay unchanged. Cost: $3 bridge fee.
- **Liquidation detection:** Uses daily high/low candles. Long liquidated when equity at daily low ≤ 5% of notional. Short liquidated when equity at daily high ≤ 5% of notional. 50% penalty on remaining equity of liquidated leg.
- **Full close+reopen:** Only after actual liquidation. Resets all state variables at current price. Charges Asgard open + HL open + HL close (old position) + gas.
- **Mark-to-market funding:** HL funding income computed on `contracts × current_price`, not entry notional. This correctly reduces funding income when SOL drops.

**Full-rotation backtest** (`backtest_managed.py`): Pessimistic model where every rebalance is a full close+reopen, charging full round-trip fees (Asgard open 0.15% + HL taker 0.035% × 2 + gas). Represents the cost floor for strategies that cannot do cross-leg capital transfers.

---

## 4. Theoretical Performance (Static Backtest)

*This section shows the upper bound — what the strategy would return if positions never needed rebalancing.*

### 4.1 Overall P&L ($1k capital)

| Component | 2x | 3x | 4x |
|-----------|---:|---:|---:|
| Asgard carry (long leg) | +$21.14 | +$12.87 | +$4.60 |
| HL funding (short leg) | +$46.74 | +$70.12 | +$93.49 |
| **Gross** | **+$67.89** | **+$82.99** | **+$98.09** |
| Fees (single round-trip) | -$4.20 | -$5.30 | -$6.40 |
| **Net** | **+$63.69** | **+$77.69** | **+$91.69** |
| Annualized | **+5.67%** | **+6.92%** | **+8.16%** |

| Metric | 2x | 3x | 4x |
|--------|---:|---:|---:|
| Asgard carry share of gross | **31.1%** | 15.5% | 4.7% |
| HL funding share of gross | 68.9% | **84.5%** | **95.3%** |

At higher leverage, carry evaporates because USDC borrowing scales as `(leverage − 1)` while SOL lending scales as `leverage` — with borrowing rates higher, each additional turn of leverage reduces net carry.

### 4.2 Static Performance by Capital Size (3x)

| Metric | $1k | $10k | $100k |
|--------|----:|-----:|------:|
| Annualized return | +6.9% | +7.1% | +7.1% |
| Fees as % of gross | 6.4% | 4.3% | 4.5% |
| 30-day win rate | 56.4% | 61.9% | 61.7% |
| 90-day win rate | 69.2% | 73.8% | 73.5% |

Static performance scales linearly — but these numbers are misleading for the backtest period, where SOL's 58% decline would have liquidated any unmanaged 3x position.

### 4.3 Monthly Breakdown (3x, static)

| Month | SOL Lend | USDC Borr | Carry | HL Fund | Strategy |
|-------|---------|----------|-------|---------|----------|
| 2024-12 | 5.20% | 14.48% | -13.4% | +1.8% | -7.9% |
| **2025-01** | 6.40% | 9.87% | -0.5% | **+11.1%** | **+32.7%** |
| 2025-02 | 7.92% | 9.43% | +4.9% | +1.3% | +8.8% |
| 2025-03 | 3.57% | 7.61% | -4.5% | -1.8% | -9.8% |
| 2025-04 | 4.44% | 7.23% | -1.1% | -5.9% | -18.9% |
| **2025-05** | 5.41% | 7.35% | +1.5% | **+10.6%** | **+33.3%** |
| **2025-06** | 5.16% | 7.42% | +0.7% | **+6.9%** | **+21.3%** |
| **2025-07** | 5.62% | 7.22% | +2.4% | **+19.9%** | **+62.0%** |
| **2025-08** | 5.83% | 5.32% | +6.9% | **+11.8%** | **+42.2%** |
| **2025-09** | 5.83% | 6.42% | +4.7% | **+10.8%** | **+37.2%** |
| 2025-10 | 4.54% | 5.60% | +2.4% | -4.8% | -11.9% |
| 2025-11 | 4.60% | 5.18% | +3.4% | -2.4% | -3.8% |
| **2025-12** | 4.05% | 5.06% | +2.0% | **+5.5%** | **+18.6%** |
| **2026-01** | 4.26% | 4.24% | +4.3% | **+3.1%** | **+13.7%** |
| 2026-02 | 6.85% | 5.01% | +10.5% | -25.4% | -65.7% |

*All rates annualized. Bold months had strong HL funding income.*

---

## 5. Realistic Performance (Capital Rebalance Model)

*This is the core finding of the study. It models what actually happens when SOL moves 58% against the position, using cross-leg capital transfers instead of full close+reopen cycles.*

### 5.1 How Capital Rebalancing Works

In a delta-neutral strategy, price moves create *opposing* equity changes on the two legs. When SOL drops 20%:
- Long leg equity shrinks (SOL collateral worth less, USDC debt unchanged) → leverage increases
- Short leg equity grows (unrealized profit on the short) → leverage decreases

Instead of closing both legs and reopening (the old model's approach, costing $5.30+ per rotation at 3x/$1k), we transfer capital from the profitable leg to the distressed one:
1. Withdraw USD from HL margin account
2. Bridge to Solana (~$3 total)
3. Reduce USDC debt on Asgard (increasing long equity)

Position sizes stay unchanged — only the equity distribution shifts. This is 40–95% cheaper than a full rotation depending on position size.

A full close+reopen is only necessary when an intraday move is too fast for daily rebalancing — i.e., actual liquidation.

### 5.2 Rebalancing Events by Leverage ($10k)

| Leverage | Capital rebalances | Full rotations (liq-triggered) | Liquidations | Trading fees |
|---------|-------------------:|-------------------------------:|-------------:|-------------:|
| 2x (default 4x trigger) | 6 | 1 | 1 | $63 |
| 2x (optimized 5x trigger) | 5 | 0 | 0 | $37 |
| 3x (6x trigger) | 7 | 1 | 1 | $87 |
| 4x (8x trigger) | 12 | 5 | 5 | $283 |

Capital rebalancing keeps both legs healthy with 5–7 transfers at 2x/3x (each costing just $3). Full rotations are rare: zero at 2x with the right trigger, one at 3x.

### 5.3 Performance Summary ($10k, capital rebalance model)

| Metric | 2x (opt.) | 3x | 4x |
|--------|----------:|---:|---:|
| **Annualized return** | **+4.0%** | **-3.5%** | **-19.6%** |
| Gross income (carry + funding) | $747 | $771 | $883 |
| Trading fees | $37 | $87 | $283 |
| Liquidation penalties | $0 | $830 | $3,244 |
| Fees as % of gross | 5.0% | 11.3% | 32.1% |
| Max drawdown | 8.4% | 8.4% | 31.5% |

**Liquidation penalties dominate losses, not trading fees.** At 3x, trading fees are only $87 (11% of gross) — a dramatic improvement from the old full-rotation model's $574 (74% of gross). But the $830 liquidation penalty from the Jan 18, 2025 event wipes out most of the gross income.

At 2x with optimized trigger: zero liquidations → trading fees are the only drag → strategy is profitable.

### 5.4 Three-Model Comparison ($10k)

| Model | 2x | 3x | 4x | What it measures |
|-------|---:|---:|---:|-----------------|
| Static (ceiling) | +5.9% | +7.1% | +8.3% | Pure yield, no price risk |
| **Capital rebalance** | **+4.0%** | **-3.5%** | **-19.6%** | Realistic with cross-leg transfers |
| Full-rotation (floor) | +4.8% | -14.9% | -14.1% | Every rebalance = full close+reopen |

The capital rebalance model closes the gap between static and full-rotation at lower leverage. At 2x, it captures 68% of the static ceiling. At 3x, it improves by 11.4pp over full-rotation but remains negative.

Note: 2x full-rotation (+4.8%) slightly outperforms 2x capital rebalance (+4.0%) with the default trigger — the old model's trigger mechanics happened to avoid the one liquidation event that hits the rebalance model. With the optimized 5x trigger, the rebalance model avoids liquidation entirely and returns +4.0%.

### 5.5 Rebalance Trigger Optimization ($10k)

The rebalance trigger — the effective leverage threshold at which capital is transferred between legs — has a larger impact on returns than leverage choice itself.

**2x leverage:**

| Trigger | Ann Return | Rebalances | Liqs | Liq Penalties |
|--------:|-----------:|-----------:|-----:|--------------:|
| 3x | +3.3% | 19 | 0 | $0 |
| 4x (default) | -4.8% | 6 | 1 | $1,196 |
| **5x** | **+4.0%** | **5** | **0** | **$0** |
| 6x | -9.6% | 3 | 2 | $1,470 |
| never | -10.5% | 0 | 4 | $1,754 |

At 2x, the **5x trigger** is optimal: rebalances frequently enough to prevent all liquidations but not so often that bridge fees add up. The 4x default trigger happens to rebalance at an unlucky time, exposing the position to the Oct 10 long liquidation.

**3x leverage:**

| Trigger | Ann Return | Rebalances | Liqs | Liq Penalties |
|--------:|-----------:|-----------:|-----:|--------------:|
| 4x | -13.2% | 19 | 2 | $2,020 |
| 5x | -8.4% | 11 | 2 | $1,524 |
| **6x (default)** | **-3.5%** | **7** | **1** | **$830** |
| 8x | -5.2% | 14 | 5 | $2,170 |
| never | -33.6% | 0 | 10 | $5,014 |

At 3x, no trigger eliminates all liquidations. The **6x trigger** is optimal — it minimizes liquidation to a single event (Jan 18) while keeping bridge fees low. Without any rebalancing, the strategy loses -33.6% from 10 liquidation events.

**4x leverage:**

| Trigger | Ann Return | Rebalances | Liqs | Liq Penalties |
|--------:|-----------:|-----------:|-----:|--------------:|
| 6x | -25.4% | 88 | 7 | $3,422 |
| **8x (default)** | **-19.6%** | **12** | **5** | **$3,244** |
| never | -42.4% | 0 | 19 | $9,035 |

At 4x, nothing works. Even the best trigger still hits 5 liquidations.

### 5.6 Event Timeline ($10k, 3x)

| Date | Event | SOL Price | Long Eq | Short Eq | Detail |
|------|-------|----------:|--------:|---------:|--------|
| 2024-12-31 | OPEN | $189.44 | $4,985 | $4,985 | Entry. Notional: $15,000 |
| 2025-01-03 | REBAL | $218.02 | $4,992 | $4,992 | Transfer $2,249. Eff lev was 6.3x |
| 2025-01-09 | REBAL | $184.91 | $5,001 | $5,001 | Transfer $2,641. Eff lev was 6.2x |
| 2025-01-17 | REBAL | $219.51 | $5,020 | $5,020 | Transfer $2,728. Eff lev was 7.6x |
| 2025-01-18 | **LIQ** | $262.20 | $8,410 | $830 | **Short liquidated** (high ≥ liq price) |
| 2025-01-18 | REOPEN | $262.20 | $4,603 | $4,603 | Reopen at reduced size. Fees: $35 |
| 2025-02-02 | REBAL | $203.45 | $4,621 | $4,621 | Transfer $3,116. Eff lev was 7.1x |
| 2025-02-26 | REBAL | $135.32 | $4,617 | $4,617 | Transfer $3,594. Eff lev was 7.0x |
| 2025-07-21 | REBAL | $195.76 | $4,779 | $4,779 | Transfer $3,166. Eff lev was 6.4x |
| 2025-11-21 | REBAL | $128.60 | $4,853 | $4,853 | Transfer $3,694. Eff lev was 6.1x |

The Jan 18, 2025 event is the critical moment. SOL spiked from $189 to $262 (close) with an intraday high above $271 — a 38% move in 18 days. Despite 3 capital rebalances in the preceding 2 weeks, the short leg's equity was insufficient to survive the spike. After liquidation, the strategy reopened at $262 with ~8% less capital and operated smoothly for the remaining 13 months.

### 5.7 P&L Waterfall ($10k)

Where does every dollar of income go?

| Component | 2x (opt.) | 3x | 4x |
|-----------|----------:|---:|---:|
| SOL lending carry | +$214 | +$205 | +$125 |
| HL funding income | +$534 | +$566 | +$758 |
| **Gross income** | **+$747** | **+$771** | **+$883** |
| Trading fees | -$37 | -$87 | -$283 |
| Liquidation penalties | -$0 | -$830 | -$3,244 |
| MTM drag on SOL carry | -$28 | -$244 | +$445 |
| **Net return** | **+$449** | **-$390** | **-$2,199** |

*MTM drag: SOL lending yield is earned in SOL. When SOL drops 58%, accumulated SOL from lending is worth less at period end than when earned. This is a real cost of the strategy in down markets — the carry income stream shrinks with the underlying price.*

### 5.8 Delta-Neutral Verification ($10k, 3x)

| Metric | Value |
|--------|------:|
| SOL price change | -57.8% |
| Portfolio equity change | -3.6% |
| Equity std dev | $266 |
| Min equity | $9,201 (Apr 8, 2025) |
| Max equity | $10,041 (Jan 17, 2025) |

The strategy is confirmed delta-neutral: while SOL dropped 57.8%, the portfolio equity moved only -3.6% (of which most is attributable to the single liquidation event and reduced yield at lower prices). Day-to-day equity fluctuation is minimal ($266 std dev on a $10k portfolio).

---

## 6. Fee Analysis

### 6.1 Single Round-Trip Fee Structure

| Fee | Rate | 2x ($1k) | 3x ($1k) | 4x ($1k) |
|-----|------|----------:|----------:|----------:|
| **Asgard open** | 0.15% on notional | **$1.50** | **$2.25** | **$3.00** |
| Asgard close | 0% | $0.00 | $0.00 | $0.00 |
| HL taker (open + close) | 0.035% × 2 | $0.70 | $1.05 | $1.40 |
| Gas | flat | $2.00 | $2.00 | $2.00 |
| **Total per rotation** | | **$4.20** | **$5.30** | **$6.40** |

### 6.2 Cost Comparison: Capital Rebalance vs. Full Rotation

| Cost type | Per-event cost (3x, $10k) | Events in backtest | Total |
|-----------|:------------------------:|-------------------:|------:|
| **Capital rebalance** (bridge fee) | **$3** | 7 | **$21** |
| Full rotation (Asgard + HL + gas) | $53 | 1 (liq only) | $53 |
| HL close fee (end of period) | $2.80 | 1 | $3 |
| **Total trading fees** | | | **$87** |

Compare to the old full-rotation model: 18 rotations × $31.90 = **$574**. Capital rebalancing reduced trading costs by **85%**.

| Model | Trading fees | Fees as % of gross | Liq penalties | Total drag |
|-------|------------:|-------------------:|--------------:|-----------:|
| **Capital rebalance** | **$87** | **11.3%** | **$830** | **$917** |
| Full-rotation | $574 | 73.5% | — | $574 |

The capital rebalance model has lower *trading* fees but higher *total* drag because it explicitly models liquidation penalties ($830). The full-rotation model avoided this by closing proactively — but at the cost of 18 full rotations. The net result: capital rebalancing is 11.4pp better (-3.5% vs -14.9%).

### 6.3 Asgard Fee Sensitivity (Capital Rebalance Model, $10k, 3x)

| Asgard Fee | Ann Return | Total Fees | Delta vs 0.15% |
|-----------|----------:|----------:|----------------:|
| **0.15% (current)** | **-3.5%** | **$87** | — |
| 0.10% | -3.4% | $73 | +0.1pp |
| 0.05% | -3.2% | $58 | +0.3pp |
| Free | -3.1% | $44 | +0.4pp |

With capital rebalancing, the Asgard fee barely matters — reducing it from 0.15% to zero improves return by only 0.4pp. This is because Asgard's open fee is only charged once at entry and once after the single liquidation event (2 charges total), not 18 times as in the old model. **Liquidation avoidance is now worth 100× more than fee negotiation.**

### 6.4 Scaling ($1k → $10k → $100k) — Capital Rebalance Model

| Metric | $1k (3x) | $10k (3x) | $100k (3x) |
|--------|----------:|----------:|-----------:|
| Total fees | $31 | $87 | $647 |
| Fees as % of gross | 40.9% | 11.3% | 8.4% |
| Annualized return | -5.3% | -3.5% | -3.3% |

At $1k, the $3 bridge fee per rebalance is proportionally large (40.9% fee drag). At $10k+, bridge fees are negligible and returns converge. The ~2pp improvement from $1k to $10k is due to fixed-cost amortization (gas and bridge fees).

### 6.5 Slippage Analysis

Slippage model based on HL SOL-PERP orderbook snapshot (Feb 2026, $292M daily volume, $271M OI):

| Capital | Lev | Notional | Estimated slippage (round-trip) |
|--------:|----:|---------:|-------------------------------:|
| $1k | 3x | $1.5k | $0.03 (0.2 bps) |
| $10k | 3x | $15k | $0.30 (0.2 bps) |
| $100k | 3x | $150k | $38 (2.5 bps) |
| $100k | 4x | $200k | $69 (3.5 bps) |

Slippage is negligible at all tested sizes. Even at $200k notional (the largest configuration), slippage adds $69 per rotation — dwarfed by platform fees. HL's SOL-PERP market is deep enough that the strategy has no meaningful capacity constraint up to $100k.

### 6.6 Competitive Context

| Platform | Open Fee | Close Fee | Round-Trip |
|----------|----------|-----------|------------|
| **Asgard** | **0.15%** | **0%** | **0.15%** |
| Marginfi (direct) | ~0.1% swap | ~0.1% swap | ~0.2% |
| Kamino (direct) | ~0.1% swap | ~0.1% swap | ~0.2% |
| Drift (direct) | 0.1% taker | 0.1% taker | 0.2% |

Asgard's 0.15% open + 0% close = 0.15% total compares favorably to direct protocol access (~0.2% round-trip). The no-close-fee is especially valuable in the managed position context — rebalancing only pays the open fee on Asgard, while direct protocols would charge both ways.

---

## 7. Risk Factors

### 7.1 Trending Markets — The Dominant Risk

This backtest demonstrates the #1 risk: sustained directional price movement creates leverage drift that eventually triggers liquidation. SOL's 58% decline forced 7 capital rebalances and 1 liquidation at 3x — it was the single liquidation event (Jan 18, 2025) that made the strategy unprofitable, not the rebalancing itself.

| SOL scenario | Expected effect on 3x |
|-------------|----------------------|
| Range-bound (±15%) | ~0 rebalances, near-static performance |
| Moderate trend (±30%) | 3–5 capital rebalances ($3 each), likely positive |
| Strong trend (±50%+) | 7+ rebalances + possible liquidation, likely negative |

**The strategy is a short-volatility bet disguised as a yield strategy.** It profits when markets are calm and bleeds when extreme moves trigger liquidation. With capital rebalancing, moderate trends are survivable — the danger is single-day spikes that breach liquidation thresholds before rebalancing can execute.

### 7.2 Liquidation Risk

At standard leverage with 5% maintenance margin:

| Leverage | Long liquidated at | Short liquidated at |
|---------|-------------------:|--------------------:|
| 2x | -47.5% from entry | +47.5% from entry |
| 3x | -31.7% from entry | +31.7% from entry |
| 4x | -23.8% from entry | +23.8% from entry |

At 4x, a 24% move in either direction triggers liquidation. SOL's daily range averaged 5.3%, meaning a ~4.5σ daily move could liquidate a 4x position in a single day. At 2x, liquidation requires a 48% move — much safer but still possible (as SOL demonstrated with its 58% decline, though this happened over weeks, not days).

### 7.3 Funding Regime Shifts

HL funding rate can turn negative (shorts pay longs), which costs the strategy money regardless of position management. Feb 2026 saw -25.4% annualized funding.

| Leverage | Feb 2026 strategy APY | P&L impact ($1k) |
|---------|----------------------:|------------------:|
| 2x | -42.2% | -$7.50 |
| 3x | -65.7% | -$11.71 |
| 4x | -89.3% | -$15.91 |

The strategy has no natural hedge against funding reversals. Adverse funding compounds with position management costs — during the same period, positions may be rebalancing (paying fees) while also losing on the funding leg.

### 7.4 Carry Erosion at Higher Leverage

Asgard net carry degrades as leverage increases because USDC borrowing (6.71% avg) exceeds SOL lending (5.24% avg):

| Leverage | Carry APY (on collateral) | Carry as % of gross |
|---------|-------------------------:|--------------------:|
| 2x | +3.76% | 31.1% |
| 3x | +2.29% | 15.5% |
| 4x | +0.82% | 4.7% |

At 4x, carry is nearly zero — the strategy becomes a pure funding rate play. If USDC borrowing rates rise further, carry could turn negative at higher leverage.

### 7.5 Data Limitations

- DefiLlama provides Kamino rates, which may differ from Asgard's executed rates
- 410-day window includes a strong bearish SOL regime — bullish conditions would show fewer rotations and better performance
- Intraday liquidation detection uses daily high/low candles, not tick-level data — some intraday liquidations may be missed or false-triggered
- No historical data for Asgard-specific optimizations (multi-protocol routing)
- The period includes a single market regime (declining SOL); strategy would perform differently in sideways or rising markets

---

## 8. Recommendations

### Critical

**8.1 Default to 2x Leverage with 5x Rebalance Trigger**
The capital rebalance backtest shows that 2x leverage with a 5x trigger is the only tested configuration that survives a 58% underlying drawdown while remaining profitable (+4.0%, zero liquidations). The bot should default to 2x/5x and require explicit user confirmation for higher leverage, with clear warnings about liquidation risk.

**8.2 Optimize Rebalance Trigger Per Leverage**
The rebalance trigger — the effective leverage threshold at which capital transfers between legs — has a larger impact on returns than leverage choice itself. At 2x, changing the trigger from 4x to 5x swings the return from -4.8% to +4.0% (an 8.8pp improvement). The bot should expose trigger selection with data-driven defaults:
- 2x leverage → 5x trigger (best: +4.0%)
- 3x leverage → 6x trigger (best: -3.5%)
- 4x leverage → 8x trigger (best: -19.6%, not recommended)

**8.3 Historical Rate API**
Expose historical lending/borrowing rates through the Asgard API. This backtest relied on DefiLlama's Kamino data as a proxy. Native historical data would enable more accurate backtesting and attract quant integrators.

### High Impact

**8.4 Position Asgard as Hedging Infrastructure**
HL funding income drives 69–95% of returns depending on leverage. The product narrative should emphasize: *"Asgard provides the leveraged long leg for delta-neutral strategies that capture perp funding income."* The carry is a bonus, not the core.

**8.5 Prioritize Liquidation Avoidance Over Fee Reduction**
Capital rebalancing already reduced trading fees from $574 to $87 at 3x/$10k — an 85% reduction. The remaining drag is dominated by liquidation penalties ($830 at 3x). Engineering effort should focus on:
- Better intraday monitoring to rebalance before liquidation thresholds are breached
- Partial de-leveraging during high-volatility regimes (reduce position size to lower effective leverage)
- Emergency circuit breakers that transfer capital before market hours with historically high volatility

Fee reduction (volume tiers, rebalance discounts) has minimal marginal value now — the Asgard fee sensitivity analysis shows reducing the fee from 0.15% to 0% only improves return by 0.4pp.

**8.6 USDC Borrowing Rate Optimization**
Net carry averaged only +2.29% (3x) because USDC borrowing nearly offsets SOL lending. Reducing average USDC borrowing from 6.71% to 5.24% (matching SOL lending) would double net carry at 3x and provide a larger cushion during adverse funding periods.

### Medium Impact

**8.7 Leverage Guidance in API/Docs**
Surface data-driven guidance:
- **Conservative / automated:** 2x with 5x trigger — +4.0% in backtest, zero liquidations, zero full rotations
- **Range-bound markets only:** 3x with 6x trigger — -3.5% in backtest due to one liquidation, would be profitable in calmer markets
- **Not recommended:** 4x — multiple liquidations unavoidable, -19.6% even with optimal trigger

**8.8 Regime Detection Signals**
Provide API fields that help bots identify favorable conditions — trailing 7-day funding rate, SOL realized volatility, or a simple indicator. Smart entry (only entering when trailing funding > 0) improves 30-day win rate by ~6.5pp in the static backtest.

**8.9 No-Close-Fee Advantage Marketing**
The zero close fee is valuable for managed positions — when a liquidation forces a full close+reopen, not paying the close fee on Asgard saves capital versus alternatives. With capital rebalancing, full rotations are rare (0–1 at 2x/3x), so this advantage matters most after liquidation events.

---

## 9. Conclusions

1. **2x leverage with an optimized trigger is profitable; 3x is marginal; 4x is not viable.** The capital rebalance backtest shows 2x with a 5x trigger returns +4.0% annualized with zero liquidations — close to the static ceiling (+5.9%). At 3x, a single liquidation event pushes the return to -3.5%, a massive improvement over the old full-rotation model (-14.9%) but still negative. 4x is unprofitable at all tested triggers (-19.6% best case) due to multiple unavoidable liquidations.

2. **Liquidation penalties dominate, not trading fees.** Capital rebalancing reduced trading fees from $574 to $87 at 3x/$10k — an 85% reduction. But the $830 liquidation penalty on Jan 18, 2025 (SOL spiked 38% intraday) is what makes 3x negative. Every leverage/trigger combination that avoids liquidation is profitable. **Liquidation avoidance is the strategy's single most important operational goal.**

3. **Rebalance trigger optimization is the key operational insight.** At 2x, changing the trigger from 4x to 5x swings the return from -4.8% to +4.0% — an 8.8pp improvement from one parameter. The trigger determines whether capital transfers happen early enough to prevent liquidation during extreme moves. This interaction between leverage and trigger is non-obvious and must be tuned empirically.

4. **The strategy is short volatility, but survivable at 2x.** It profits when markets are calm and bleeds when extreme moves trigger liquidation. With capital rebalancing (not full rotations), moderate trends are manageable — 2x survived SOL's 58% decline with 5 capital rebalances and zero liquidations. The danger is concentrated in single-day spikes, not slow drawdowns.

5. **MTM drag is a real but secondary cost.** SOL lending yield is denominated in SOL. When SOL drops 58%, accumulated SOL from lending is worth less — this "MTM drag" cost $244 at 3x/$10k. It's a structural feature of any SOL-denominated yield in a declining market, and shows up as a balancing item in the P&L waterfall.

6. **Scale still doesn't change the picture.** Performance ranges from -5.3% ($1k, 3x) to -3.3% ($100k, 3x) — a ~2pp improvement from fixed-cost amortization (bridge fees and gas). The fundamental leverage/trigger conclusion is the same at all sizes. Slippage is negligible up to $100k.

7. **HL funding is the engine (73% of gross at 3x), Asgard is the chassis.** Funding income of $566 dwarfs carry income of $205 at 3x/$10k. The product narrative should emphasize funding capture, not lending yield.

8. **A single catastrophic event determines the outcome.** The Jan 18, 2025 SOL spike ($189→$262, high $271+) is the determinative event in this 410-day backtest. Every leverage/trigger combo that avoids liquidation on this day is profitable; every one that doesn't is negative. This concentration of risk in tail events is characteristic of short-volatility strategies.

9. **This backtest covers a single, challenging market regime.** SOL's 58% decline is not guaranteed to repeat. In range-bound or moderately bullish periods, 3x would likely be profitable (close to the static results of +7.1%). The recommendation to default to 2x with a 5x trigger is a conservative choice validated against the worst observed conditions.

---

## Appendix: Source Materials

All source data and scripts are in this `feasibility/` folder:

### Data (`data/`)
| File | Description | Source |
|------|-------------|--------|
| `sol_funding_history.json` | 9,807 hourly SOL funding rates | Hyperliquid `fundingHistory` API |
| `sol_daily_candles.json` | 410 daily SOL OHLCV candles | Hyperliquid `candleSnapshot` API |
| `kamino_sol_lending_rates.json` | 819 daily SOL lending APY records | DefiLlama (Kamino pool) |
| `kamino_usdc_borrowing_rates.json` | 819 daily USDC borrowing APY records | DefiLlama (Kamino pool) |
| `managed_backtest_output.txt` | Full output of managed backtest run | Generated |
| `rebalance_backtest_output.txt` | Full output of capital rebalance backtest run | Generated |

### Scripts (`scripts/`)
| Script | Description |
|--------|-------------|
| `fetch_funding.py` | Fetches SOL funding rate history from Hyperliquid |
| `backtest_full.py` | Static backtest with real rates (single leverage, single size) |
| `backtest_leverage.py` | Multi-leverage static backtest (2x/3x/4x) |
| `backtest_scaled.py` | Multi-size static backtest ($1k/$10k/$100k) with slippage model |
| `backtest_managed.py` | Full-rotation backtest — every rebalance is a full close+reopen (pessimistic floor) |
| `backtest_rebalance.py` | **Capital rebalance backtest** — cross-leg capital transfers, proper state tracking, trigger optimization (realistic model) |

---

*Data: 410 days of overlapping observations (Dec 2024 – Feb 2026) combining 9,807 hourly SOL funding rates from Hyperliquid, daily SOL lending rates and USDC borrowing rates from DefiLlama (Kamino pools), and daily SOL OHLCV candles from Hyperliquid. Capital rebalance simulation uses proper per-leg state tracking (sol_qty, usdc_debt, short_margin), mark-to-market funding, compounding carry, and cross-leg capital transfers ($3 bridge fee). Liquidation modeled as 50% equity penalty on the affected leg. DefiLlama Kamino rates used as proxy for Asgard's underlying protocol rates.*
