"""
Streamlit dashboard for delta-neutral basis trading feasibility study.

Strategy: open both legs when net APY > threshold, close when APY drops
below threshold OR when either leg gets within buffer % of liquidation.
Capital sits idle when not deployed.

Run with:  streamlit run scripts/app.py
"""

import sys
import os
from collections import defaultdict

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from lib.data import load_data
from lib.simulation import (
    run_simulation, venue_hl, venue_drift,
    compute_net_apy, ASGARD_FEE_BPS, GAS_COST,
)

# ── Page config ──────────────────────────────────────────────────────

st.set_page_config(
    page_title="Basis Trading Feasibility",
    page_icon="📊",
    layout="wide",
)

st.title("Delta-Neutral Basis Trading — Feasibility Dashboard")

st.markdown(
    "**Long leg (Asgard):** SOL collateral on Kamino, borrow USDC. Earns lending yield, pays borrow interest.\n\n"
    "**Short leg:** SOL perp on **Hyperliquid** or **Drift**. Earns funding rate.\n\n"
    "Price exposure cancels out — profit = carry + funding - fees. Opens when net APY > threshold."
)

# ── Data loading (cached) ────────────────────────────────────────────

@st.cache_data
def get_data():
    return load_data()

data = get_data()
all_dates = data["all_dates"]
N = len(all_dates)
p_start = data["price_by_date"][all_dates[0]]["close"]
p_end = data["price_by_date"][all_dates[-1]]["close"]

st.caption(
    f"Period: {all_dates[0]} to {all_dates[-1]} ({N} days) · "
    f"SOL: ${p_start:.2f} → ${p_end:.2f} ({(p_end/p_start-1)*100:+.1f}%)"
)

# ── Sidebar ──────────────────────────────────────────────────────────

st.sidebar.header("Parameters")

capital = st.sidebar.selectbox(
    "Capital",
    [1_000, 5_000, 10_000, 50_000, 100_000],
    index=2,
    format_func=lambda x: f"${x:,}",
)

leverage = st.sidebar.slider(
    "Leverage", min_value=2.0, max_value=4.0, value=3.0, step=0.5,
)

apy_threshold = st.sidebar.slider(
    "APY Threshold (%)",
    min_value=0.0, max_value=30.0, value=10.0, step=1.0,
    help="Open when net APY > this, close when it drops below",
)

liq_buffer = st.sidebar.slider(
    "Liquidation Buffer (%)",
    min_value=5.0, max_value=25.0, value=10.0, step=1.0,
    help="Close when price is within this % of liquidation",
)

lookback = st.sidebar.slider(
    "APY Lookback (days)",
    min_value=1, max_value=14, value=7, step=1,
    help="Rolling window for APY signal (prevents whipsawing)",
)

asgard_fee_bps = st.sidebar.number_input(
    "Asgard Fee (bps)",
    min_value=0.0, max_value=100.0, value=float(ASGARD_FEE_BPS), step=1.0,
    format="%.1f",
    help="Asgard opening fee in basis points (0.15% = 15 bps). Charged on notional at open only.",
)

venue_choice = st.sidebar.radio("Venue", ["Hyperliquid", "Drift", "Both"], index=2)

# ── Helpers ──────────────────────────────────────────────────────────

def get_venues():
    if venue_choice == "Hyperliquid":
        return [("Hyperliquid", venue_hl(data))]
    elif venue_choice == "Drift":
        return [("Drift", venue_drift(data))]
    else:
        return [("Hyperliquid", venue_hl(data)), ("Drift", venue_drift(data))]

VENUE_COLORS = {"Hyperliquid": "#636EFA", "Drift": "#EF553B"}

@st.cache_data
def cached_sim(_data_id, cap, lev, venue_name, apy_thr, liq_buf, lb_days, fee_bps):
    venues = {"Hyperliquid": venue_hl(data), "Drift": venue_drift(data)}
    return run_simulation(
        data, cap, lev, venues[venue_name],
        apy_threshold=apy_thr, liq_buffer_pct=liq_buf,
        lookback_days=lb_days, asgard_fee_bps=fee_bps,
    )

data_id = (all_dates[0], all_dates[-1], N)

