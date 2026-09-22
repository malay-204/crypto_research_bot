"""Foreground live scanner: polls due public sources, re-assesses, logs changes, redraws the dashboard.

Paper-only. Nothing here places orders, signs requests or runs after the terminal is closed.
"""
import copy
import json
import math
import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .core import iso, utcnow
from .pipeline import collect, download, snapshot
from .universe import DEFAULT_SCANNER, discover

CHART_CANDLES = 168


def throttled(transport, interval, sleep=time.sleep, clock=time.monotonic):
    """Space requests to stay well inside Coinbase public rate limits."""
    last = [None]
    def call(url, config):
        if last[0] is not None:
            wait = interval-(clock()-last[0])
            if wait > 0:
                sleep(wait)
        try:
            return transport(url, config)
        finally:
            last[0] = clock()
    return call


def scanner_settings(config):
    return dict(copy.deepcopy(DEFAULT_SCANNER), **config.get("scanner", {}))


def due_feeds(db, runtime, now):
    every = runtime["scanner"]["feed_refresh_seconds"]
    due = []
    for feed in runtime["feeds"]:
        row = db.execute("SELECT received FROM fetches WHERE source=? ORDER BY received DESC,id DESC LIMIT 1",
                         (f"feed:{feed['id']}",)).fetchone()
        if row is None or now-row["received"] >= every:
            due.append(feed)
    return due


