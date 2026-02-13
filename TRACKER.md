# Feasibility Study — Fix & Update Tracker

**Goal:** Fix the broken `backtest_rebalance.py` script, then update `study.md` with corrected results.

**Core insight driving the fix:** The old managed backtest (`backtest_managed.py`) modeled every rebalance as a full close+reopen ($5.30/rotation at 3x/$1k). In a delta-neutral strategy, price moves create *opposing* equity changes on the two legs — you can transfer capital between legs (~$3 bridge fee) instead of closing everything. This should dramatically reduce fee drag, especially at 3x+.

---

## Phase 1: Fix `backtest_rebalance.py`

### 1.1 Rewrite simulation engine with correct state tracking
- [x] **Status:** done
- **File:** `scripts/backtest_rebalance.py`
- **What was broken:**
  - Long equity recalculated from scratch each day, discarding all accumulated carry income
  - Short equity had two conflicting calculations, second overwrote first
  - No compounding of carry into position state
  - Funding calculated on entry notional, not mark-to-market
  - Capital rebalancing set equity directly instead of adjusting underlying state (debt/margin)
- **Fix applied — proper state variables:**
  - Long leg: `sol_qty` (grows with lending yield), `usdc_debt` (grows with borrow interest)
  - Short leg: `short_contracts` (fixed), `short_entry` (fixed), `short_margin` (grows with funding)
  - Equity always derived: `long_eq = sol_qty × price - usdc_debt`, `short_eq = short_margin + contracts × (entry - price)`
  - Capital rebalance adjusts `usdc_debt` and `short_margin` — positions stay same size
  - Full close+reopen after liquidation resets all state vars at current price
- **Notes:**
  - Also added `total_liq_penalty` tracking and P&L waterfall (carry + funding - fees - liq penalty - MTM drag = return)
  - MTM drag = revaluation loss when SOL earned from lending at high prices is later worth less
  - `asgard_fee_bps` is now a function parameter instead of monkey-patched global

### 1.2 Include HL close fee at simulation end
- [x] **Status:** done
- **What:** Added `hl_close_fee = short_contracts * final_price * HL_FEE_BPS / 10000` at end of simulation, included in `total_fees`.
- **Notes:** Matches static backtest's fee accounting.

### 1.3 Preserve output tables and add comparison to static model
- [x] **Status:** done
- **What:** 7-section output: (1) Performance matrix, (2) Optimal trigger per leverage, (3) Fee breakdown, (4a/4b) Event logs for 2x and 3x, (5) Asgard fee sensitivity, (6) P&L waterfall, (7) Delta-neutral sanity check.
- **Notes:** Added per-leverage trigger optimization (Section 2) — the most actionable new output.

### 1.4 Run script and validate results
- [x] **Status:** done
- **Validation results:**
  - ✅ Delta-neutral confirmed: SOL moved -57.8%, equity moved -3.6% at 3x/$10k
  - ✅ 2x with optimal trigger (5x): **+4.0%** annualized, 0 liquidations — close to static ceiling (+5.9%)
  - ✅ 3x with default trigger (6x): **-3.5%** — vastly better than old model (-14.9%)
  - ✅ Capital rebalances are modest: 5-7 at 2x/3x, up to 12 at 4x
  - ✅ Full rotations rare: 0-1 at 2x/3x with good triggers
  - ✅ P&L waterfall adds up: gross - fees - liq_penalty - mtm_drag = return
  - ✅ Output saved to `data/rebalance_backtest_output.txt`
- **Key findings (input for Phase 2):**
  - **Liquidation penalties dominate losses, not fees.** At 3x/$10k: $830 liq penalty vs $87 trading fees.
  - **Trigger choice matters more than leverage choice.** 2x with 4x trigger: -4.8%. 2x with 5x trigger: +4.0%. An 8.8pp swing from trigger alone.
  - **The Jan 18 2025 spike ($189→$262, high $271+) is the single catastrophic event.** Every leverage/trigger combo that avoids liquidation on this day is profitable.
  - **MTM drag is real but secondary.** SOL lending yield is denominated in SOL. When SOL drops 58%, accumulated SOL from lending is worth less. ~$244 drag at 3x/$10k.
  - **3x is NOT viable at default trigger** — it still hits the Jan 18 liquidation. But the -3.5% result is dramatically better than the old -14.9%.
  - **4x is unprofitable at all triggers** (-19.6% best case). Multiple liquidations unavoidable.

---

## Phase 2: Update `study.md`