def sim(cap, lev, vname, apy_thr, liq_buf, lb_days=None, fee_bps=None):
    if lb_days is None:
        lb_days = lookback
    if fee_bps is None:
        fee_bps = asgard_fee_bps
    return cached_sim(data_id, cap, lev, vname, apy_thr, liq_buf, lb_days, fee_bps)

def find_contiguous_blocks(dates_list, all_dates_ref):
    if not dates_list:
        return []
    date_set = set(dates_list)
    blocks = []
    start = None
    for d in all_dates_ref:
        if d in date_set:
            if start is None:
                start = d
            end = d
        else:
            if start is not None:
                blocks.append((start, end))
                start = None
    if start is not None:
        blocks.append((start, end))
    return blocks

def cycle_cost(cap, lev, venue_cfg, asg_bps=None):
    """Cost of one full open+close cycle."""
    if asg_bps is None:
        asg_bps = asgard_fee_bps
    half = cap / 2
    notional = half * lev
    fee_bps = venue_cfg["fee_bps"]
    open_fee = notional * asg_bps / 10000 + notional * fee_bps / 10000 + GAS_COST
    close_fee = notional * fee_bps / 10000 + GAS_COST
    return open_fee + close_fee


# ── Run simulations for selected params ──────────────────────────────

venues = get_venues()
results = {}
for vname, vcfg in venues:
    results[vname] = sim(capital, leverage, vname, apy_threshold, liq_buffer)

# ═════════════════════════════════════════════════════════════════════
# MAIN TABS
# ═════════════════════════════════════════════════════════════════════

main_tab2, main_tab1 = st.tabs(["Breakeven Analysis", "Comparison Analysis"])

# ═════════════════════════════════════════════════════════════════════
# TAB 1: COMPARISON ANALYSIS (HL vs Drift)
# ═════════════════════════════════════════════════════════════════════

