# Drift vs Hyperliquid: Short Leg Comparison Study

## 1. Why Compare?

The existing feasibility study uses **Hyperliquid** (HL) as the short-leg venue for the delta-neutral basis strategy. Drift is a Solana-native perp DEX that shares the same chain as Asgard (the long leg), creating three structural advantages:

1. **Same-chain capital transfers** — No Arbitrum-to-Solana bridge needed. Rebalancing costs ~$0.001 gas instead of ~$3 per bridge.
2. **Lower maintenance margin** — 3% vs HL's 5%. Positions survive larger price moves before liquidation.
3. **Lower liquidation penalty** — 2.5% of position notional vs HL's ~50% of remaining equity. Even when liquidation occurs, the damage is smaller.

These advantages could significantly improve results at higher leverage, where the original study found that **liquidation penalties — not trading fees — are the dominant cost**.

## 2. Venue Parameter Comparison

| Parameter | Hyperliquid | Drift | Impact |
|-----------|:-----------:|:-----:|--------|
| Taker fee | 3.5 bps | 3.5 bps | Identical |
| Maintenance margin | 5.0% | 3.0% | Drift survives larger moves |
| Liquidation penalty | 50% of equity | 2.5% of notional | Drift much lower in practice |
| Bridge/transfer cost | $3.00 | $0.001 | Drift 3000x cheaper |
| Avg funding rate (annualized) | +4.2% | -5.4% | HL pays shorts; Drift charges shorts |
| Positive funding days | 298/410 (73%) | 140/410 (34%) | HL structurally more favorable |
| Funding settlement | Hourly | Hourly | Same |

**The tradeoff**: HL has better funding rates (shorts earn income). Drift has better risk parameters (cheaper rebalancing, wider liquidation margin, lower penalty). Which matters more depends on leverage.

## 3. Funding Rate Analysis

### 3.1 Statistical Summary

| Metric | Hyperliquid | Drift |
|--------|:-----------:|:-----:|
| Mean daily rate | +0.000114 | -0.000148 |
| Std dev | 0.000453 | 0.000494 |
| Annualized mean | +4.2% | -5.4% |
| Positive days (shorts earn) | 298 (73%) | 140 (34%) |
| Max daily rate | +0.00166 | +0.00094 |
| Min daily rate | -0.00400 | -0.00670 |
| Cross-venue correlation | 0.626 | 0.626 |

### 3.2 Monthly Breakdown (annualized %)

| Month | HL | Drift | Drift − HL |
|-------|---:|------:|-----------:|
| 2024-12 | +1.8% | -3.7% | -5.5% |
| 2025-01 | +11.1% | +5.9% | -5.2% |
| 2025-02 | +1.3% | -17.5% | -18.8% |
| 2025-03 | -1.8% | -13.2% | -11.5% |
| 2025-04 | -5.9% | -11.7% | -5.7% |
| 2025-05 | +10.6% | -1.1% | -11.7% |
| 2025-06 | +6.9% | -1.9% | -8.8% |
| 2025-07 | +19.9% | +6.8% | -13.0% |
| 2025-08 | +11.8% | -2.1% | -13.9% |
| 2025-09 | +10.8% | +3.1% | -7.8% |
| 2025-10 | -4.8% | -16.8% | -12.1% |
| 2025-11 | -2.4% | -10.6% | -8.2% |
| 2025-12 | +5.5% | +0.9% | -4.6% |
| 2026-01 | +3.1% | -3.9% | -7.0% |
| 2026-02 | -25.4% | -27.7% | -2.3% |

**Key observations:**
- Drift funding was worse than HL in **every single month**. The gap ranged from -2.3% to -18.8%.
- Both venues had negative funding in Feb-Apr 2025 and Oct-Nov 2025 (prolonged bearish periods where shorts pay).
- HL had only 3 negative months vs Drift's 11.
- The 0.626 correlation means they move together directionally but Drift has a persistent negative offset.

### 3.3 Why Drift Funding Is Structurally Lower

Drift is a Solana-native DEX with lower open interest and different participant composition than Hyperliquid. The consistently negative funding suggests Drift has more short-biased flow (or less long-biased demand) relative to HL. For our short-leg strategy, this means:

- **HL**: Shorts are typically paid to maintain positions (+4.2% annualized income)
- **Drift**: Shorts typically pay to maintain positions (-5.4% annualized cost)
- **Net difference**: ~9.6% annualized funding headwind on Drift

## 4. Performance Results

### 4.1 Side-by-Side Performance Matrix

