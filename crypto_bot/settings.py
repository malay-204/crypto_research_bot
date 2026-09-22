"""Phase 4 settings are explicit simulation assumptions, never live permissions."""
import copy
import math

DEFAULTS = {
    "candles": {"granularity_seconds": 3600, "lookback_candles": 720,
                "page_size": 300, "max_age_seconds": 7200},
    "strategy": {"fast_window": 24, "slow_window": 72, "buffer_bps": 25},
    "costs": {"fee_bps_per_side": 60, "spread_floor_bps": 10, "slippage_bps_per_side": 10},
    "risk": {"max_position_pct": 20, "max_portfolio_exposure_pct": 40,
             "max_trade_pct": 10, "position_loss_exit_pct": 8,
             "portfolio_loss_pause_pct": 10, "min_trade_usd": 10,
             "portfolio_max_age_seconds": 86400},
    "portfolio": {"kind": "hypothetical_scenario", "cash_usd": 10000,
                  "reference_equity_usd": 10000, "positions": {}}
}

def number(value, name, low=0, high=1e15, integer=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"{name} outside permitted range")
    return value

def validate_portfolio(p, assets, discoverable=False):
    if not isinstance(p, dict) or p.get("kind") not in ("hypothetical_scenario", "paper_snapshot"):
        raise ValueError("Portfolio must be a hypothetical_scenario or paper_snapshot")
    number(p.get("cash_usd"), "cash_usd", high=1e12)
    number(p.get("reference_equity_usd"), "reference_equity_usd", low=0.01, high=1e12)
    if p["kind"] == "paper_snapshot":
        from .core import parse_time
        if parse_time(p.get("as_of")) is None:
            raise ValueError("Paper snapshot needs timezone-aware as_of")
    positions = p.get("positions")
    from .universe import valid_pair
    # Scanner configs discover their universe later; held pairs are then forced into it.
    if not isinstance(positions, dict) or any(a not in assets and not (discoverable and valid_pair(a)) for a in positions):
        raise ValueError("Portfolio contains unsupported/unpriced positions")
    for pair, pos in positions.items():
        if not isinstance(pos, dict):
            raise ValueError("Each position needs quantity and average_cost_usd")
        number(pos.get("quantity"), pair + " quantity", high=1e9)
        number(pos.get("average_cost_usd"), pair + " average_cost_usd", low=0.00000001, high=1e9)
    return p

def validate_phase4(c, discoverable=False):
    """discoverable=True only for the scanner, whose universe (and held pairs) is discovered later."""
    p = c.get("phase4")
    if p is None:  # Historical v1 configuration snapshots remain readable.
        return
    if not isinstance(p, dict) or p.get("assumptions") != "provisional_simulation_only":
        raise ValueError("Phase 4 assumptions must be provisional_simulation_only")
    for key in DEFAULTS:
        if not isinstance(p.get(key), dict):
            raise ValueError(f"Missing phase4.{key}")
    candles, strategy, risk, costs = (p[k] for k in ("candles", "strategy", "risk", "costs"))
    g = number(candles.get("granularity_seconds"), "granularity", 60, 86400, True)
    if g not in (60,300,900,3600,21600,86400):
        raise ValueError("Unsupported Coinbase candle granularity")
    n = number(candles.get("lookback_candles"), "lookback_candles", 3, 10000, True)
    number(candles.get("page_size"), "page_size", 1, 300, True)
    number(candles.get("max_age_seconds"), "candle max age", g, g*3)
    fast = number(strategy.get("fast_window"), "fast_window", 2, n-1, True)
    number(strategy.get("slow_window"), "slow_window", fast+1, n, True)
    number(strategy.get("buffer_bps"), "buffer_bps", 0, 1000)
    for k in DEFAULTS["costs"]:
        number(costs.get(k), k, 0, 1000)
    for k in ("max_position_pct", "max_portfolio_exposure_pct", "max_trade_pct",
              "position_loss_exit_pct", "portfolio_loss_pause_pct"):
        number(risk.get(k), k, 0.01, 100)
    if risk["max_position_pct"] > risk["max_portfolio_exposure_pct"]:
        raise ValueError("Position cap cannot exceed portfolio cap")
    number(risk.get("min_trade_usd"), "min_trade_usd", 0.01, 1e9)
    number(risk.get("portfolio_max_age_seconds"), "portfolio_max_age_seconds", 1, 604800)
    validate_portfolio(p["portfolio"], c["assets"], discoverable)

def defaults():
    return dict(copy.deepcopy(DEFAULTS), assumptions="provisional_simulation_only")
