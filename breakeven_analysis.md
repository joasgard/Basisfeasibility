# Breakeven Analysis: Delta-Neutral Basis Strategy

## Core Formula

```
Net APY = L/2 × (R_lend + R_fund − R_borr) − fee_drag(L, C) − bridge_drag(C)
          ───────────────────────────────     ──────────────     ─────────────
          "spread" amplified by leverage       scales as ~L/C    flat $ / capital
```

Where:
- **L** = leverage, **C** = total capital
- **R_lend** = SOL lending APY on Kamino
- **R_fund** = perp funding rate (annualized) — positive = shorts earn
- **R_borr** = USDC borrowing APY on Kamino
- **fee_drag** = round-trip trading fees annualized
- **bridge_drag** = rebalance transfer costs annualized

The `/2` factor exists because only half the capital is deployed on each leg.

---

## 1. Observed Annualized Rates (Dec 2024 – Feb 2026)

| Component | Rate |
|-----------|-----:|
| SOL lending APY (Kamino) | +5.24% |
| USDC borrowing APY (Kamino) | +6.71% |
| Carry spread (lend − borr) | -1.47% |
| HL funding rate | +4.16% |
| Drift funding rate | -5.41% |

**Net spread = R_lend + R_fund − R_borr:**

| Venue | Calculation | Spread |
|-------|-------------|-------:|
| Hyperliquid | +5.24% + 4.16% − 6.71% | **+2.69%** |
| Drift | +5.24% + (−5.41%) − 6.71% | **−6.88%** |

HL's spread is positive — leverage amplifies income. Drift's spread is negative — leverage amplifies losses.

---

## 2. Theoretical Gross APY (before fees and liquidations)

Formula: `Gross APY = L/2 × spread`

| Leverage | Hyperliquid | Drift |
|---------:|------------:|------:|
| 2.0x | +2.69% | −6.88% |
| 3.0x | +4.03% | −10.32% |
| 3.8x | +5.11% | −13.07% |
| 4.0x | +5.38% | −13.76% |

---

## 3. Round-Trip Fee Drag (open + close + gas)

Trading fees are identical between venues (both 3.5 bps taker). The only difference is the $2 gas cost, which matters at small capital.

| Capital | Lev | Fee % of Capital | Fee $ |
|--------:|:---:|-----------------:|------:|
| $1,000 | 2x | 0.420% | $4.20 |
| $1,000 | 3x | 0.530% | $5.30 |
| $1,000 | 4x | 0.640% | $6.40 |
| $10,000 | 2x | 0.240% | $24.00 |
| $10,000 | 3x | 0.350% | $35.00 |
| $10,000 | 4x | 0.460% | $46.00 |
| $100,000 | 2x | 0.222% | $222.00 |
| $100,000 | 3x | 0.332% | $332.00 |
| $100,000 | 4x | 0.442% | $442.00 |

Fee drag scales as ~L/C: higher leverage and smaller capital = more drag. But at $10k+, fees are a rounding error compared to the spread.

---

## 4. Breakeven Spread

The minimum net spread (R_lend + R_fund − R_borr) needed to cover all fees, assuming 1 round-trip/year and ~6 rebalances/year.

| Capital | Lev | HL Breakeven | Drift Breakeven | HL Actual | Drift Actual | HL Margin | Drift Margin |
|--------:|:---:|------------:|----------------:|----------:|-------------:|----------:|-------------:|
| $1,000 | 2x | +2.22% | +0.42% | +2.69% | −6.88% | +0.47% | **−7.30%** |
| $1,000 | 3x | +1.55% | +0.35% | +2.69% | −6.88% | +1.14% | **−7.23%** |
| $1,000 | 4x | +1.22% | +0.32% | +2.69% | −6.88% | +1.47% | **−7.20%** |
| $10,000 | 2x | +0.42% | +0.24% | +2.69% | −6.88% | +2.27% | **−7.12%** |
| $10,000 | 3x | +0.35% | +0.23% | +2.69% | −6.88% | +2.34% | **−7.11%** |
| $10,000 | 4x | +0.32% | +0.23% | +2.69% | −6.88% | +2.37% | **−7.11%** |
| $100,000 | 2x | +0.24% | +0.22% | +2.69% | −6.88% | +2.45% | **−7.10%** |
| $100,000 | 3x | +0.23% | +0.22% | +2.69% | −6.88% | +2.46% | **−7.10%** |
| $100,000 | 4x | +0.23% | +0.22% | +2.69% | −6.88% | +2.46% | **−7.10%** |

