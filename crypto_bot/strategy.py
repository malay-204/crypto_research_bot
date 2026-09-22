"""Deterministic paper assessments. No forecast, orders, fills or balance mutation."""
import copy
import math
from .core import iso, parse_time
from .candles import candle_view
from .settings import validate_phase4

def baseline(rows, settings, round_trip_bps):
    closes = [r["close"] for r in rows]
    fast_n, slow_n = settings["fast_window"], settings["slow_window"]
    if len(closes) < slow_n:
        return {"state":"unavailable"}
    fast, slow = sum(closes[-fast_n:])/fast_n, sum(closes[-slow_n:])/slow_n
    gap = (fast/slow-1)*10000
    last = closes[-1]
    threshold = max(settings["buffer_bps"], round_trip_bps)
    # A trend gap is only a heuristic screening hurdle, not expected return.
    bullish = gap > threshold and last > fast
    bearish = gap < -settings["buffer_bps"] and last < fast
    return {"state":"bullish" if bullish else "bearish" if bearish else "neutral",
            "fast_sma":fast,"slow_sma":slow,"last_close":last,"trend_gap_bps":gap,
            "entry_threshold_bps":threshold,"round_trip_cost_bps":round_trip_bps}

def assess(db, report, asof):
    config = report["config"]
    validate_phase4(config)
    p = config["phase4"]
    portfolio = copy.deepcopy(p["portfolio"])
    risk, costs = p["risk"], p["costs"]
    assets = config["assets"]
    markets = {m["pair"]:m for m in report["markets"]}
    views = {a:candle_view(db,config,a,asof) for a in assets}
    global_blocks = []
    if portfolio["kind"] == "paper_snapshot":
        age = asof-parse_time(portfolio["as_of"])
        if age < 0 or age > risk["portfolio_max_age_seconds"]:
            global_blocks.append("Paper portfolio snapshot is future-dated or stale")
    positions = portfolio["positions"]
    quantities = {a:positions.get(a,{}).get("quantity",0) for a in assets}
    marks, rates = {}, {}
    for a in assets:
        m = markets.get(a)
        if m and m["data_status"] == "ok":
            marks[a] = (m["bid"]+m["ask"])/2
            spread = max(costs["spread_floor_bps"],m["spread_bps"])
            rates[a] = (costs["fee_bps_per_side"]+costs["slippage_bps_per_side"]+spread/2)/10000
        elif quantities[a] > 0:
            global_blocks.append(f"{a}: held position lacks a fresh usable mark; portfolio risk cannot be measured")
    values = {a:quantities[a]*marks.get(a,0) for a in assets}
    equity = portfolio["cash_usd"]+sum(values.values())
    if not math.isfinite(equity) or equity <= 0:
        global_blocks.append("Paper equity must be positive and finite")
    loss_pct = max(0,(1-equity/portfolio["reference_equity_usd"])*100) if not global_blocks else None
    # Reserve the cost of liquidating existing holdings before setting exposure ceilings.
    # This bounds equity loss from all proposed sells, including sells later in pair order.
    risk_equity = equity-sum(values[a]*rates.get(a,0) for a in assets)
    cap, pos_cap = risk["max_portfolio_exposure_pct"]/100,risk["max_position_pct"]/100
    initial_breach = bool(not global_blocks and (
        loss_pct >= risk["portfolio_loss_pause_pct"] or sum(values.values()) > cap*risk_equity+1e-8 or
        any(values[a] > pos_cap*risk_equity+1e-8 or (
            quantities[a] and marks[a] <= positions[a]["average_cost_usd"]*(1-risk["position_loss_exit_pct"]/100)
        ) for a in assets)))
    projected_qty = dict(quantities)
    projected_cash = portfolio["cash_usd"]
    # Entry reservations never spend expected sale proceeds or credit anticipated reductions.
    buy_cash, buy_equity, buy_values = portfolio["cash_usd"],risk_equity,dict(values)
    results = {}
    for a in assets:
        v, m = views[a],markets.get(a)
        cost_rate = rates.get(a)
        signal = baseline(v["rows"],p["strategy"],2*cost_rate*10000) if v["status"]=="ok" and cost_rate is not None else {"state":"unavailable"}
        supports, opposes = [],[]
        if signal["state"] != "unavailable":
            supports.append(f"Closed-candle SMA({p['strategy']['fast_window']})={signal['fast_sma']:.2f}; SMA({p['strategy']['slow_window']})={signal['slow_sma']:.2f}; gap={signal['trend_gap_bps']:.1f} bps.")
            opposes.append(f"Estimated round-trip friction is {signal['round_trip_cost_bps']:.1f} bps. The trend gap is not an expected return.")
            if signal["state"]=="bullish":
                supports.append("Fast average exceeds the slow average by the entry hurdle and the last close exceeds the fast average.")
                opposes.append("A lagging trend rule can enter near a reversal; this signal has no established predictive accuracy.")
            elif signal["state"]=="bearish":
                supports.append("Fast average is below the slow average by the buffer and the last close is below the fast average.")
                opposes.append("Exiting after a decline can miss a rebound; the trend rule does not forecast the next price.")
            else:
                supports.append("The complete entry or exit conditions are not satisfied.")
                opposes.append("Waiting or holding may miss a new trend between manual reviews.")
        else:
            supports.append("Usable closed candles and a fresh ticker are required for a numerical signal.")
            opposes.append("Missing or stale information cannot establish a neutral or safe market.")
        blocks = list(global_blocks)
        if not m or m["data_status"]!="ok":
            blocks.append("Ticker is missing, stale, invalid or over the spread limit")
        if v["status"]!="ok":
            blocks.append("Closed-candle data: "+v["status"])
        blocks += report["blocking_reasons"]+report.get("asset_blocks",{}).get(a,[])
        if a.split("-")[0] in report.get("news_gaps",[]):
            opposes.append("No recent source-linked news matched this coin; narrative context is unavailable (news never alters the numerical rule).")
        results[a] = {"pair":a,"action":"WAIT","reason":"Assessment inputs need review",
            "signal":signal,"supporting_evidence":supports,"opposing_evidence":opposes,
            "blocking_reasons":list(dict.fromkeys(blocks)),"proposed_trade":None,
            "current_quantity":quantities[a],"current_value_usd":values[a] if a in marks else None,
            "data_freshness":{"ticker_observed_at":iso(m["observed"]) if m else None,
                 "ticker_event_age_seconds":m["event_age_seconds"] if m else None,
                 "candle_close_at":v["latest_close"],"candle_close_age_seconds":v["close_age_seconds"],
                 "candle_status":v["status"]},
            "numerical_sources":[{"type":"ticker","fetch_id":m["fetch_id"],
                "url":f"https://api.exchange.coinbase.com/products/{a}/ticker"}] if m else [],
            "news_evidence_ids":[e["id"] for e in report["evidence"] if a.split("-")[0] in e["assets"]],
            "news_policy":"Unverified source statements for manual supporting/opposing review. No news sentiment, claim or similarity score enters the numerical strategy.",
            "invalidation":[
                "Any required input becomes stale, missing, invalid or is corrected.",
                "A new closed candle changes the moving-average conditions.",
                "Portfolio quantities, cost basis, cash or reference equity change.",
                "Spread, fees, slippage or exposure/loss assumptions change.",
                "Material source-linked news is independently verified and warrants a new manual review."],
            "uncertainty":"Unvalidated heuristic; no calibrated probability, expected-return estimate or profitability claim.",
            "one_way_cost_bps":cost_rate*10000 if cost_rate is not None else None}
        results[a]["numerical_sources"] += [{"type":"candles","url":v["source_url"],
            "batch_id":v["batch"]["id"] if v["batch"] else None,
            "candle_revision_ids":[r["id"] for r in v["rows"]]}]

    entry_order = list(assets)
    if config.get("research_universe"):
        # Scanner mode: scarce shared cash goes to the widest margin over the cost hurdle first.
        # This is an ordering heuristic, not a probability or expected-return ranking.
        margin = lambda a: (results[a]["signal"]["trend_gap_bps"]-results[a]["signal"]["entry_threshold_bps"]
                            if results[a]["signal"]["state"]!="unavailable" else float("-inf"))
        entry_order.sort(key=lambda a:(-margin(a),a))
        for a in assets:
            s = results[a]["signal"]
            results[a]["screen_margin_bps"] = s["trend_gap_bps"]-s["entry_threshold_bps"] if s["state"]!="unavailable" else None

    def proposal(a, side, quantity, why):
        nonlocal projected_cash
        mid, rate = marks[a],rates[a]
        notional = quantity*mid
        fee = notional*costs["fee_bps_per_side"]/10000
        slip = notional*costs["slippage_bps_per_side"]/10000
        half_spread = notional*(max(costs["spread_floor_bps"],markets[a]["spread_bps"])/2)/10000
        friction = fee+slip+half_spread
        delta = -(notional+friction) if side=="BUY" else notional-friction
        projected_cash += delta
        projected_qty[a] += quantity if side=="BUY" else -quantity
        return {"side":side,"quantity":quantity,"mid_notional_usd":notional,
            "fee_usd":fee,"half_spread_usd":half_spread,"slippage_usd":slip,
            "total_cost_usd":friction,"cash_change_usd":delta,
            "assumed_fill_price_usd":mid*(1+(rate-costs["fee_bps_per_side"]/10000)*(1 if side=="BUY" else -1)),
            "reason":why,"status":"proposal_only_not_filled"}

    # Risk exits take precedence and only require valid portfolio/ticker marks.
    # A bad candle/news feed must not masquerade as permission to increase risk.
    if not global_blocks:
        for a in assets:
            r = results[a]
            if quantities[a] <= 0 or a not in marks:
                continue
            current_values = {b:projected_qty[b]*marks.get(b,0) for b in assets}
            current_equity = projected_cash+sum(current_values.values())
            reason, sell_value = None,0
            if loss_pct >= risk["portfolio_loss_pause_pct"]:
                reason, sell_value = "Reference-equity loss limit reached; exit paper exposure and pause entries",current_values[a]
            elif marks[a] <= positions[a]["average_cost_usd"]*(1-risk["position_loss_exit_pct"]/100):
                reason, sell_value = "Position loss threshold reached against supplied average cost",current_values[a]
            else:
                position_excess = max(0,current_values[a]-pos_cap*risk_equity)
                total_excess = max(0,sum(current_values.values())-cap*risk_equity)
                if max(position_excess,total_excess)>1e-8:
                    reason = "Reduce to simulation exposure caps, allowing for estimated sale costs"
                    sell_value = min(current_values[a],max(position_excess,total_excess))
            if reason:
                qty = min(quantities[a],sell_value/marks[a])
                r.update(action="REDUCE/EXIT",reason=reason,proposed_trade=proposal(a,"SELL",qty,reason))
                r["supporting_evidence"].append(reason)
                r["opposing_evidence"].append("Loss thresholds are review triggers; gaps and worse fills can exceed the assumed loss.")
                continue
            if not r["blocking_reasons"] and r["signal"]["state"]=="bearish":
                reason = "Bearish closed-candle baseline with an existing paper holding"
                r.update(action="REDUCE/EXIT",reason=reason,proposed_trade=proposal(a,"SELL",quantities[a],reason))
            elif not r["blocking_reasons"]:
                r.update(action="HOLD",reason="Maintain existing paper holding; no baseline exit or risk trigger")
        for a in entry_order:
            r = results[a]
            if r["proposed_trade"] or r["blocking_reasons"] or r["signal"]["state"]!="bullish":
                if not r["blocking_reasons"] and not quantities[a] and r["signal"]["state"]!="bullish":
                    r["reason"]="No existing position and no complete bullish entry condition"
                continue
            if initial_breach:
                r["reason"]="Risk limit breached in the supplied portfolio; all new entries paused"
                r["action"]="HOLD" if quantities[a] else "WAIT"
                continue
            rate = rates[a]
            capacities = [buy_cash/(1+rate),buy_equity*risk["max_trade_pct"]/100,
                (pos_cap*buy_equity-buy_values[a])/(1+pos_cap*rate),
                (cap*buy_equity-sum(buy_values.values()))/(1+cap*rate)]
            # Fees shrink equity, which also tightens caps on other existing positions.
            if rate:
                capacities += [(pos_cap*buy_equity-buy_values[b])/(pos_cap*rate)
                               for b in assets if b!=a]
            nominal = max(0,min(capacities))
            qty = math.floor(nominal/marks[a]*1e8)/1e8
            nominal = qty*marks[a]
            if nominal < risk["min_trade_usd"]:
                r["action"]="HOLD" if quantities[a] else "WAIT"
                r["reason"]="Bullish screen, but shared cash/exposure room is below the minimum paper entry"
                continue
            reason = "Bullish baseline; proposed entry fits shared cash and simulation exposure caps after costs"
            r.update(action="BUY",reason=reason,proposed_trade=proposal(a,"BUY",qty,reason))
            buy_cash -= nominal*(1+rate)
            buy_equity -= nominal*rate
            buy_values[a] += nominal

    projected_values = {a:projected_qty[a]*marks.get(a,0) for a in assets}
    projected_equity = projected_cash+sum(projected_values.values())
    return {"status":"inputs_blocked" if any(r["blocking_reasons"] for r in results.values()) else "reviewable",
        "scope":"PAPER ASSESSMENTS ONLY — proposals do not change balances or execute orders",
        "assumptions_status":"PROVISIONAL SIMULATION ASSUMPTIONS — NOT APPROVED REAL-MONEY LIMITS",
        "strategy_name":"Closed-candle SMA trend baseline v1",
        "formula":f"SMA({p['strategy']['fast_window']}) vs SMA({p['strategy']['slow_window']}); BUY requires positive gap > max(buffer, estimated round-trip bps) and last close > fast SMA. Bearish exit requires gap < -buffer and last close < fast SMA. A trend gap is not a return forecast.",
        "allocation_order":entry_order,"allocation_note":("Scanner mode: entries reserve shared cash/exposure in descending margin over the entry hurdle (an ordering heuristic, not a probability);" if config.get("research_universe") else "Entries reserve shared cash/exposure in configured pair order;")+" expected sales do not fund entries. Exposure ceilings reserve liquidation costs of existing holdings. Entry trade cap does not limit risk exits.",
        "settings":p,"input_portfolio":portfolio,
        "portfolio":{"marks_usable":not global_blocks,"equity_usd":equity if not global_blocks else None,
            "equity_after_liquidation_cost_reserve_usd":risk_equity if not global_blocks else None,
            "exposure_pct":100*sum(values.values())/equity if not global_blocks else None,
            "reference_loss_pct":loss_pct,"new_entries_paused":initial_breach or bool(global_blocks),
            "blocking_reasons":global_blocks},
        "projected_if_all_proposals_filled":None if global_blocks else {
            "cash_usd":projected_cash,"quantities":projected_qty,"equity_usd":projected_equity,
            "exposure_pct":100*sum(projected_values.values())/projected_equity if projected_equity>0 else None,
            "note":"Illustrative cost calculation only; no fill engine or saved simulated balance."},
        "assessments":list(results.values()),"candle_data":list(views.values()),
        "limitations":["No chronological validation, cash/hold benchmark or proven predictive edge yet.",
            "No live paper fill history. A proposed portfolio is not a performance track record.",
            "Single-venue candles may be incomplete or revised; gaps are blocked, never forward-filled.",
            "Reference-equity loss is relative to the supplied scenario reference, not an automatically tracked high-water mark.",
            "Fees, half spread and slippage are explicit assumptions; depth, gaps and market impact can produce worse outcomes.",
            "The planned horizon is context only; this SMA rule does not forecast a 72-hour return.",
            "News evidence may support or oppose a narrative but remains unverified and does not alter the numerical rule."]}