### 2.1 Update Section 1 (Executive Summary)
- [x] **Status:** done
- **What:** Replace the "Realistic Performance" table and narrative. Old summary said 3x loses -14.9% and recommends 2x only. New narrative:
  - 2x with optimized trigger: +4.0% (profitable, zero liquidations)
  - 3x: -3.5% (much better than old -14.9%, but still negative due to Jan 18 event)
  - Liquidation penalties dominate, not fees
  - Rebalance trigger is the key operational parameter
- **Notes:**
  - (none yet)

### 2.2 Update Section 3.4 (Simulation Models)
- [x] **Status:** done
- **What:** Add "Capital rebalance backtest" model description. Explain the three-model progression: static (ceiling) → capital rebalance (realistic) → full-rotation (pessimistic floor). Describe the state tracking model (sol_qty, usdc_debt, short_margin).
- **Notes:**
  - (none yet)

### 2.3 Rewrite Section 5 (Realistic Performance)
- [x] **Status:** done
- **What:** Replace all tables and narrative with rebalance model results:
  - 5.1: Capital rebalance events (transfers vs full rotations)
  - 5.2: Performance summary with new numbers across leverage/capital combos
  - 5.3: Three-model comparison (static / rebalance / full-rotation)
  - 5.4: Event timeline for $10k/2x and $10k/3x
  - 5.5: Rebalance trigger optimization per leverage (the key new section)
  - 5.6: P&L waterfall decomposition
  - Trim or remove old 5.6 APY-based exits (still valid but less relevant now)
- **Notes:**
  - (none yet)

### 2.4 Update Section 6 (Fee Analysis)
- [x] **Status:** done
- **What:** Rewrote 6.2 (cost comparison table with capital rebalance vs full rotation), 6.3 (Asgard fee sensitivity — 0.4pp impact from 0.15% to 0%), 6.4 (scaling with rebalance model numbers). Kept 6.1, 6.5, 6.6 as-is.
- **Notes:**
  - Key insight in 6.2: capital rebalance model has lower *trading* fees ($87 vs $574) but shows liquidation penalty ($830) — total drag is comparable but the rebalance model is 11.4pp better in net return

### 2.5 Update Section 9 (Conclusions)
- [x] **Status:** done
- **What:** Rewrote all 9 conclusions. Also updated:
  - Section 7.1 (Risk Factors): Updated from "rotations" language to capital rebalancing, corrected expected rebalance counts
  - Section 8 (Recommendations): Rewrote 8.1 (2x/5x trigger), 8.2 (trigger optimization replaces regime-aware buffer), 8.5 (liquidation avoidance over fee reduction), 8.7 (data-driven leverage guidance), 8.9 (no-close-fee reframed)
  - Appendix: Added `backtest_rebalance.py` and `rebalance_backtest_output.txt`
  - Footnote: Updated methodology description
- **Notes:**
  - Section 8 wasn't in the original tracker but had stale numbers ("18 rotations", "+4.8%", etc.) that needed correction

### 2.6 Update README.md
- [x] **Status:** done
- **What:** Complete rewrite: three-model comparison table, optimal trigger table, updated file list (added TRACKER.md), updated script commands (backtest_rebalance.py is primary), corrected data path description.
- **Notes:**
  - (none)

---

## Phase 3: Drift vs Hyperliquid Comparison Study

### 3.1 Fetch Drift funding rate history
- [x] **Status:** done
- **File:** `scripts/fetch_drift_funding.py`
- **What:** Fetch hourly Drift SOL-PERP funding rates from Drift Data API, normalize to HL-compatible format (rate as fraction of notional), aggregate to daily sums.
- **Notes:**
  - API endpoint: `GET https://data.api.drift.trade/market/SOL-PERP/fundingRates/{year}/{month}/{day}`
  - Normalization: divide each hourly `fundingRate` by `oraclePriceTwap` to match HL format
  - 410 days fetched, 0 missing. Drift avg funding: -5.4% annualized (shorts pay), vs HL +4.2% (shorts earn).

### 3.2 Create parameterized comparison backtest
- [x] **Status:** done
- **File:** `scripts/backtest_comparison.py`
- **What:** Extracted simulation logic into parameterized function accepting `VenueConfig` dict (fee_bps, maintenance_margin, liq_penalty_model, bridge_cost, funding_data). Runs both HL and Drift across all leverage/capital/trigger combos.
- **Output sections:** (0) Venue parameters, (1) Performance matrix, (2) Trigger optimization, (3) Fee/cost comparison, (4) P&L waterfall, (5) Funding rate statistics, (6) Delta-neutral verification, (7) Event logs, (8) Winner summary.
- **Key design decision:** Two liquidation penalty models:
  - HL: `penalty = equity * 50%` (equity-based)
  - Drift: `penalty = min(notional * 2.5%, remaining_equity)` (notional-based, capped by equity)