with main_tab1:

    # -- KPI Cards --
    for vname, _ in venues:
        r = results[vname]
        with st.container(border=True):
            st.markdown(f"**{vname}**")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Ann Return", f"{r['ann_return']:+.1f}%", help="Annualized return on total capital over the full backtest period, including idle time")
            c2.metric("Max Drawdown", f"{r['max_dd_pct']:.1f}%", help="Largest peak-to-trough equity decline during the backtest")
            c3.metric("Deployed", f"{r['deployed_pct']:.0f}%", help="Percentage of days the position was open (not idle). Lower = more time sitting in cash")
            c4.metric("Opens / Closes", f"{r['opens']} / {r['closes']}", help="Number of times the position was opened and closed. Each cycle incurs open/close fees")
            c5.metric("Total Fees", f"${r['total_fees']:,.0f}", help="Sum of all trading fees: Asgard open fees, venue taker fees, and gas costs across all open/close cycles")

    st.divider()

    # -- Portfolio Value --
    st.subheader("Portfolio Value")

    LEV_COLORS = {2.0: "#2ECC71", 2.5: "#27AE60", 3.0: "#3498DB", 3.5: "#8E44AD", 4.0: "#E74C3C"}
    all_levs = [2.0, 2.5, 3.0, 3.5, 4.0]

    for vname, _ in venues:
        fig_pv = make_subplots(specs=[[{"secondary_y": True}]])
        for lev in all_levs:
            r_lev = sim(capital, lev, vname, apy_threshold, liq_buffer)
            eq_df = pd.DataFrame(r_lev["daily_equity"])
            fig_pv.add_trace(
                go.Scatter(x=eq_df["date"], y=eq_df["equity"], name=f"{lev:.1f}x", line=dict(color=LEV_COLORS[lev])),
                secondary_y=False,
            )
        sol_prices = [data["price_by_date"][d]["close"] for d in all_dates]
        fig_pv.add_trace(
            go.Scatter(x=all_dates, y=sol_prices, name="SOL Price", line=dict(color="gray", dash="dot"), opacity=0.4),
            secondary_y=True,
        )
        fig_pv.add_hline(y=capital, line_dash="dash", line_color="gray", opacity=0.4, secondary_y=False)
        fig_pv.update_layout(title=f"{vname} — ${capital:,} across leverage levels", height=500, legend=dict(orientation="h", yanchor="bottom", y=1.02))
        fig_pv.update_yaxes(title_text="Portfolio Value ($)", secondary_y=False)
        fig_pv.update_yaxes(title_text="SOL Price ($)", secondary_y=True)
        st.plotly_chart(fig_pv, use_container_width=True)

    st.divider()

    # -- Net APY Over Time --
    st.subheader("Net APY Over Time")

    fig_apy = go.Figure()
    for vname in ["Hyperliquid", "Drift"]:
        funding_ref = data["hl_by_date"] if vname == "Hyperliquid" else data["drift_by_date"]
        apys = []
        for d in all_dates:
            sol_lend = data["sol_by_date"].get(d, {}).get("lend", 0)
            usdc_borr = data["usdc_by_date"].get(d, {}).get("borrow", 0)
            fund = funding_ref.get(d, 0)
            apys.append(compute_net_apy(sol_lend, usdc_borr, fund, leverage))
        apy_series = pd.Series(apys, index=all_dates)
        apy_roll = apy_series.rolling(lookback, min_periods=1).mean()
        fig_apy.add_trace(go.Scatter(x=all_dates, y=apy_roll, name=f"{vname} ({lookback}d avg)", line=dict(color=VENUE_COLORS[vname])))

    fig_apy.add_hline(y=apy_threshold, line_dash="dash", line_color="red", annotation_text=f"Threshold: {apy_threshold:.0f}%")
    fig_apy.update_layout(title=f"Net APY at {leverage:.1f}x Leverage ({lookback}-day rolling avg)", yaxis_title="Net APY %", height=400, legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(fig_apy, use_container_width=True)

    st.divider()

    # -- Funding Rates --
    st.subheader("Funding Rates")
    col1, col2 = st.columns(2)

    with col1:
        hl_rates = [data["hl_by_date"].get(d, 0) for d in all_dates]
        dr_rates = [data["drift_by_date"].get(d, 0) for d in all_dates]
        hl_roll = pd.Series(hl_rates, index=all_dates).rolling(30).mean() * 365 * 100
        dr_roll = pd.Series(dr_rates, index=all_dates).rolling(30).mean() * 365 * 100
        fig_roll = go.Figure()
        fig_roll.add_trace(go.Scatter(x=all_dates, y=hl_roll, name="Hyperliquid", line=dict(color=VENUE_COLORS["Hyperliquid"])))
        fig_roll.add_trace(go.Scatter(x=all_dates, y=dr_roll, name="Drift", line=dict(color=VENUE_COLORS["Drift"])))
        fig_roll.update_layout(title="Rolling 30-Day Avg Funding (Annualized %)", yaxis_title="Annualized Rate %", height=350, legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig_roll, use_container_width=True)

    with col2:
        hl_monthly = defaultdict(list)
        dr_monthly = defaultdict(list)
        for d in all_dates:
            month = d[:7]
            hl_monthly[month].append(data["hl_by_date"].get(d, 0))
            dr_monthly[month].append(data["drift_by_date"].get(d, 0))
        months = sorted(hl_monthly.keys())
        hl_month_ann = [sum(hl_monthly[m]) / len(hl_monthly[m]) * 365 * 100 for m in months]
        dr_month_ann = [sum(dr_monthly[m]) / len(dr_monthly[m]) * 365 * 100 for m in months]
        fig_monthly = go.Figure()
        fig_monthly.add_trace(go.Bar(x=months, y=hl_month_ann, name="Hyperliquid", marker_color=VENUE_COLORS["Hyperliquid"]))
        fig_monthly.add_trace(go.Bar(x=months, y=dr_month_ann, name="Drift", marker_color=VENUE_COLORS["Drift"]))
        fig_monthly.update_layout(title="Monthly Avg Funding (Annualized %)", barmode="group", yaxis_title="Annualized Rate %", height=350, legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig_monthly, use_container_width=True)

    hl_rates = [data["hl_by_date"].get(d, 0) for d in all_dates]
    dr_rates = [data["drift_by_date"].get(d, 0) for d in all_dates]
    hl_mean = sum(hl_rates) / N
    dr_mean = sum(dr_rates) / N
    stats_df = pd.DataFrame({
        "Metric": ["Annualized Mean Funding", "Positive Days (shorts earn)", "Negative Days (shorts pay)", "Max Daily Rate", "Min Daily Rate"],
        "Hyperliquid": [f"{hl_mean*365*100:+.2f}%", str(sum(1 for r in hl_rates if r > 0)), str(sum(1 for r in hl_rates if r < 0)), f"{max(hl_rates):.6f}", f"{min(hl_rates):.6f}"],
        "Drift": [f"{dr_mean*365*100:+.2f}%", str(sum(1 for r in dr_rates if r > 0)), str(sum(1 for r in dr_rates if r < 0)), f"{max(dr_rates):.6f}", f"{min(dr_rates):.6f}"],
    })
    st.dataframe(stats_df, hide_index=True, use_container_width=True)

    st.divider()

    # -- P&L Waterfall --
    st.subheader("P&L Waterfall")
    levs = [2.0, 3.0, 4.0]
    for vname, vcfg in venues:
        st.markdown(f"**{vname} — ${capital:,}**")
        fig_wf = make_subplots(rows=1, cols=len(levs), subplot_titles=[f"{l:.0f}x Leverage" for l in levs])
        for li, lev in enumerate(levs):
            r = sim(capital, lev, vname, apy_threshold, liq_buffer)
            labels = ["Carry", "Funding", "Gross", "−Fees", "Net"]
            values = [r["carry"], r["funding"], r["gross"], -r["total_fees"], r["final"] - r["initial"]]
            measures = ["relative", "relative", "total", "relative", "total"]
            fig_wf.add_trace(go.Waterfall(x=labels, y=values, measure=measures, increasing=dict(marker_color="#2ECC71"), decreasing=dict(marker_color="#E74C3C"), totals=dict(marker_color="#3498DB"), textposition="outside", text=[f"${v:+,.0f}" for v in values], showlegend=False), row=1, col=li + 1)
        fig_wf.update_layout(height=450, showlegend=False)
        st.plotly_chart(fig_wf, use_container_width=True)

        rows = []
        for lev in levs:
            r = sim(capital, lev, vname, apy_threshold, liq_buffer)
            rows.append({"Leverage": f"{lev:.0f}x", "Ann Return": f"{r['ann_return']:+.1f}%", "Deployed %": f"{r['deployed_pct']:.0f}%", "Opens": r["opens"], "Closes (APY)": r["close_reasons"]["apy"], "Closes (Liq Prox)": r["close_reasons"]["liq_proximity"], "Total Fees": f"${r['total_fees']:,.0f}", "Max DD": f"{r['max_dd_pct']:.1f}%"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.divider()

    # -- Threshold Heatmap --
    st.subheader("APY Threshold × Leverage Heatmap")
    st.caption("Annualized return % for each combination. Position only open when net APY > threshold.")
    thresholds = [0, 5, 8, 10, 12, 15, 20, 25]
    levs = [2.0, 2.5, 3.0, 3.5, 4.0]
    hm_cols = st.columns(len(venues))
    for vi, (vname, vcfg) in enumerate(venues):
        ret_matrix = []
        annotation_text = []
        for lev in levs:
            row_ret = []
            row_ann = []
            for thr in thresholds:
                r = sim(capital, lev, vname, float(thr), liq_buffer)
                row_ret.append(r["ann_return"])
                row_ann.append(f"{r['ann_return']:+.1f}%\n{r['deployed_pct']:.0f}%d")
            ret_matrix.append(row_ret)
            annotation_text.append(row_ann)
        fig_hm = go.Figure(data=go.Heatmap(z=ret_matrix, x=[f"{t}%" for t in thresholds], y=[f"{l:.1f}x" for l in levs], text=annotation_text, texttemplate="%{text}", colorscale="RdYlGn", colorbar_title="Ann Ret %"))
        fig_hm.update_layout(title=f"{vname} — ${capital:,} (cells show return + deployed %)", xaxis_title="APY Threshold", yaxis_title="Leverage", height=450)
        with hm_cols[vi]:
            st.plotly_chart(fig_hm, use_container_width=True)

    st.divider()

    # -- Event Timeline --
    st.subheader("Event Timeline")
    for vname, vcfg in venues:
        r = results[vname]
        fig_tl = go.Figure()
        sol_prices = [data["price_by_date"][d]["close"] for d in all_dates]
        fig_tl.add_trace(go.Scatter(x=all_dates, y=sol_prices, name="SOL Price", line=dict(color="gray"), opacity=0.7))
        eq_df = pd.DataFrame(r["daily_equity"])
        deployed_dates = eq_df[eq_df["state"] == "DEPLOYED"]["date"].tolist()
        for b_start, b_end in find_contiguous_blocks(deployed_dates, all_dates):
            fig_tl.add_vrect(x0=b_start, x1=b_end, fillcolor="green", opacity=0.07, layer="below", line_width=0)
        open_evts = [e for e in r["events"] if e["type"] == "OPEN"]
        if open_evts:
            fig_tl.add_trace(go.Scatter(x=[e["date"] for e in open_evts], y=[e["price"] for e in open_evts], mode="markers", marker=dict(size=12, color="#2ECC71", symbol="triangle-up"), name="Open", hovertext=[f"OPEN<br>Price: ${e['price']:.2f}<br>Net APY: {e['net_apy']:.1f}%<br>Notional: ${e['notional']:,.0f}" for e in open_evts], hoverinfo="text"))
        close_evts = [e for e in r["events"] if e["type"] == "CLOSE"]
        if close_evts:
            fig_tl.add_trace(go.Scatter(x=[e["date"] for e in close_evts], y=[e["price"] for e in close_evts], mode="markers", marker=dict(size=12, color=["red" if e["reason"] == "liq_proximity" else "orange" for e in close_evts], symbol=["x" if e["reason"] == "liq_proximity" else "triangle-down" for e in close_evts]), name="Close", hovertext=[f"CLOSE ({e['reason']})<br>Price: ${e['price']:.2f}<br>Net APY: {e['net_apy']:.1f}%<br>Equity: ${e['equity_after']:,.0f}<br>Long buf: {e['long_buffer']:.1f}%  Short buf: {e['short_buffer']:.1f}%" for e in close_evts], hoverinfo="text"))
        fig_tl.update_layout(title=f"{vname} — Events on SOL Price (green = deployed)", yaxis_title="SOL Price ($)", height=500, legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig_tl, use_container_width=True)

        event_rows = []
        for e in r["events"]:
            row = {"Date": e["date"], "Type": e["type"], "Price": f"${e['price']:.2f}"}
            if e["type"] == "OPEN":
                row["Detail"] = f"APY={e['net_apy']:.1f}%, Notional=${e['notional']:,.0f}, Fees=${e['fees']:.0f}"
            elif e["type"] == "CLOSE":
                row["Detail"] = f"Reason={e['reason']}, APY={e['net_apy']:.1f}%, Equity=${e['equity_after']:,.0f}, Fees=${e['fees']:.0f}"
            event_rows.append(row)
        if event_rows:
            st.dataframe(pd.DataFrame(event_rows), hide_index=True, use_container_width=True)


# ═════════════════════════════════════════════════════════════════════
# TAB 2: BREAKEVEN ANALYSIS
# ═════════════════════════════════════════════════════════════════════

with main_tab2:

    st.markdown(
        "How long does a trade need to stay open to cover the open/close fees? "
        "This section answers that question at different APY levels and leverage."
    )

    # -----------------------------------------------------------------
    # 1. Fee breakdown per cycle
    # -----------------------------------------------------------------
    st.subheader("Cost Per Open/Close Cycle")

    levs_be = [2.0, 2.5, 3.0, 3.5, 4.0]
    venue_cfgs = {"Hyperliquid": venue_hl(data), "Drift": venue_drift(data)}

    fee_rows = []
    for lev in levs_be:
        half = capital / 2
        notional = half * lev
        for vname, vcfg in venue_cfgs.items():
            fee_bps = vcfg["fee_bps"]
            asgard_fee = notional * asgard_fee_bps / 10000
            venue_total = notional * fee_bps / 10000 * 2  # open + close
            gas = GAS_COST * 2
            total = asgard_fee + venue_total + gas
            fee_rows.append({
                "Leverage": f"{lev:.1f}x",
                "Venue": vname,
                "Notional": f"${notional:,.0f}",
                f"Asgard ({asgard_fee_bps:.1f}bps)": f"${asgard_fee:,.1f}",
                f"Perp ({fee_bps}bps x2)": f"${venue_total:,.1f}",
                "Gas (x2)": f"${gas:,.1f}",
                "Total": f"${total:,.1f}",
                "% of Capital": f"{total / capital * 100:.2f}%",
            })
    st.dataframe(pd.DataFrame(fee_rows), hide_index=True, use_container_width=True)

    # Stacked bar chart: fee components by leverage
    fig_fees = go.Figure()
    asgard_vals, venue_vals, gas_vals = [], [], []
    for lev in levs_be:
        half = capital / 2
        notional = half * lev
        asgard_vals.append(notional * asgard_fee_bps / 10000)
        venue_vals.append(notional * 3.5 / 10000 * 2)
        gas_vals.append(GAS_COST * 2)
    x_labels = [f"{lev:.1f}x" for lev in levs_be]
    fig_fees.add_trace(go.Bar(x=x_labels, y=asgard_vals, name=f"Asgard ({asgard_fee_bps:.1f}bps)", marker_color="#E74C3C"))
    fig_fees.add_trace(go.Bar(x=x_labels, y=venue_vals, name="Perp Venue (3.5bps x 2)", marker_color="#3498DB"))
    fig_fees.add_trace(go.Bar(x=x_labels, y=gas_vals, name="Gas", marker_color="#95A5A6"))
    fig_fees.update_layout(
        title=f"Fee Breakdown Per Cycle — ${capital:,} (same for HL & Drift)",
        xaxis_title="Leverage", yaxis_title="Cost ($)",
        barmode="stack", height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig_fees, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------
    # 2. Breakeven days at different APY levels
    # -----------------------------------------------------------------
    st.subheader("Days to Break Even")
    st.caption(
        "Minimum days the position must stay open at a given net APY to recover "
        "the open + close fees for one cycle."
    )

    apy_levels = [10, 15, 20, 25, 30, 40, 50]

    for vname, vcfg in venue_cfgs.items():
        be_rows = []
        for lev in levs_be:
            cost = cycle_cost(capital, lev, vcfg)
            row = {"Leverage": f"{lev:.1f}x", "Cycle Cost": f"${cost:,.1f}"}
            for apy in apy_levels:
                daily_income = capital * (apy / 100) / 365
                if daily_income > 0:
                    days = cost / daily_income
                    row[f"{apy}% APY"] = f"{days:.1f}d"
                else:
                    row[f"{apy}% APY"] = "—"
            be_rows.append(row)

        st.markdown(f"**{vname}** — ${capital:,}")
        st.dataframe(pd.DataFrame(be_rows), hide_index=True, use_container_width=True)

    # Grouped bar chart: breakeven days by APY level
    fig_be_days = go.Figure()
    vcfg_ref = list(venue_cfgs.values())[0]
    for lev in [2.0, 3.0, 4.0]:
        cost = cycle_cost(capital, lev, vcfg_ref)
        days_list = []
        for apy in apy_levels:
            daily_income = capital * (apy / 100) / 365
            days_list.append(cost / daily_income if daily_income > 0 else None)
        fig_be_days.add_trace(go.Bar(
            x=[f"{a}%" for a in apy_levels], y=days_list,
            name=f"{lev:.0f}x Leverage", marker_color=LEV_COLORS[lev],
        ))
    fig_be_days.update_layout(
        title=f"Breakeven Days by APY Level — ${capital:,}",
        xaxis_title="Net APY", yaxis_title="Days to Break Even",
        barmode="group", height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig_be_days, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------
    # 3. Breakeven curve chart
    # -----------------------------------------------------------------
    st.subheader("Breakeven Days vs Net APY")

    fig_be_curve = go.Figure()
    apy_range = list(range(5, 61))

    for lev in [2.0, 3.0, 4.0]:
        # Use HL cost (HL and Drift have nearly identical fee_bps)
        cost = cycle_cost(capital, lev, venue_cfgs["Hyperliquid"])
        days = [cost / (capital * (a / 100) / 365) for a in apy_range]
        fig_be_curve.add_trace(go.Scatter(
            x=apy_range, y=days,
            name=f"{lev:.0f}x Leverage",
            line=dict(color=LEV_COLORS[lev]),
        ))

    fig_be_curve.add_hline(y=7, line_dash="dot", line_color="gray", annotation_text="7 days")
    fig_be_curve.add_hline(y=14, line_dash="dot", line_color="gray", annotation_text="14 days")

    fig_be_curve.update_layout(
        title=f"Days Needed to Break Even — ${capital:,}",
        xaxis_title="Net APY %",
        yaxis_title="Days to Break Even",
        height=400,
        yaxis=dict(range=[0, 30]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig_be_curve, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------
    # 4. Breakeven APY (minimum APY needed for a given holding period)
    # -----------------------------------------------------------------
    st.subheader("Minimum APY Needed")
    st.caption(
        "If you expect the trade to last N days, what minimum net APY do you need "
        "to just cover fees?"
    )

    hold_days = [3, 5, 7, 10, 14, 21, 30]

    for vname, vcfg in venue_cfgs.items():
        min_apy_rows = []
        for lev in levs_be:
            cost = cycle_cost(capital, lev, vcfg)
            row = {"Leverage": f"{lev:.1f}x"}
            for hd in hold_days:
                # cost = capital * (apy/100) / 365 * hd → apy = cost / capital * 365 / hd * 100
                min_apy = cost / capital * 365 / hd * 100
                row[f"{hd}d hold"] = f"{min_apy:.1f}%"
            min_apy_rows.append(row)

        st.markdown(f"**{vname}** — ${capital:,}")
        st.dataframe(pd.DataFrame(min_apy_rows), hide_index=True, use_container_width=True)

    # Line chart: minimum APY vs holding period
    fig_min_apy = go.Figure()
    vcfg_ref = list(venue_cfgs.values())[0]
    for lev in [2.0, 3.0, 4.0]:
        cost = cycle_cost(capital, lev, vcfg_ref)
        min_apys = [cost / capital * 365 / hd * 100 for hd in hold_days]
        fig_min_apy.add_trace(go.Scatter(
            x=hold_days, y=min_apys,
            name=f"{lev:.0f}x Leverage",
            line=dict(color=LEV_COLORS[lev]),
            mode="lines+markers",
        ))
    fig_min_apy.add_hline(
        y=apy_threshold, line_dash="dash", line_color="red",
        annotation_text=f"Threshold: {apy_threshold:.0f}%",
    )
    fig_min_apy.update_layout(
        title=f"Minimum APY to Break Even — ${capital:,}",
        xaxis_title="Holding Period (days)", yaxis_title="Minimum Net APY %",
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig_min_apy, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------
    # 5. Time in trade at different APY levels — from actual backtest
    # -----------------------------------------------------------------
    st.subheader("Actual Trade Durations vs Breakeven")
    st.caption(
        "Each bar is one open/close cycle from the backtest. "
        "The dashed lines show the minimum hold time needed to break even at that APY level."
    )

    for vname, vcfg in venue_cfgs.items():
        r = sim(capital, leverage, vname, apy_threshold, liq_buffer)

        # Extract cycle durations
        events = r["events"]
        cycles = []
        for i, e in enumerate(events):
            if e["type"] == "OPEN":
                for j in range(i + 1, len(events)):
                    if events[j]["type"] == "CLOSE":
                        open_idx = all_dates.index(e["date"])
                        close_idx = all_dates.index(events[j]["date"])
                        days = close_idx - open_idx
                        pnl = events[j]["equity_after"] - (e["long_eq"] + e["short_eq"])
                        cycles.append({
                            "open": e["date"],
                            "close": events[j]["date"],
                            "days": days,
                            "pnl": pnl,
                            "reason": events[j]["reason"],
                            "open_apy": e["net_apy"],
                        })
                        break

        if not cycles:
            st.markdown(f"**{vname}**: No trades at current settings.")
            continue

        cost = cycle_cost(capital, leverage, vcfg)

        fig_dur = go.Figure()

        # Bars for each cycle
        colors = ["#2ECC71" if c["pnl"] > 0 else "#E74C3C" for c in cycles]
        fig_dur.add_trace(go.Bar(
            x=[f"#{i+1}" for i in range(len(cycles))],
            y=[c["days"] for c in cycles],
            marker_color=colors,
            hovertext=[
                f"{c['open']} → {c['close']}<br>"
                f"{c['days']}d, P&L: ${c['pnl']:+,.0f}<br>"
                f"Reason: {c['reason']}<br>"
                f"Open APY: {c['open_apy']:.1f}%"
                for c in cycles
            ],
            hoverinfo="text",
            name="Trade Duration",
        ))

        # Breakeven lines at selected APY levels
        be_apy_lines = [10, 20, 30, 40]
        line_colors = ["#E74C3C", "#E67E22", "#F1C40F", "#2ECC71"]
        for apy, lc in zip(be_apy_lines, line_colors):
            daily_income = capital * (apy / 100) / 365
            if daily_income > 0:
                be_days = cost / daily_income
                fig_dur.add_hline(
                    y=be_days, line_dash="dash", line_color=lc,
                    annotation_text=f"BE @ {apy}% ({be_days:.1f}d)",
                    annotation_position="top right",
                )

        fig_dur.update_layout(
            title=f"{vname} — Trade Durations at {leverage:.1f}x (green = profitable, red = loss)",
            xaxis_title="Trade #",
            yaxis_title="Days Open",
            height=400,
        )
        st.plotly_chart(fig_dur, use_container_width=True)

        # Cycle summary table
        cycle_df = pd.DataFrame([{
            "#": i + 1,
            "Open": c["open"],
            "Close": c["close"],
            "Days": c["days"],
            "P&L": f"${c['pnl']:+,.0f}",
            "Close Reason": c["reason"],
            "Open APY": f"{c['open_apy']:.1f}%",
            "Profitable": "Yes" if c["pnl"] > 0 else "No",
        } for i, c in enumerate(cycles)])
        st.dataframe(cycle_df, hide_index=True, use_container_width=True)

    st.divider()

    # -----------------------------------------------------------------
    # 6. Breakeven heatmap: capital × leverage
    # -----------------------------------------------------------------
    st.subheader("Breakeven Days Heatmap — Capital × Leverage")
    st.caption(
        f"Days to break even at the current sidebar APY threshold ({apy_threshold:.0f}%). "
        "Larger capital = fees are a smaller fraction = faster breakeven."
    )

    caps_be = [1_000, 5_000, 10_000, 50_000, 100_000]
    levs_hm = [2.0, 2.5, 3.0, 3.5, 4.0]

    for vname, vcfg in venue_cfgs.items():
        be_matrix = []
        be_text = []
        for lev in levs_hm:
            row_z = []
            row_t = []
            for cap in caps_be:
                cost = cycle_cost(cap, lev, vcfg)
                daily_income = cap * (apy_threshold / 100) / 365
                if daily_income > 0 and apy_threshold > 0:
                    days = cost / daily_income
                    row_z.append(days)
                    row_t.append(f"{days:.1f}d")
                else:
                    row_z.append(None)
                    row_t.append("—")
            be_matrix.append(row_z)
            be_text.append(row_t)

        fig_be_hm = go.Figure(data=go.Heatmap(
            z=be_matrix,
            x=[f"${c:,}" for c in caps_be],
            y=[f"{l:.1f}x" for l in levs_hm],
            text=be_text,
            texttemplate="%{text}",
            colorscale="RdYlGn_r",
            colorbar_title="Days",
        ))
        fig_be_hm.update_layout(
            title=f"{vname} — Days to Break Even @ {apy_threshold:.0f}% Net APY",
            xaxis_title="Capital",
            yaxis_title="Leverage",
            height=400,
        )
        st.plotly_chart(fig_be_hm, use_container_width=True)
