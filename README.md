# Feasibility Study: Delta-Neutral Basis Trading

Economic analysis of a SOL/USDC delta-neutral strategy using Asgard (leveraged lending) + short perp (Hyperliquid or Drift).

## Key Finding

**2x leverage with an optimized rebalance trigger returns +4.0% annualized** with zero liquidations. 3x is marginal (-3.5%), 4x is not viable (-19.6%). Liquidation penalties — not trading fees — are the dominant cost.

Capital rebalancing (transferring funds between legs at $3/bridge) reduced trading fees by 85% vs. the old full-rotation model. But the real insight is that **every leverage/trigger combo that avoids liquidation is profitable**.

### Three-Model Comparison ($10k)

| Model | 2x | 3x | 4x |
|-------|---:|---:|---:|
| Static (ceiling) | +5.9% | +7.1% | +8.3% |
| **Capital rebalance (realistic)** | **+4.0%** | **-3.5%** | **-19.6%** |
| Full-rotation (floor) | +4.8% | -14.9% | -14.1% |

### Optimal Trigger Per Leverage ($10k)

| Leverage | Best Trigger | Ann Return | Liquidations |
|---------|:-----------:|----------:|------------:|
| 2x | 5x | **+4.0%** | 0 |
| 3x | 6x | -3.5% | 1 |
| 4x | 8x | -19.6% | 5 |

### Drift vs Hyperliquid Comparison

Drift (Solana-native perp DEX) offers same-chain transfers ($0.001 vs $3), lower maintenance margin (3% vs 5%), and lower liquidation penalties (2.5% notional vs 50% equity) — but structurally worse funding rates (-5.4% vs +4.2% annualized).

| Leverage | HL (optimized) | Drift (optimized) | Winner |
|:--------:|:--------------:|:-----------------:|:------:|
| 2x | **+4.0%** | -4.8% | HL (funding) |
| 3x | -3.5% | **-1.3%** | Drift (liq resilience) |
| 4x | -19.6% | **+2.3%** | Drift (liq penalty) |

See **[`drift_comparison.md`](drift_comparison.md)** for full analysis.

## Files

- **`study.md`** — Full analysis with methodology, results, and recommendations
- **`drift_comparison.md`** — Drift vs Hyperliquid short-leg comparison study
- **`TRACKER.md`** — Fix & update tracker documenting all changes
- **`data/`** — Raw data (funding rates, lending rates, price candles) + backtest output
- **`scripts/`** — Backtest scripts (static, multi-leverage, multi-size, full-rotation, capital rebalance, venue comparison)

## Reproducing Results

```bash
# Capital rebalance backtest (primary finding)
python3 scripts/backtest_rebalance.py

# Drift vs Hyperliquid comparison
python3 scripts/fetch_drift_funding.py    # fetch Drift funding data (one-time)
python3 scripts/backtest_comparison.py     # run side-by-side comparison

# Full-rotation backtest (pessimistic floor)
python3 scripts/backtest_managed.py

# Static backtests (theoretical ceiling)
python3 scripts/backtest_full.py
python3 scripts/backtest_leverage.py
python3 scripts/backtest_scaled.py
```

Scripts read local data files from `data/`. Output is saved to `data/rebalance_backtest_output.txt` and `data/comparison_backtest_output.txt`.