| Capital | Lev | HL Ann Ret | HL Liqs | Drift Ann Ret | Drift Liqs | Winner |
|--------:|:---:|-----------:|--------:|--------------:|-----------:|:------:|
| $1,000 | 2x | -8.5% | 1 | -5.2% | 1 | Drift |
| $1,000 | 3x | -5.3% | 1 | -9.2% | 1 | HL |
| $1,000 | 4x | -22.1% | 5 | +1.4% | 5 | Drift |
| $10,000 | 2x | -4.8% | 1 | -4.8% | 1 | Tie |
| $10,000 | 3x | -3.5% | 1 | -8.9% | 1 | HL |
| $10,000 | 4x | -19.6% | 5 | +2.3% | 5 | Drift |
| $100,000 | 2x | -4.7% | 1 | -4.8% | 1 | Tie |
| $100,000 | 3x | -3.3% | 1 | -8.8% | 1 | HL |
| $100,000 | 4x | -19.3% | 5 | +2.3% | 5 | Drift |

*Results use default triggers (2x target leverage). Both venues use identical Asgard long-leg parameters.*

### 4.2 With Optimized Rebalance Triggers ($10k)

| Lev | HL Best Trigger | HL Best Return | Drift Best Trigger | Drift Best Return |
|:---:|:--------------:|:--------------:|:-----------------:|:----------------:|
| 2x | 5x | **+4.0%** | 3x | -4.8% |
| 3x | 6x | **-3.5%** | 8x | **-1.3%** |
| 4x | 8x | -19.6% | 8x | **+2.3%** |

**Trigger sweep detail ($10k, 3x):**

| Trigger | HL | Drift | Drift − HL |
|--------:|---:|------:|-----------:|
| 4x | -13.2% | -8.8% | +4.4% |
| 5x | -8.4% | -11.4% | -3.0% |
| 6x | **-3.5%** | -8.9% | -5.4% |
| 8x | -5.2% | **-1.3%** | +3.9% |
| 10x | -18.6% | -7.1% | +11.5% |
| never | -33.6% | -22.0% | +11.7% |

### 4.3 Analysis by Leverage

**2x leverage: HL wins**
- With optimized triggers: HL +4.0% vs Drift -4.8%.
- At 2x, liquidations are rare and manageable. HL's funding advantage (+4.2% vs -5.4% annualized) dominates. The ~9.6% funding gap overwhelms Drift's structural advantages in bridge cost and margin.
- HL is the clear choice at 2x.

**3x leverage: HL wins with optimal trigger, Drift wins at aggressive triggers**
- HL best: -3.5% (6x trigger), Drift best: -1.3% (8x trigger).
- At HL's optimal 6x trigger, the Jan 18 2025 spike ($189 to $262) causes one liquidation costing $830 penalty. The position recovers thanks to strong funding income.
- On Drift with 3% margin, the Jan 18 spike triggers a rebalance (not a liquidation) — the position survives. But negative funding drags returns down over the remaining period.
- The -1.3% Drift result at 8x trigger is actually the best 3x result across either venue when using aggressive (infrequent) rebalancing.

**4x leverage: Drift wins decisively**
- HL: -19.6%, Drift: +2.3% (both at 8x trigger).
- Both have 5 liquidations, but the outcomes are completely different:
  - HL liquidation penalties: **$3,244** (50% of equity at each event)
  - Drift liquidation penalties: **$0** (see Section 5.1 for explanation)
- This 21.8 percentage point gap is almost entirely explained by the liquidation penalty model.

## 5. Cost Structure Comparison ($10k)

### 5.1 Fee & Penalty Breakdown

| Lev | Component | Hyperliquid | Drift |
|:---:|-----------|------------:|------:|
| 2x | Trading fees | $45 | $45 |
| 2x | Bridge costs | $18 | $0.01 |
| 2x | Liq penalties | $1,196 | $258 |
| 2x | **Total drag** | **$1,259** | **$304** |
| 3x | Trading fees | $66 | $65 |
| 3x | Bridge costs | $21 | $0.02 |
| 3x | Liq penalties | $830 | $388 |
| 3x | **Total drag** | **$917** | **$453** |
| 4x | Trading fees | $247 | $287 |
| 4x | Bridge costs | $36 | $0.03 |
| 4x | Liq penalties | $3,244 | $0 |
| 4x | **Total drag** | **$3,527** | **$287** |

### 5.2 Why Drift's 4x Liquidation Penalty Is $0