HL clears breakeven at every combination. Drift fails everywhere — its negative spread means no amount of capital or leverage can make the fees work, because the spread itself is the problem.

Note that HL's breakeven is higher at small capital due to the $3 bridge cost per rebalance (1.80%/yr at $1k vs 0.02%/yr at $100k). Drift's near-zero bridge cost makes this irrelevant.

---

## 5. Breakeven Funding Rate

Given observed lending and borrowing rates, what minimum funding rate would each venue need?

Formula: `R_fund_min = breakeven_spread − R_lend + R_borr`

| Capital | Lev | HL Required | Drift Required | HL Actual | Drift Actual | HL | Drift |
|--------:|:---:|------------:|---------------:|----------:|-------------:|:--:|:-----:|
| $1,000 | 2x | +3.69% | +1.89% | +4.16% | −5.41% | Pass | Fail |
| $1,000 | 3x | +3.03% | +1.83% | +4.16% | −5.41% | Pass | Fail |
| $1,000 | 4x | +2.69% | +1.79% | +4.16% | −5.41% | Pass | Fail |
| $10,000 | 2x | +1.89% | +1.71% | +4.16% | −5.41% | Pass | Fail |
| $10,000 | 3x | +1.83% | +1.71% | +4.16% | −5.41% | Pass | Fail |
| $10,000 | 4x | +1.79% | +1.70% | +4.16% | −5.41% | Pass | Fail |
| $100,000 | 2x | +1.71% | +1.69% | +4.16% | −5.41% | Pass | Fail |
| $100,000 | 3x | +1.71% | +1.69% | +4.16% | −5.41% | Pass | Fail |
| $100,000 | 4x | +1.70% | +1.69% | +4.16% | −5.41% | Pass | Fail |

Drift would need funding to swing from −5.41% to at least +1.70% — a 7.1 percentage point shift — just to break even. HL has 2.3–2.5% of headroom above breakeven at $10k+.

---

## 6. Liquidation Distance

Maximum SOL price move before either leg gets liquidated. The **long leg** (SOL drops) is always the binding constraint.

| Leverage | Venue | Short Liq (SOL rises) | Long Liq (SOL drops) | Binding | Price Range @ $189 |
|---------:|-------|----------------------:|---------------------:|--------:|-------------------:|
| 2.0x | Hyperliquid | +90.5% | −45.0% | Long | $104 – $361 |
| 2.0x | Drift | +94.2% | −47.0% | Long | $100 – $368 |
| 3.0x | Hyperliquid | +46.3% | −28.3% | Long | $136 – $277 |
| 3.0x | Drift | +47.8% | −30.3% | Long | $132 – $280 |
| 3.8x | Hyperliquid | +33.3% | −21.3% | Long | $149 – $253 |
| 3.8x | Drift | +34.3% | −23.3% | Long | $145 – $254 |
| 4.0x | Hyperliquid | +31.1% | −20.0% | Long | $152 – $248 |
| 4.0x | Drift | +32.0% | −22.0% | Long | $148 – $250 |

Drift's 3% maintenance margin (vs HL's 5%) gives ~2pp more room at every leverage level. SOL moved −58% during the backtest period — well beyond the liquidation distance for anything above 2x.

---

## 7. Full Breakeven Summary ($10k)