def due_candles(db, runtime, now):
    """A pair is due when a newer closed candle exists, or a failed batch has waited out the retry delay."""
    g = runtime["phase4"]["candles"]["granularity_seconds"]
    cutoff = int(now//g)*g
    retry = runtime["scanner"]["candle_retry_seconds"]
    due = []
    for pair in runtime["assets"]:
        b = db.execute("""SELECT * FROM candle_batches WHERE pair=? AND granularity=?
            ORDER BY started DESC,id DESC LIMIT 1""", (pair, g)).fetchone()
        if b is None or b["end"] < cutoff:
            due.append(pair)
        elif b["status"] != "ok" and now-b["started"] >= retry:
            due.append(pair)
    return due


def chart_series(db, runtime, pair, asof, count=CHART_CANDLES):
    """Latest revision of each closed candle that was observable at asof (no look-ahead)."""
    g = runtime["phase4"]["candles"]["granularity_seconds"]
    cutoff = int(asof//g)*g
    rows = db.execute("""SELECT start,open,high,low,close,volume,observed FROM candles
        WHERE pair=? AND granularity=? AND observed<=? AND start>=? AND start+granularity<=?
        ORDER BY start ASC,observed DESC,id DESC""", (pair, g, asof, cutoff-count*g, asof)).fetchall()
    seen = {}
    for r in rows:
        seen.setdefault(r["start"], dict(r))
    return [seen[k] for k in sorted(seen)]


def rank_key(a):
    order = {"BUY":0, "HOLD":2, "REDUCE/EXIT":3, "WAIT":4}
    bullish_unfunded = a["action"] in ("WAIT","HOLD") and a["signal"]["state"] == "bullish" and not a["blocking_reasons"]
    group = 1 if bullish_unfunded else order.get(a["action"], 5)
    if a["action"] == "WAIT" and a["blocking_reasons"]:
        group = 6
    margin = a.get("screen_margin_bps")
    return (group, -(margin if margin is not None else -1e18), a["pair"])


def log_event(db, now, pair, kind, message, detail=None):
    db.execute("INSERT INTO scanner_events(observed,pair,kind,message,detail) VALUES (?,?,?,?,?)",
               (now, pair, kind, message, json.dumps(detail or {}, sort_keys=True)))


def diff_events(db, previous, report, now):
    """Compare this cycle with the previous one and store human-readable change notes."""
    p4 = report["phase4"]
    current = {a["pair"]:a for a in p4["assessments"]}
    if previous is None:
        log_event(db, now, None, "started", f"Scanner cycle started with {len(current)} markets")
    else:
        before = previous["actions"]
        for pair in sorted(set(current)-set(before)):
            log_event(db, now, pair, "universe", f"{pair} entered the tracked universe")
        for pair in sorted(set(before)-set(current)):
            log_event(db, now, pair, "universe", f"{pair} left the tracked universe")
        for pair, a in current.items():
            if pair in before and before[pair] != a["action"]:
                log_event(db, now, pair, "action", f"{pair}: paper assessment {before[pair]} → {a['action']}",
                          {"reason": a["reason"]})
        for h in report["health"]:
            old = previous["health"].get(h["source"])
            if old is not None and old != h["status"]:
                log_event(db, now, None, "source", f"{h['source']}: {old} → {h['status']}", {"error": h["error"]})
        # URLs, not row IDs, so hosted runs that start from a fresh database do not re-announce old news.
        new_urls = {e["url"] for e in report["evidence"]}-set(previous["evidence"])
        new_ids = [x["id"] for x in report["evidence"] if x["url"] in new_urls]
        for e in [x for x in report["evidence"] if x["url"] in new_urls][:10]:
            log_event(db, now, None, "news", f"New unverified item ({', '.join(e['assets'])}): {e['title'][:160]}",
                      {"url": e["url"], "evidence_id": e["id"]})
        if len(new_ids) > 10:
            log_event(db, now, None, "news", f"{len(new_ids)-10} more new news items")
    db.commit()
    return {"actions": {k:v["action"] for k,v in current.items()},
            "health": {h["source"]:h["status"] for h in report["health"]},
            "evidence": sorted({e["url"] for e in report["evidence"]})}


def recent_events(db, limit=25):
    return [dict(r, observed_at=iso(r["observed"])) for r in db.execute(
        "SELECT * FROM scanner_events ORDER BY observed DESC,id DESC LIMIT ?", (limit,))]


def cycle(db, config, state, transport=download, clock=utcnow):
    """One scan: discover (cached hourly), poll tickers plus due feeds/candles, then assess."""
    now = clock()
    try:
        runtime = discover(db, config, transport, clock)
        state["runtime"] = runtime
    except (ValueError, TypeError, KeyError, OSError) as exc:
        log_event(db, now, None, "source", f"Market discovery failed: {str(exc)[:200]}")
        db.commit()
        if not state.get("runtime"):
            raise ValueError(f"Market discovery failed and no earlier universe exists: {exc}")
        runtime = state["runtime"]
        runtime["research_universe"]["discovery_error"] = str(exc)[:300]
    feeds = due_feeds(db, runtime, now)
    pairs = due_candles(db, runtime, now)
    summary = collect(db, runtime, transport=transport, clock=clock, with_candles=bool(pairs),
                      feeds=feeds, candle_pairs=pairs)
    asof = clock()
    report = snapshot(db, runtime, asof)
    report["scanner"] = {"refresh_seconds": runtime["scanner"]["refresh_seconds"],
        "polled": {"tickers": len(runtime["assets"]), "feeds": [f["id"] for f in feeds], "candles": pairs},
        "failures": [s for s in summary if not s["ok"]],
        "charts": {a: chart_series(db, runtime, a, asof) for a in runtime["assets"]},
        "ranking": [a["pair"] for a in sorted(report["phase4"]["assessments"], key=rank_key)],
        "ranking_note": "Paper BUY proposals first, then unfunded bullish screens, holdings, exits, neutral waits and blocked inputs; within a group by margin over the cost-adjusted entry hurdle. An ordering heuristic, not a probability of profit."}
    state["previous"] = diff_events(db, state.get("previous"), report, asof)
    report["scanner"]["events"] = recent_events(db)
    return report


def write_atomic(path, text):
    path = Path(path)
    tmp = path.with_suffix(path.suffix+".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def publish(report, out_dir):
    from .dashboard import render_dashboard
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_atomic(out/"index.html", render_dashboard(report))
    write_atomic(out/"state.json", json.dumps(report, indent=1, default=str))
    return out/"index.html"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve(directory, port):
    """Serve only the dashboard folder, only to this computer."""
    Path(directory).mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(QuietHandler, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def load_memory(db, path):
    """Carry the change log and last-seen state between hosted runs that each use a fresh database."""
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not db.execute("SELECT 1 FROM scanner_events LIMIT 1").fetchone():
        for ev in reversed(data.get("events", [])):
            db.execute("INSERT INTO scanner_events(observed,pair,kind,message,detail) VALUES (?,?,?,?,?)",
                       (ev["observed"], ev["pair"], ev["kind"], ev["message"], ev["detail"]))
        db.commit()
    return {"previous": data["previous"]} if data.get("previous") else {}


def save_memory(db, path, state):
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps({"previous": state.get("previous"),
        "events": [{k:e[k] for k in ("observed","pair","kind","message","detail")} for e in recent_events(db, 200)]}))


def run(db, config, out_dir, archive_dir, cycles=None, transport=download, clock=utcnow,
        sleep=time.sleep, echo=print, memory_path=None, page_refresh=None, hosted=False):
    config = copy.deepcopy(config)
    config["scanner"] = scanner_settings(config)
    transport = throttled(transport, config["scanner"]["request_interval_seconds"], sleep)
    state, done, last_archive = load_memory(db, memory_path), 0, None
    failures = 0
    while cycles is None or done < cycles:
        started = clock()
        try:
            report = cycle(db, config, state, transport, clock)
            report["scanner"]["page_refresh_seconds"] = int(page_refresh or config["scanner"]["refresh_seconds"])
            report["scanner"]["hosted"] = hosted
            page = publish(report, out_dir)
            save_memory(db, memory_path, state)
            hour = int(started//3600)
            if last_archive != hour:
                from .report import render
                render(report, archive_dir)
                last_archive = hour
            buys = [a["pair"] for a in report["phase4"]["assessments"] if a["action"] == "BUY"]
            echo(f"{iso(started)} scanned {len(report['phase4']['assessments'])} markets · "
                 f"status={report['research_status']} · paper BUY: {', '.join(buys) or 'none'} · {page}")
        except ValueError as exc:
            echo(f"{iso(started)} cycle failed: {exc}")
            failures += 1
        done += 1
        if cycles is not None and done >= cycles:
            break
        sleep(max(1, config["scanner"]["refresh_seconds"]-(clock()-started)))
    state["failures"] = failures
    return state


DEMO_COINS = [  # symbol, name, price, hourly drift, 24h base volume
    ("BTC","Bitcoin",60000,0.0010,900), ("ETH","Ethereum",2500,-0.0005,9000),
    ("SOL","Solana",150,0.0020,60000), ("DOGE","Dogecoin",0.12,0.0,90000000),
    ("LINK","Chainlink",14,-0.0015,300000), ("NEAR","NEAR Protocol",5,0.0004,500000)]


def demo_scanner_transport(now):
    """Synthetic discovery, tickers, candles and feeds for the demo database only."""
    from urllib.parse import parse_qs, urlsplit
    from .__main__ import demo_transport
    base = demo_transport(now)
    coins = {c[0]:c for c in DEMO_COINS}
    def transport(url, config):
        path = urlsplit(url).path
        if path == "/products":
            rows = [{"id":f"{s}-USD","base_currency":s,"quote_currency":"USD","status":"online"} for s in coins]
            rows += [{"id":"USDC-USD","base_currency":"USDC","quote_currency":"USD","status":"online"},
                     {"id":"OLD-USD","base_currency":"OLD","quote_currency":"USD","status":"delisted"},
                     {"id":"TINY-USD","base_currency":"TINY","quote_currency":"USD","status":"online"},
                     {"id":"BTC-EUR","base_currency":"BTC","quote_currency":"EUR","status":"online"}]
            return 200, json.dumps(rows).encode(), {}
        if path == "/products/stats":
            stats = {f"{s}-USD":{"stats_24hour":{"open":str(p*(1-24*d)),"last":str(p),"volume":str(v)}}
                     for s,_,p,d,v in coins.values()}
            stats["TINY-USD"] = {"stats_24hour":{"open":"1","last":"1","volume":"10"}}
            stats["USDC-USD"] = {"stats_24hour":{"open":"1","last":"1","volume":"1e9"}}
            return 200, json.dumps(stats).encode(), {}
        if path == "/currencies":
            return 200, json.dumps([{"id":s,"name":n} for s,n,*_ in coins.values()]).encode(), {}
        symbol = path.split("/")[2].split("-")[0] if path.startswith("/products/") else None
        if symbol in coins and symbol not in ("BTC","ETH"):
            _, _, price, drift, volume = coins[symbol]
            if path.endswith("/ticker"):
                return 200, json.dumps({"price":str(price),"bid":str(price*0.9999),"ask":str(price*1.0001),
                                        "volume":str(volume),"time":iso(now-10)}).encode(), {}
            q = parse_qs(urlsplit(url).query)
            g = int(q["granularity"][0])
            from .core import parse_time
            start, end = int(parse_time(q["start"][0])), int(parse_time(q["end"][0]))
            cutoff = int(now//g)*g
            rows = []
            for t in range(start, end, g):
                close = price*math.exp(drift*(t-cutoff)/g)
                rows.append([t, close*0.998, close*1.002, close*0.999, close, volume/24])
            return 200, json.dumps(rows[::-1]).encode(), {}
        if "/feed" in url or "/rss" in url:
            if "cointelegraph" in url:
                data = (f'<rss><channel><item><title>DEMO: Solana developer conference announced</title>'
                        f'<link>https://cointelegraph.com/demo-solana</link><pubDate>{iso(now-1800)}</pubDate>'
                        f'<description>Synthetic statement about the Solana blockchain. Not a real event.</description></item></channel></rss>')
                return 200, data.encode(), {}
            if "decrypt" in url:
                data = (f'<rss><channel><item><title>DEMO: Dogecoin payments pilot reportedly paused</title>'
                        f'<link>https://decrypt.co/demo-doge</link><pubDate>{iso(now-2400)}</pubDate>'
                        f'<description>Synthetic, unverified crypto claim for testing review flags.</description></item></channel></rss>')
                return 200, data.encode(), {}
        return base(url, config)
    return transport
