"""Escaped, offline presentation of paper proposals and their evidence."""
import html
from .core import iso

def section(p):
    e = lambda v: html.escape(str(v if v is not None else "Unavailable"),quote=True)
    money = lambda v: "Unavailable" if v is None else f"${v:,.2f}"
    def items(values):
        return "<ul>"+"".join("<li>"+e(v)+"</li>" for v in values)+"</ul>"
    def table(headers, rows):
        return '<div class="scroll"><table><thead><tr>'+"".join("<th>"+e(h)+"</th>" for h in headers)+"</tr></thead><tbody>"+"".join("<tr>"+"".join("<td>"+e(v)+"</td>" for v in row)+"</tr>" for row in rows)+"</tbody></table></div>"
    config = p["settings"]
    risk, costs = config["risk"],config["costs"]
    cash = p["input_portfolio"]["cash_usd"]
    status = p["portfolio"]
    output = ['<section id="assessments"><h2>Paper decision review</h2>',
        '<div class="demo">'+e(p["assumptions_status"])+'</div>',
        '<p><strong>Assessment input status: '+e(p["status"])+'</strong>. Freshness checks describe this snapshot at its UTC as-of time; refresh before another review.</p>',
        "<p>"+e(p["scope"])+". No profitability or validated probability is claimed.</p>",
        '<p class="note">Input portfolio: '+e(p["input_portfolio"]["kind"])+
        '. The default $10,000 cash scenario is hypothetical. Proposals never update its balance.</p>',
        table(["Input cash","Marked equity","Exposure","Reference-equity loss","New entries paused"],[
            [money(cash),money(status["equity_usd"]),
             f'{status["exposure_pct"]:.2f}%' if status["exposure_pct"] is not None else None,
             f'{status["reference_loss_pct"]:.2f}%' if status["reference_loss_pct"] is not None else None,
             status["new_entries_paused"]]]),
        "<p><strong>"+e(p["strategy_name"])+"</strong>: "+e(p["formula"])+"</p>",
        '<p class="note">'+e(p["allocation_note"])+"</p>"]
    for a in p["assessments"]:
        output += ['<article class="assessment"><div class="meta">'+e(a["pair"])+' · PAPER ONLY</div>',
            '<h3><span class="badge">'+e(a["action"])+"</span></h3><p><strong>"+e(a["reason"])+"</strong></p>",
            '<p class="note">'+e(a["uncertainty"])+"</p>"]
        if a["proposed_trade"]:
            t=a["proposed_trade"]
            output += [table(["Proposed side","Quantity","Mid notional","Fee","Half spread","Slippage","Cash change"],[
                [t["side"],f'{t["quantity"]:.8f}',money(t["mid_notional_usd"]),money(t["fee_usd"]),
                 money(t["half_spread_usd"]),money(t["slippage_usd"]),money(t["cash_change_usd"])]]),
                 '<p class="note">Illustrative assumed fill price: '+money(t["assumed_fill_price_usd"])+
                 ". Proposal only; no fill, order or balance update occurred.</p>"]
        output += ['<div class="evidence-columns"><div><h4>Supporting numerical evidence</h4>'+items(a["supporting_evidence"])+"</div>",
            "<div><h4>Opposing evidence and uncertainty</h4>"+items(a["opposing_evidence"])+"</div></div>"]
        if a["blocking_reasons"]:
            output += ["<h4>Data limitations / gates</h4>"+items(a["blocking_reasons"])]
        f=a["data_freshness"]
        output += [table(["Ticker observed (UTC)","Trade age (seconds)","Latest candle close (UTC)","Close age (seconds)","Candle quality"],[
            [f["ticker_observed_at"],f["ticker_event_age_seconds"],f["candle_close_at"],
             round(f["candle_close_age_seconds"],1) if f["candle_close_age_seconds"] is not None else None,f["candle_status"]]]),
            '<p class="note">Numerical sources: '+
                " · ".join('<a href="'+e(s["url"])+'" target="_blank" rel="noopener noreferrer">'+e(s["type"])+"</a>"
                           for s in a["numerical_sources"])+
                ". Fetch, batch and candle revision IDs are retained in the matching JSON.</p>",
            "<h4>Unverified news for manual review</h4><p>"+e(a["news_policy"])+"</p><p>"+
                (" · ".join('<a href="#evidence-'+str(i)+'">Evidence #'+str(i)+"</a>" for i in a["news_evidence_ids"]) or "No eligible linked claims.")+"</p>",
            "<details><summary>What would invalidate this assessment?</summary>"+items(a["invalidation"])+"</details></article>"]
    candles=[]
    for v in p["candle_data"]:
        b=v["batch"]
        candles.append([v["pair"],v["status"],b["received_count"] if b else None,
             b["expected"] if b else None,b["missing_count"] if b else None,
             iso(b["start"]) if b else None,iso(b["end"]) if b else None,
             b["excluded_count"] if b else None])
    output += ["<h3>Historical candle collection</h3>",
        '<p class="note">Ranges are half-open UTC intervals. Open candles and extra boundary rows are excluded. Gaps are never filled. A successful earlier batch cannot hide a failed newer batch.</p>',
        table(["Pair","State","Received","Expected","Missing","Start UTC","End UTC","Excluded"],candles),
        "<details><summary>Simulation costs and risk limits</summary>",
        table(["Cost assumption","Value (basis points)"],[
            ["Fee per side",costs["fee_bps_per_side"]],["Full spread floor",costs["spread_floor_bps"]],
            ["Slippage per side",costs["slippage_bps_per_side"]]]),
        "<p>One-way cost = fee + slippage + half of max(observed spread, spread floor). Costs apply on both entry and exit; fees use mid notional. This is an approximation, not a venue fee quote.</p>",
        table(["Risk assumption","Value"],list(risk.items())),
        "<p>Position loss uses the supplied average cost. Portfolio loss uses the supplied reference equity; no automatic high-water mark is tracked. Thresholds do not guarantee a maximum realized loss.</p></details>"]
    projected=p["projected_if_all_proposals_filled"]
    if projected:
        output += ["<details><summary>Illustrative portfolio if all proposals filled at assumed costs</summary>",
            table(["Cash","Marked equity after costs","Exposure"],[[money(projected["cash_usd"]),
                money(projected["equity_usd"]),f'{projected["exposure_pct"]:.2f}%']]),
            "<p>"+e(projected["note"])+"</p></details>"]
    output += ["</section>"]
    return "".join(output)