| Lev | Venue | Gross APY | Fee Drag | Bridge Drag | Net APY | Liq Distance | Breakeven Funding | Actual Funding |
|:---:|-------|----------:|---------:|------------:|--------:|-------------:|------------------:|---------------:|
| 2.0x | Hyperliquid | +2.69% | 0.24% | 0.180% | **+2.27%** | 45.0% | +1.89% | +4.16% |
| 2.0x | Drift | −6.88% | 0.24% | 0.000% | **−7.12%** | 47.0% | +1.71% | −5.41% |
| 3.0x | Hyperliquid | +4.03% | 0.35% | 0.180% | **+3.50%** | 28.3% | +1.83% | +4.16% |
| 3.0x | Drift | −10.32% | 0.35% | 0.000% | **−10.67%** | 30.3% | +1.71% | −5.41% |
| 3.8x | Hyperliquid | +5.11% | 0.44% | 0.180% | **+4.49%** | 21.3% | +1.80% | +4.16% |
| 3.8x | Drift | −13.07% | 0.44% | 0.000% | **−13.51%** | 23.3% | +1.70% | −5.41% |
| 4.0x | Hyperliquid | +5.38% | 0.46% | 0.180% | **+4.74%** | 20.0% | +1.79% | +4.16% |
| 4.0x | Drift | −13.76% | 0.46% | 0.000% | **−14.22%** | 22.0% | +1.70% | −5.41% |

---

## 8. Effect of Holding Period on Breakeven ($10k)

Shorter holding periods amortize open/close fees over fewer days, increasing drag.

| Hold Period | Lev | HL Net APY | Drift Net APY | HL Breakeven Spread | Drift Breakeven Spread |
|:-----------:|:---:|----------:|--------------:|--------------------:|-----------------------:|
| 90 days | 2x | +1.54% | −7.85% | +1.153% | +0.973% |
| 90 days | 3x | +2.43% | −11.74% | +1.066% | +0.946% |
| 90 days | 4x | +3.33% | −15.62% | +1.023% | +0.933% |
| 180 days | 2x | +2.02% | −7.37% | +0.667% | +0.487% |
| 180 days | 3x | +3.14% | −11.03% | +0.593% | +0.473% |
| 180 days | 4x | +4.27% | −14.69% | +0.556% | +0.466% |
| 365 days | 2x | +2.27% | −7.12% | +0.420% | +0.240% |
| 365 days | 3x | +3.50% | −10.67% | +0.353% | +0.233% |
| 365 days | 4x | +4.74% | −14.22% | +0.320% | +0.230% |
| 2 years | 2x | +2.39% | −7.00% | +0.300% | +0.120% |
| 2 years | 3x | +3.68% | −10.49% | +0.237% | +0.117% |
| 2 years | 4x | +4.97% | −13.99% | +0.205% | +0.115% |

Even at 90-day holding periods, HL remains profitable at all leverage levels. Drift remains unprofitable regardless of holding period.

---

## 9. Key Takeaways

### What leverage amplifies

The spread (R_lend + R_fund − R_borr) is multiplied by L/2:

| Venue | Spread | 2x Gross | 3x Gross | 4x Gross |
|-------|-------:|---------:|---------:|---------:|
| Hyperliquid | +2.69% | +2.69% | +4.03% | +5.38% |
| Drift | −6.88% | −6.88% | −10.32% | −13.76% |

**If the spread is positive, crank leverage. If negative, no leverage helps.**

### What fees cost

- Fee drag scales as ~L/C — higher leverage and smaller capital both hurt
- At $1k/2x: 0.42% drag. At $100k/2x: 0.22% drag
- Bridge cost only matters at small capital: $1k = 1.80%/yr (HL), $100k = 0.02%/yr
- Drift's same-chain advantage eliminates bridge drag entirely, but this saves ~0.18%/yr at $10k — irrelevant vs the 9.6% funding gap

### The breakeven paradox

This analysis shows HL is theoretically profitable at **every** leverage level — higher leverage = higher returns. But the backtest tells a different story:

| Leverage | Breakeven Net APY | Backtest Result | Gap |
|---------:|------------------:|----------------:|----:|
| 2x | +2.27% | +4.0% (optimized) | HL wins in practice too |
| 3x | +3.50% | −3.5% | **Liquidation wiped 7pp** |
| 4x | +4.74% | −19.6% | **Liquidation wiped 24pp** |

The breakeven formula assumes no liquidations. At 3x+, a single large SOL move triggers liquidation, and the penalty ($830 at 3x, $3,244 at 4x on HL) destroys the theoretical edge. Drift's lower liquidation penalty is why its backtest result at 4x (+2.3%) beats HL despite a far worse theoretical breakeven.

**The breakeven formula is necessary but not sufficient. Liquidation risk is the binding constraint at 3x+.**
