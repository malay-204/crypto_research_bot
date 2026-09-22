"""Self-contained, auto-refreshing scanner dashboard. All external text is escaped; no scripts load from the web."""
import html
import json
from .core import iso

CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--ink:#16181d;--muted:#5d6470;--line:#dfe2e7;--accent:#2f5bd3;
--fast:#c05a00;--slow:#6b4fbb;--vol:#c9ced8;--buy:#136f3a;--buybg:#e3f4e9;--warn:#8a5a00;--warnbg:#fdf1d8;
--bad:#a3262a;--badbg:#fbe5e5;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#101216;--panel:#181b21;--ink:#e8eaee;
--muted:#9aa2ae;--line:#2b3039;--accent:#7ea2ff;--fast:#f29b4b;--slow:#b39cf2;--vol:#3a404b;--buy:#6fd49a;
--buybg:#123222;--warn:#f0c46a;--warnbg:#3a2e12;--bad:#ff8a8a;--badbg:#3b1717;--chip:#232730}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 48px}h1{font-size:22px;margin:0}h2{font-size:16px;margin:28px 0 10px}
h3{font-size:15px;margin:0}a{color:var(--accent)}.muted{color:var(--muted)}.small{font-size:12px}
.top{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;justify-content:space-between}
.banner{margin:14px 0;padding:10px 12px;border-radius:8px;background:var(--warnbg);color:var(--warn);font-weight:600}
.stale{display:none;background:var(--badbg);color:var(--bad)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
.tile b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle;white-space:nowrap}
th{font-size:12px;color:var(--muted);font-weight:600}td.wrap{white-space:normal;min-width:220px}
.num{text-align:right}.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:700;background:var(--chip)}
.BUY{background:var(--buybg);color:var(--buy)}.REDUCE{background:var(--badbg);color:var(--bad)}.BLOCKED{background:var(--warnbg);color:var(--warn)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.card ul{margin:6px 0 0;padding-left:18px}.card li{margin:2px 0}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:560px){.cols{grid-template-columns:1fr}}
.legend{display:flex;gap:12px;font-size:12px;color:var(--muted);margin:4px 0}
.legend i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:4px}
svg{display:block;width:100%;height:auto}.events li{margin:4px 0}.tag{font-size:11px;color:var(--muted);margin-right:6px}
details summary{cursor:pointer;color:var(--muted);margin-top:8px}
"""

def e(v):
    return html.escape(str(v if v is not None else "—"), quote=True)

def money(v):
    if v is None:
        return "—"
    return f"${v:,.2f}" if v >= 1 else f"${v:,.6f}"

def compact(v):
    if v is None:
        return "—"
    for size, unit in ((1e9,"B"),(1e6,"M"),(1e3,"K")):
        if v >= size:
            return f"${v/size:,.1f}{unit}"
    return f"${v:,.0f}"

def pct(v, digits=2):
    return "—" if v is None else f"{v:+.{digits}f}%"

def sma(values, n):
    return [None if i+1 < n else sum(values[i+1-n:i+1])/n for i in range(len(values))]

def path(values, lo, hi, w, h, pad=4):
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2 or hi <= lo:
        return ""
    step = (w-2*pad)/max(1, len(values)-1)
    return " ".join(f"{pad+i*step:.1f},{pad+(h-2*pad)*(1-(v-lo)/(hi-lo)):.1f}" for i, v in pts)

def sparkline(rows, label):
    closes = [r["close"] for r in rows]
    if len(closes) < 2:
        return '<span class="muted small">no chart</span>'
    lo, hi = min(closes), max(closes)
    return (f'<svg viewBox="0 0 120 32" width="120" height="32" role="img" aria-label="{e(label)}" preserveAspectRatio="none">'
            f'<polyline fill="none" stroke="var(--accent)" stroke-width="1.5" points="{path(closes, lo, hi, 120, 32, 2)}"/></svg>')

def chart(rows, fast, slow, label):
    """Hourly closes with SMA overlays and volume bars. Candles before a window fills show no SMA."""
    if len(rows) < 2:
        return '<p class="muted">Not enough closed candles to draw a chart yet.</p>'
    w, h, vh = 600, 170, 40
    closes = [r["close"] for r in rows]
    f, s = sma(closes, fast), sma(closes, slow)
    everything = closes+[v for v in f+s if v is not None]
    lo, hi = min(everything), max(everything)
    pad = max((hi-lo)*0.05, abs(hi)*0.005)
    lo, hi = lo-pad, hi+pad
    vmax = max(r["volume"] for r in rows) or 1
    bw = (w-8)/len(rows)
    bars = "".join(f'<rect x="{4+i*bw:.1f}" y="{h+vh-r["volume"]/vmax*vh:.1f}" width="{max(bw-0.6,0.4):.1f}" '
                   f'height="{r["volume"]/vmax*vh:.1f}" fill="var(--vol)"/>' for i, r in enumerate(rows))
    lines = "".join(f'<polyline fill="none" stroke="{c}" stroke-width="{sw}" points="{path(v, lo, hi, w, h)}"/>'
                    for v, c, sw in ((closes,"var(--accent)",1.8),(f,"var(--fast)",1.2),(s,"var(--slow)",1.2)))
    text = (f'<text x="{w-4}" y="15" text-anchor="end" font-size="15" fill="var(--muted)">{e(money(hi))}</text>'
            f'<text x="{w-4}" y="{h-4}" text-anchor="end" font-size="15" fill="var(--muted)">{e(money(lo))}</text>'
            f'<text x="4" y="{h+vh+17}" font-size="15" fill="var(--muted)">{e(iso(rows[0]["start"])[:16])}Z</text>'
            f'<text x="{w-4}" y="{h+vh+17}" text-anchor="end" font-size="15" fill="var(--muted)">close {e(iso(rows[-1]["start"]+3600)[:16])}Z</text>')
    return (f'<svg viewBox="0 0 {w} {h+vh+22}" role="img" aria-label="{e(label)}">'
            f'<line x1="0" x2="{w}" y1="{h}" y2="{h}" stroke="var(--line)"/>{bars}{lines}{text}</svg>'
            f'<div class="legend"><span><i style="background:var(--accent)"></i>close</span>'
            f'<span><i style="background:var(--fast)"></i>SMA {fast}</span><span><i style="background:var(--slow)"></i>SMA {slow}</span>'
            f'<span><i style="background:var(--vol);height:8px"></i>volume</span></div>')

def badge(a):
    cls = {"BUY":"BUY","REDUCE/EXIT":"REDUCE"}.get(a["action"], "")
    if a["action"] == "WAIT" and a["blocking_reasons"]:
        cls = "BLOCKED"
    return f'<span class="badge {cls}">{e(a["action"])}</span>'

def items(values):
    return "<ul>"+"".join("<li>"+e(v)+"</li>" for v in values)+"</ul>" if values else '<p class="muted">None.</p>'

def render_dashboard(r):
    p4, sc = r["phase4"], r["scanner"]
    strat = p4["settings"]["strategy"]
    universe = r["config"]["research_universe"]
    members = {m["pair"]:m for m in universe["members"]}
    markets = {m["pair"]:m for m in r["markets"]}
    by_pair = {a["pair"]:a for a in p4["assessments"]}
    evidence = {x["id"]:x for x in r["evidence"]}
    ranked = [by_pair[pair] for pair in sc["ranking"]]
    buys = [a for a in ranked if a["action"] == "BUY"]
    bullish = [a for a in ranked if a["signal"]["state"] == "bullish"]
    blocked = [a for a in ranked if a["blocking_reasons"]]
    healthy = sum(h["status"] == "ok" for h in r["health"])
    demo = r["mode"] == "demo"
    refresh = int(sc.get("page_refresh_seconds", sc["refresh_seconds"]))
    cadence = (f"updated by a scheduled GitHub Actions run about every {refresh//60} min (GitHub can delay runs)"
               if sc.get("hosted") else f"refreshes every {refresh} s while the scanner runs")
    out = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<meta http-equiv="refresh" content="{min(refresh, 300)}"><title>Live Crypto Scanner</title><style>{CSS}</style></head><body><main>',
        f'<div class="top"><div><h1>Live crypto scanner{" — SYNTHETIC DEMO" if demo else ""}</h1>',
        f'<div class="muted">Updated {e(r["as_of"])} · {e(cadence)} · <span id="age"></span></div></div>',
        f'<div><span class="badge">{e(r["mode"].upper())}</span> <span class="badge BLOCKED">PAPER ONLY · EXECUTION DISABLED</span></div></div>',
        f'<div class="banner stale" id="stale">This page has not been updated for several refresh intervals. The scanner may have stopped; figures below are outdated.</div>',
        '<div class="banner">Research and paper assessments only. Rankings and actions come from an unvalidated moving-average rule with provisional cost and risk assumptions. They are not predictions, probabilities or financial advice, and no profitability is implied.</div>']
    if demo:
        out.append('<div class="banner">Every price, market and headline on this page is synthetic test data.</div>')
    if universe.get("discovery_error"):
        out.append(f'<div class="banner">Latest market discovery failed ({e(universe["discovery_error"])}); showing the last successfully observed universe.</div>')
    out += ['<div class="tiles">',
        f'<div class="tile"><span class="muted">Markets tracked</span><b>{len(ranked)}</b><span class="small muted">of {e(universe["liquid_count"])} liquid / {e(universe["eligible_active_count"])} eligible</span></div>',
        f'<div class="tile"><span class="muted">Paper BUY proposals</span><b>{len(buys)}</b><span class="small muted">limited by shared cash and caps</span></div>',
        f'<div class="tile"><span class="muted">Bullish screens</span><b>{len(bullish)}</b><span class="small muted">trend gap above cost hurdle</span></div>',
        f'<div class="tile"><span class="muted">Blocked inputs</span><b>{len(blocked)}</b><span class="small muted">stale, missing or wide-spread data</span></div>',
        f'<div class="tile"><span class="muted">Sources healthy</span><b>{healthy}/{len(r["health"])}</b><span class="small muted">research status: {e(r["research_status"])}</span></div>',
        '</div>']
    if r["blocking_reasons"]:
        out.append('<div class="banner">Global gates (all new entries blocked): '+e("; ".join(r["blocking_reasons"]))+'</div>')

    out.append('<h2>Ranked paper screen</h2><p class="muted small">'+e(sc["ranking_note"])+'</p><div class="panel scroll"><table><thead><tr>'
        '<th>#</th><th>Market</th><th>7-day hourly</th><th class="num">Price</th><th class="num">24h</th><th class="num">24h turnover≈</th>'
        '<th class="num">Spread</th><th class="num">Trend gap</th><th class="num">Hurdle</th><th class="num">Margin</th><th>Paper action</th><th class="num">News</th></tr></thead><tbody>')
    for i, a in enumerate(ranked, 1):
        m, u, s = markets.get(a["pair"], {}), members.get(a["pair"], {}), a["signal"]
        ok = s["state"] != "unavailable"
        spread = f'{m["spread_bps"]:.1f} bps' if m else None
        gap = f'{s["trend_gap_bps"]:+.0f} bps' if ok else None
        hurdle = f'{s["entry_threshold_bps"]:.0f} bps' if ok else None
        margin = f'{a["screen_margin_bps"]:+.0f} bps' if a.get("screen_margin_bps") is not None else None
        out.append(f'<tr><td>{i}</td><td><a href="#coin-{e(a["pair"])}"><b>{e(a["pair"])}</b></a><div class="small muted">{e(u.get("name"))}</div></td>'
            f'<td>{sparkline(sc["charts"].get(a["pair"], []), a["pair"]+" 7-day closes")}</td>'
            f'<td class="num">{e(money(m.get("price")))}</td><td class="num">{e(pct(u.get("stats_change_24h_pct")))}</td>'
            f'<td class="num">{e(compact(u.get("approx_usd_volume_24h")))}</td>'
            f'<td class="num">{e(spread)}</td><td class="num">{e(gap)}</td><td class="num">{e(hurdle)}</td><td class="num">{e(margin)}</td>'
            f'<td>{badge(a)}<div class="small muted" style="white-space:normal;min-width:210px;max-width:260px">{e(a["reason"])}</div></td>'
            f'<td class="num">{len(a["news_evidence_ids"])}</td></tr>')
    out.append('</tbody></table></div>')

    out.append('<h2>Changes since earlier refreshes</h2><div class="panel"><ul class="events">')
    for ev in sc["events"][:20]:
        out.append(f'<li><span class="tag">{e(ev["observed_at"][11:19])}Z · {e(ev["kind"])}</span>{e(ev["message"])}</li>')
    out.append('</ul></div>' if sc["events"] else '<li class="muted">No changes recorded yet.</li></ul></div>')

    focus = ranked[:6]+[a for a in ranked[6:] if a["current_quantity"] or a["action"] == "REDUCE/EXIT"]
    out.append(f'<h2>Details: top {min(6,len(ranked))} and any paper holdings</h2><div class="cards">')
    for a in focus:
        u = members.get(a["pair"], {})
        out.append(f'<article class="card" id="coin-{e(a["pair"])}"><div class="top"><h3>{e(a["pair"])} <span class="muted small">{e(u.get("name"))}</span></h3>{badge(a)}</div>'
            f'<p><b>{e(a["reason"])}</b></p>{chart(sc["charts"].get(a["pair"], []), strat["fast_window"], strat["slow_window"], a["pair"]+" hourly closes with moving averages")}')
        if a["proposed_trade"]:
            t = a["proposed_trade"]
            out.append(f'<p class="small">Proposal only (not filled): {e(t["side"])} {t["quantity"]:.8f} ≈ {e(money(t["mid_notional_usd"]))}; '
                       f'estimated costs {e(money(t["total_cost_usd"]))} (fee {e(money(t["fee_usd"]))}, half spread {e(money(t["half_spread_usd"]))}, slippage {e(money(t["slippage_usd"]))}).</p>')
        out.append('<div class="cols"><div><b class="small">Supporting</b>'+items(a["supporting_evidence"])+'</div>'
                   '<div><b class="small">Opposing / uncertainty</b>'+items(a["opposing_evidence"])+'</div></div>')
        if a["blocking_reasons"]:
            out.append('<b class="small">Data gates</b>'+items(a["blocking_reasons"]))
        f = a["data_freshness"]
        out.append(f'<p class="small muted">Ticker observed {e(f["ticker_observed_at"])} · latest candle close {e(f["candle_close_at"])} · candles {e(f["candle_status"])}</p>')
        news = [evidence[i] for i in a["news_evidence_ids"] if i in evidence]
        news.sort(key=lambda x: -x["published"])
        out.append('<b class="small">Unverified linked news (manual review; never enters the rule)</b>')
        out.append("<ul>"+"".join(f'<li><a href="{e(x["url"])}" target="_blank" rel="noopener noreferrer">{e(x["title"])}</a> '
                   f'<span class="tag">{e(x["source"])} · {e(iso(x["published"])[:16])}Z</span></li>' for x in news[:5])+"</ul>"
                   if news else '<p class="muted small">No recent matched items.</p>')
        out.append('<details><summary>What would invalidate this</summary>'+items(a["invalidation"])+'</details></article>')
    out.append('</div>')

    latest = sorted(r["evidence"], key=lambda x: -x["published"])[:15]
    out.append('<h2>Latest matched news (unverified source statements)</h2><div class="panel"><ul class="events">')
    out += [f'<li><span class="tag">{e(", ".join(x["assets"]))} · {e(x["source"])} · {e(iso(x["published"])[:16])}Z</span>'
            f'<a href="{e(x["url"])}" target="_blank" rel="noopener noreferrer">{e(x["title"])}</a>'
            + (f' <span class="tag">flags: {e(", ".join(x["review_flags"]))}</span>' if x["review_flags"] else "")+'</li>' for x in latest]
    out.append('</ul></div>')

    port = p4["portfolio"]
    out.append('<h2>Paper portfolio and assumptions</h2><div class="panel small">'
        f'<p>{e(p4["assumptions_status"])}. Input: {e(p4["input_portfolio"]["kind"])}, cash {e(money(p4["input_portfolio"]["cash_usd"]))}, '
        f'marked equity {e(money(port["equity_usd"]))}, new entries paused: {e(port["new_entries_paused"])}. Proposals never change this balance.</p>'
        f'<p>{e(p4["formula"])}</p><p>{e(p4["allocation_note"])}</p>'
        f'<p>Costs per side: fee {e(p4["settings"]["costs"]["fee_bps_per_side"])} bps, slippage {e(p4["settings"]["costs"]["slippage_bps_per_side"])} bps, plus half of max(observed spread, {e(p4["settings"]["costs"]["spread_floor_bps"])} bps floor). '
        f'Caps: position {e(p4["settings"]["risk"]["max_position_pct"])}%, portfolio {e(p4["settings"]["risk"]["max_portfolio_exposure_pct"])}%, single entry {e(p4["settings"]["risk"]["max_trade_pct"])}% of equity.</p></div>')

    out.append('<h2>Sources</h2><div class="panel scroll"><table><thead><tr><th>Source</th><th>Status</th><th>Last poll (UTC)</th><th class="num">Age (s)</th><th>Detail</th></tr></thead><tbody>')
    out += [f'<tr><td>{e(h["source"])}</td><td>{e(h["status"])}</td><td>{e(h["last_poll"])}</td><td class="num">{e(h["age_seconds"])}</td>'
            f'<td class="wrap">{e(h["error"] or ("required" if h["required"] else "optional"))}</td></tr>' for h in r["health"]]
    out.append('</tbody></table></div>')
    out.append('<h2>Coverage and limitations</h2><div class="panel small">'
        f'<p><b>Universe:</b> {e(universe["scope"])}. {e(universe["selection"])} Minimum ≈{e(compact(r["config"]["scanner"]["min_approx_usd_volume_24h"]))} daily turnover; up to {e(r["config"]["scanner"]["max_assets"])} markets. '
        f'Discovered {e(universe["observed_at"])}.</p>'+items(universe["limitations"]+r["limitations"])+'</div>')
    out.append(f'<p class="small muted">Full audit JSON for this refresh: <a href="state.json">state.json</a>. Hourly archived reports are kept under reports/scanner/.</p>')
    out.append('<script>(function(){var t=Date.parse(%s),r=%d;function u(){var s=Math.max(0,Math.round((Date.now()-t)/1000));'
        'document.getElementById("age").textContent="page age "+(s<120?s+" s":Math.round(s/60)+" min");if(s>3*r+30)document.getElementById("stale").style.display="block";}u();setInterval(u,1000);})();</script>'
        % (json.dumps(r["as_of"]), refresh))
    out.append('</main></body></html>')
    return "".join(out)
