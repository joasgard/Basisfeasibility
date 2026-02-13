"""
Simulation engine for delta-neutral basis trading.

Strategy rules:
  - OPEN when net APY of both legs combined > threshold (default 10%)
  - CLOSE when net APY drops below threshold OR either leg is within
    liq_buffer_pct of its liquidation price
  - Both legs always open/close together (delta-neutral at all times)
  - Position is NEVER liquidated — we exit before that happens
  - Capital sits idle when conditions aren't met
"""

ASGARD_FEE_BPS = 15   # 0.15% on notional (open only)
GAS_COST = 2.0        # per on-chain operation


def venue_hl(data):
    """Return Hyperliquid venue config with funding data bound."""
    return {
        "name": "Hyperliquid",
        "fee_bps": 3.5,
        "maintenance_margin": 0.05,
        "bridge_cost": 3.0,
        "funding_by_date": data["hl_by_date"],
    }


def venue_drift(data):
    """Return Drift venue config with funding data bound."""
    return {
        "name": "Drift",
        "fee_bps": 3.5,
        "maintenance_margin": 0.03,
        "bridge_cost": 0.001,
        "funding_by_date": data["drift_by_date"],
    }


def compute_net_apy(sol_lend_apy, usdc_borr_apy, funding_daily, leverage):
    """
    Instantaneous net APY on total capital.

    Long leg:  earns sol_lend on notional, pays usdc_borrow on debt
    Short leg: earns funding on notional

    net = L/2 * (sol_lend + funding_ann) - (L-1)/2 * usdc_borrow
    All inputs are percentages (sol_lend_apy, usdc_borr_apy) or daily rate (funding).
    Returns APY as a percentage.
    """
    L = leverage
    funding_ann_pct = funding_daily * 365 * 100
    return (L / 2) * (sol_lend_apy + funding_ann_pct) - ((L - 1) / 2) * usdc_borr_apy