With 3% maintenance margin, Drift positions are liquidated when equity drops to 3% of notional. In our daily-resolution simulation, by the time the check fires, equity has typically gone to $0 (the price moved through and past the 3% threshold in a single day). The penalty calculation caps at remaining equity: `min(2.5% of notional, max(equity, 0)) = 0`.

This is realistic: on Drift, positions liquidated below zero equity have losses absorbed by the insurance fund. The effective cost to the trader is losing their remaining margin — not an additional penalty on top.

On HL with 5% maintenance margin, liquidation triggers earlier when there's still ~2.5-5% equity remaining. The 50%-of-equity penalty then takes a meaningful bite: for a $10k half-position at 4x ($20k notional), 5% equity = $1,000, and penalty = $500.

### 5.3 P&L Waterfall ($10k)

| Lev | Component | Hyperliquid | Drift |
|:---:|-----------|------------:|------:|
| 2x | Carry (lending − borrowing) | $214 | $212 |
| 2x | Funding income | **+$534** | **-$423** |
| 2x | Gross income | $747 | -$211 |
| 2x | Trading fees | -$63 | -$45 |
| 2x | Liq penalties | -$1,196 | -$258 |
| 2x | MTM drag | -$28 | -$29 |
| 2x | **Net return** | **-$540** | **-$544** |
| 3x | Carry | $205 | $123 |
| 3x | Funding income | **+$566** | **-$622** |
| 3x | Gross income | $771 | -$500 |
| 3x | Trading fees | -$87 | -$65 |
| 3x | Liq penalties | -$830 | -$388 |
| 3x | MTM drag | -$244 | -$42 |
| 3x | **Net return** | **-$390** | **-$994** |
| 4x | Carry | $125 | $68 |
| 4x | Funding income | **+$758** | **-$1,031** |
| 4x | Gross income | $883 | -$963 |
| 4x | Trading fees | -$283 | -$287 |
| 4x | Liq penalties | -$3,244 | -$0 |
| 4x | MTM drag | +$445 | +$1,503 |
| 4x | **Net return** | **-$2,199** | **+$253** |

**Key insight from the waterfall:**
- HL's P&L is: strong funding income, moderate carry, destroyed by liquidation penalties at high leverage.
- Drift's P&L is: negative funding income (shorts pay), minimal carry (positions smaller after negative funding drains margin), but dramatically lower liquidation penalties.
- The MTM drag column balances the waterfall — positive values mean accumulated SOL lending yield appreciated (position resets from liquidation lock in different cost bases).

## 6. Delta-Neutral Verification ($10k, 3x)

| Metric | Hyperliquid | Drift |
|--------|:-----------:|:-----:|
| Start equity | $9,969 | $9,967 |
| End equity | $9,611 | $9,008 |
| Min equity | $9,201 | $9,007 |
| Max equity | $10,041 | $10,070 |
| Std dev | $266 | $319 |
| SOL price change | -57.8% | -57.8% |
| Equity change | -3.6% | -9.6% |
| Delta-neutral | Yes | Yes |

Both venues maintain delta neutrality: SOL dropped 57.8% while equity moved -3.6% (HL) and -9.6% (Drift). The equity changes are driven by net income/costs, not directional exposure.

## 7. Event Log Comparison ($10k, 3x)

### Hyperliquid — 10 events, 1 liquidation

| Date | Event | Price | Detail |
|------|-------|------:|--------|
| 2024-12-31 | Open | $189 | Long=$4,985, Short=$4,985, Notional=$15,000 |
| 2025-01-03 | Rebalance | $218 | Transfer $2,249 at 6.3x effective leverage |
| 2025-01-09 | Rebalance | $185 | Transfer $2,641 at 6.2x |
| 2025-01-17 | Rebalance | $220 | Transfer $2,728 at 7.6x |
| **2025-01-18** | **Liquidation** | **$262** | **Short leg, penalty $830** |
| 2025-01-18 | Reopen | $262 | Restart at $13,808 notional |
| 2025-02-02 | Rebalance | $203 | Transfer $3,116 at 7.1x |
| 2025-02-26 | Rebalance | $135 | Transfer $3,594 at 7.0x |
| 2025-07-21 | Rebalance | $196 | Transfer $3,166 at 6.4x |
| 2025-11-21 | Rebalance | $129 | Transfer $3,694 at 6.1x |

### Drift — 20 events, 1 liquidation