### 3.3 Run comparison and validate
- [x] **Status:** done
- **Validation:**
  - ✅ 410 aligned days across all data sources
  - ✅ Delta-neutral confirmed for both venues (SOL -57.8%, equity -3.6% HL / -9.6% Drift)
  - ✅ P&L waterfalls balance for both venues
  - ✅ Drift same-chain advantage visible ($18-36 bridge savings reduced to <$0.03)
  - ✅ Output saved to `data/comparison_backtest_output.txt`
- **Key findings:**
  - HL wins at 2x (+4.0% vs -4.8%) — funding dominates when liqs are rare
  - HL wins at 3x (-3.5% vs -8.9% at default trigger) — but Drift wins at optimal trigger (-1.3% vs -3.5%)
  - Drift wins at 4x (+2.3% vs -19.6%) — liquidation penalty model is the deciding factor
  - Drift's $0 effective liq penalty at 4x is realistic: 3% margin means positions are underwater when liquidated

### 3.4 Write comparison study document
- [x] **Status:** done
- **File:** `drift_comparison.md`
- **What:** Standalone analysis with venue comparison, side-by-side results, funding rate analysis, event log comparison, conclusions and venue recommendations by leverage level.

### 3.5 Update README.md and TRACKER.md
- [x] **Status:** done

---

## Decisions Log

| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| 1 | Use mark-to-market for HL funding (not entry notional) | On HL, funding settles on position_value = contracts × mark_price. When SOL drops 58%, funding income drops proportionally. More accurate than static model's fixed notional. | 2026-02-13 |
| 2 | Model carry as compounding (sol_qty grows, usdc_debt grows) | Lending yield adds SOL to balance, borrow interest adds to debt. Over 410 days, compounding effect is small but correct. | 2026-02-13 |
| 3 | Capital rebalance = adjust usdc_debt and short_margin | Transferring USD from short to long = withdraw from HL + bridge + reduce Asgard debt. Positions stay same size, only equity distribution changes. | 2026-02-13 |
| 4 | Liquidation penalty = 50% of remaining equity on liquidated leg | Aggressive estimate. In practice, depends on how fast the move was and liquidation mechanics. | 2026-02-13 |
| 5 | Keep 3 models for comparison: static / rebalance / full-rotation | Shows the full spectrum: theoretical ceiling, realistic, and pessimistic floor. | 2026-02-13 |
| 6 | Default trigger = 2× target leverage, but per-leverage optimization is critical | 2x/4x trigger: -4.8%. 2x/5x trigger: +4.0%. The trigger-leverage interaction is non-obvious and must be tuned. | 2026-02-13 |
| 7 | Track MTM drag as a separate P&L waterfall component | SOL lending yield is denominated in SOL. When SOL drops, accumulated SOL from lending loses value. This "MTM drag" is the balancing item between tracked income and actual return. | 2026-02-13 |
| 8 | Normalize Drift funding rates by dividing by oracle price | Drift API returns fundingRate in USDC-per-SOL-per-hour. Dividing by oraclePriceTwap gives fraction-of-notional, matching HL format. Same formula works for both venues. | 2026-02-13 |
| 9 | Two liquidation penalty models (equity-based vs notional-based) | HL uses 50% of remaining equity. Drift uses 2.5% of position notional, capped by remaining equity. At 3% maintenance margin, equity is often ~$0 when liquidation fires in daily simulation → effective penalty ≈ $0. | 2026-02-13 |
| 10 | Venue recommendation varies by leverage | 2x: HL (funding). 3x: depends on trigger (HL at 6x, Drift at 8x). 4x: Drift (only venue where profitable). | 2026-02-13 |

---

## Files Modified

| File | Status | Notes |
|------|--------|-------|
| `scripts/backtest_rebalance.py` | done | Full rewrite of simulation engine. 7-section output. |
| `data/rebalance_backtest_output.txt` | done | Saved output for reference. |
| `study.md` | done | Updated: Sections 1, 3.4, 5, 6, 7.1, 8, 9, Appendix, footnote |
| `README.md` | done | Updated: key finding table, three-model comparison, trigger table, script list, Drift comparison |
| `scripts/fetch_drift_funding.py` | done | Fetch + normalize Drift SOL-PERP funding rates |
| `data/drift_sol_funding_history.json` | done | 410 days of daily Drift funding rates (HL-normalized format) |
| `scripts/backtest_comparison.py` | done | Parameterized venue comparison backtest (8 output sections) |
| `data/comparison_backtest_output.txt` | done | Full comparison backtest output |
| `drift_comparison.md` | done | Standalone Drift vs HL comparison analysis |