def run_simulation(
    data: dict,
    initial_capital: float,
    target_leverage: float,
    venue: dict,
    apy_threshold: float = 10.0,
    liq_buffer_pct: float = 10.0,
    lookback_days: int = 7,
    asgard_fee_bps: float = ASGARD_FEE_BPS,
):
    """
    Simulate delta-neutral strategy with conditional deployment.

    Parameters
    ----------
    data : dict from load_data()
    initial_capital : starting USD
    target_leverage : e.g. 2.0, 3.0, 4.0
    venue : venue config dict (from venue_hl / venue_drift)
    apy_threshold : minimum net APY % to open / stay open
    liq_buffer_pct : close when price is within this % of liquidation
    lookback_days : rolling window for APY signal (avoids whipsawing)
    asgard_fee_bps : Asgard opening fee in basis points
    """
    all_dates = data["all_dates"]
    sol_by_date = data["sol_by_date"]
    usdc_by_date = data["usdc_by_date"]
    price_by_date = data["price_by_date"]

    fee_bps = venue["fee_bps"]
    maint_margin = venue["maintenance_margin"]
    funding_data = venue["funding_by_date"]

    # -- Pre-compute daily net APY for the full period --
    raw_apys = []
    for date in all_dates:
        sol_lend_apy = sol_by_date.get(date, {}).get("lend", 0)
        usdc_borr_apy = usdc_by_date.get(date, {}).get("borrow", 0)
        fd = funding_data.get(date, 0)
        raw_apys.append(compute_net_apy(sol_lend_apy, usdc_borr_apy, fd, target_leverage))

    # Rolling average for entry/exit signal
    smoothed_apys = []
    for i in range(len(raw_apys)):
        window_start = max(0, i - lookback_days + 1)
        smoothed_apys.append(sum(raw_apys[window_start:i+1]) / (i - window_start + 1))

    # -- State machine --
    state = "IDLE"
    cash = initial_capital

    # Position state (only meaningful when DEPLOYED)
    sol_qty = 0.0
    usdc_debt = 0.0
    short_contracts = 0.0
    short_entry = 0.0
    short_margin = 0.0

    # -- Tracking --
    total_fees = 0.0
    total_carry = 0.0
    total_funding = 0.0
    opens = 0
    closes = 0
    close_reasons = {"apy": 0, "liq_proximity": 0}
    deployed_days = 0
    idle_days = 0
    events = []
    daily_equity_log = []
    daily_apy_log = []

    for i, date in enumerate(all_dates):
        p = price_by_date[date]
        sol_close = p["close"]

        # Rates for today
        sol_lend_apy = sol_by_date.get(date, {}).get("lend", 0)   # % annualized
        usdc_borr_apy = usdc_by_date.get(date, {}).get("borrow", 0)
        funding_daily = funding_data.get(date, 0)

        net_apy_raw = raw_apys[i]
        net_apy = smoothed_apys[i]  # smoothed signal for entry/exit decisions
        daily_apy_log.append({"date": date, "net_apy": net_apy_raw, "net_apy_smooth": net_apy})

        if state == "IDLE":
            if net_apy > apy_threshold:
                # -- OPEN both legs --
                half = cash / 2
                notional = half * target_leverage

                sol_qty = notional / sol_close
                usdc_debt = half * (target_leverage - 1)
                short_contracts = notional / sol_close
                short_entry = sol_close
                short_margin = half

                open_fees = (
                    notional * asgard_fee_bps / 10000
                    + notional * fee_bps / 10000
                    + GAS_COST
                )
                usdc_debt += open_fees / 2
                short_margin -= open_fees / 2
                total_fees += open_fees

                state = "DEPLOYED"
                opens += 1

                long_eq = sol_qty * sol_close - usdc_debt
                short_eq = short_margin
                total_eq = long_eq + short_eq

                events.append({
                    "date": date, "type": "OPEN",
                    "price": sol_close,
                    "long_eq": long_eq, "short_eq": short_eq,
                    "notional": notional, "fees": open_fees,
                    "net_apy": net_apy,
                })

                daily_equity_log.append({
                    "date": date, "equity": total_eq, "state": "DEPLOYED",
                })
                deployed_days += 1
            else:
                daily_equity_log.append({
                    "date": date, "equity": cash, "state": "IDLE",
                })
                idle_days += 1

        elif state == "DEPLOYED":
            # -- Accrue daily yields --
            sol_lend_daily = (sol_lend_apy / 100) / 365
            usdc_borr_daily = (usdc_borr_apy / 100) / 365

            sol_earned = sol_qty * sol_lend_daily
            debt_increase = usdc_debt * usdc_borr_daily
            carry = sol_earned * sol_close - debt_increase

            sol_qty += sol_earned
            usdc_debt += debt_increase

            fund_income = funding_daily * short_contracts * sol_close
            short_margin += fund_income

            total_carry += carry
            total_funding += fund_income

            # -- Derive equity --
            long_eq = sol_qty * sol_close - usdc_debt
            short_eq = short_margin + short_contracts * (short_entry - sol_close)

            # -- Check liquidation proximity --
            # Long leg: liquidation at price where equity = maint_margin * notional
            liq_price_long = usdc_debt / (sol_qty * (1 - maint_margin))
            # Short leg: liquidation at high price
            liq_price_short = (
                (short_margin + short_contracts * short_entry)
                / (short_contracts * (1 + maint_margin))
            )

            # Buffer: how far is current price from each liquidation price (%)
            long_buffer = (sol_close - liq_price_long) / sol_close * 100
            short_buffer = (liq_price_short - sol_close) / sol_close * 100

            near_liq = long_buffer <= liq_buffer_pct or short_buffer <= liq_buffer_pct
            apy_below = net_apy < apy_threshold

            if near_liq or apy_below:
                # -- CLOSE both legs --
                close_fee = (
                    short_contracts * sol_close * fee_bps / 10000
                    + GAS_COST
                )
                total_fees += close_fee

                total_eq = long_eq + short_eq - close_fee
                cash = total_eq

                reason = "liq_proximity" if near_liq else "apy"
                close_reasons[reason] += 1
                closes += 1

                events.append({
                    "date": date, "type": "CLOSE",
                    "reason": reason,
                    "price": sol_close,
                    "long_eq": long_eq, "short_eq": short_eq,
                    "equity_after": cash,
                    "fees": close_fee,
                    "net_apy": net_apy,
                    "long_buffer": long_buffer,
                    "short_buffer": short_buffer,
                })

                # Reset position state
                sol_qty = 0.0
                usdc_debt = 0.0
                short_contracts = 0.0
                short_entry = 0.0
                short_margin = 0.0

                state = "IDLE"

                daily_equity_log.append({
                    "date": date, "equity": cash, "state": "IDLE",
                })
                idle_days += 1
            else:
                total_eq = long_eq + short_eq
                daily_equity_log.append({
                    "date": date, "equity": total_eq, "state": "DEPLOYED",
                })
                deployed_days += 1

    # -- If still deployed at end, close --
    if state == "DEPLOYED":
        final_price = price_by_date[all_dates[-1]]["close"]
        close_fee = short_contracts * final_price * fee_bps / 10000
        total_fees += close_fee
        final_capital = long_eq + short_eq - close_fee
    else:
        final_capital = cash

    total_return = final_capital - initial_capital
    n_days = len(all_dates)
    ann_return = (total_return / initial_capital) * (365 / n_days) * 100

    # Max drawdown
    peak = initial_capital
    max_dd = 0
    for de in daily_equity_log:
        peak = max(peak, de["equity"])
        dd = (peak - de["equity"]) / peak
        max_dd = max(max_dd, dd)

    gross = total_carry + total_funding

    return {
        "venue": venue["name"],
        "initial": initial_capital,
        "leverage": target_leverage,
        "final": final_capital,
        "return_pct": total_return / initial_capital * 100,
        "ann_return": ann_return,
        "total_fees": total_fees,
        "carry": total_carry,
        "funding": total_funding,
        "gross": gross,
        "opens": opens,
        "closes": closes,
        "close_reasons": close_reasons,
        "deployed_days": deployed_days,
        "idle_days": idle_days,
        "deployed_pct": deployed_days / n_days * 100 if n_days > 0 else 0,
        "max_dd_pct": max_dd * 100,
        "events": events,
        "daily_equity": daily_equity_log,
        "daily_apy": daily_apy_log,
    }