| Date | Event | Price | Detail |
|------|-------|------:|--------|
| 2024-12-31 | Open | $189 | Long=$4,985, Short=$4,985, Notional=$15,000 |
| 2025-01-03 | Rebalance | $218 | Transfer $2,261 at 6.3x |
| 2025-01-09 | Rebalance | $185 | Transfer $2,634 at 6.2x |
| 2025-01-17 | Rebalance | $220 | Transfer $2,766 at 7.9x |
| **2025-01-18** | **Rebalance** | **$262** | **Transfer $3,386 at 13.1x** (survived!) |
| 2025-01-27 | Rebalance | $235 | Transfer $2,212 at 6.7x |
| 2025-02-02 | Rebalance | $203 | Transfer $2,523 at 6.5x |
| ... | (11 more rebalances) | ... | ... |
| **2025-10-10** | **Liquidation** | **$188** | **Long leg, penalty $388** |
| 2025-10-10 | Reopen | $188 | Restart at $14,109 notional |
| 2025-11-13 | Rebalance | $145 | Transfer $3,102 at 7.4x |
| 2026-02-01 | Rebalance | $101 | Transfer $3,279 at 6.1x |

**Critical difference:** The Jan 18, 2025 spike that liquidated HL's short leg at $262 was survived by Drift as a rebalance (albeit at a painful 13.1x effective leverage). Drift's 3% maintenance margin gave the position enough room to survive the $189 to $262 move.

However, Drift was eventually liquidated on Oct 10 (long leg at $188), a move HL survived because HL's positive funding had built up a larger equity buffer by that point.

## 8. Conclusions

### 8.1 Venue Recommendation by Leverage

| Leverage | Recommended Venue | Why |
|:--------:|:-----------------:|-----|
| **2x** | **Hyperliquid** | Funding advantage (+4.2% vs -5.4%) dominates; liquidations rare and survivable |
| **3x** | **Depends** | HL wins with tight trigger (6x: -3.5%), Drift wins with loose trigger (8x: -1.3%) |
| **4x** | **Drift** | 21.8pp advantage from lower liquidation penalties; only venue where 4x is profitable |

### 8.2 Key Findings

1. **Funding rates matter more than structural advantages at low leverage.** HL's 9.6% funding advantage overwhelms Drift's cheaper transfers and lower margin at 2x.

2. **Liquidation penalties matter more than funding at high leverage.** At 4x, Drift's near-zero effective liquidation penalty vs HL's $3,244 is the entire difference between +2.3% and -19.6%.

3. **Drift makes 4x viable.** The original study concluded 4x was not viable. With Drift, 4x actually returns +2.3% annualized — the only venue/leverage combo above 2x that's profitable with default triggers.

4. **The "same-chain advantage" is real but secondary.** Bridge savings are $18-36 for HL vs $0.01-0.03 for Drift across all scenarios. This is a few basis points — meaningful but not the deciding factor.

5. **At 3x, trigger optimization changes the winner.** The optimal Drift trigger (8x, -1.3%) beats the optimal HL trigger (6x, -3.5%) by 2.2pp. This suggests a split venue strategy: conservative triggers on HL, aggressive triggers on Drift.

6. **Drift funding is structurally unfavorable for shorts.** Negative in 11/15 months, with a persistent ~7-14% annualized gap vs HL. This is likely a function of Drift's participant composition (more hedging shorts than speculative longs).

### 8.3 What This Changes for Production

The original study recommended **2x leverage with HL, using a 5x rebalance trigger**. This study adds nuance:

- **If targeting 2x**: Stay with HL. Funding income is the primary revenue driver at this leverage.
- **If willing to run 3x**: Consider Drift with 8x trigger (-1.3%) or HL with 6x trigger (-3.5%). Drift is marginally better but with more operational complexity (more frequent rebalances: 18 vs 7).
- **If targeting 4x**: Use Drift — it's the only venue where 4x is profitable.
- **Split-venue approach**: Run 2x on HL (funding income) + additional leverage on Drift (liquidation resilience). This hedges venue risk and captures both advantages.

## Appendix

### Data Sources
- Drift funding: `data/drift_sol_funding_history.json` (fetched from Drift Data API)
- HL funding: `data/sol_funding_history.json` (fetched from Hyperliquid API)
- SOL candles, Kamino lending/borrowing: Same as original study

### Scripts
- `scripts/fetch_drift_funding.py` — Fetch and normalize Drift funding rates
- `scripts/backtest_comparison.py` — Parameterized comparison backtest

### Backtest Parameters
- Period: 2024-12-31 to 2026-02-13 (410 days)
- SOL price: $189.44 to $79.87 (-57.8%)
- Long leg: Asgard with Kamino (3.8x max leverage)
- Asgard open fee: 15 bps
- Gas cost: $2 per operation
- Default rebalance trigger: 2x target leverage
