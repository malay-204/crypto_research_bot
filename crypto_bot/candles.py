"""Bounded time-window pagination, closed OHLCV validation and revision storage."""
import json
import math
from urllib.parse import urlencode
from .core import iso, utcnow, record_fetch

BASE = "https://api.exchange.coinbase.com"

def parse_candles(body, granularity, start, end, closed_before):
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise ValueError("Candles response must be an array")
    accepted, excluded = {}, 0
    for row in rows:
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError("Candle must contain time, low, high, open, close, volume")
        if any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) for v in row):
            raise ValueError("Candle contains nonfinite or nonnumeric values")
        t, low, high, opening, close, volume = row
        if t <= 0 or t != int(t) or int(t) % granularity:
            raise ValueError("Unaligned or invalid candle timestamp")
        if min(low, high, opening, close) <= 0 or volume < 0 or not low <= min(opening,close) <= max(opening,close) <= high:
            raise ValueError("Invalid candle OHLC/volume")
        # Coinbase can return rows before start and at end. Never include open buckets.
        if t < start or t >= end or t + granularity > closed_before:
            excluded += 1
            continue
        value = tuple(row)
        if int(t) in accepted and accepted[int(t)] != value:
            raise ValueError("Conflicting duplicate candle in one response")
        accepted[int(t)] = value
    return sorted(accepted.values()), excluded

def ingest_candles(db, fid, pair, g, observed, rows):
    changed = 0
    for t, low, high, opening, close, volume in rows:
        prior = db.execute("""SELECT low,high,open,close,volume FROM candles
            WHERE pair=? AND granularity=? AND start=? ORDER BY observed DESC,id DESC LIMIT 1""",
            (pair,g,t)).fetchone()
        values = (low,high,opening,close,volume)
        if prior and tuple(prior) == values:
            continue
        db.execute("""INSERT INTO candles(fetch_id,pair,granularity,start,low,high,open,close,volume,observed)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (fid,pair,g,t,*values,observed))
        changed += 1
    return changed

def collect_candles(db, config, run, transport, clock=utcnow, pairs=None):
    settings = config["phase4"]["candles"]
    g, count, page_size = (int(settings[k]) for k in ("granularity_seconds","lookback_candles","page_size"))
    # Freeze the watermark once for all pairs/pages, even if collection crosses an hour.
    cutoff = int(clock() // g) * g
    start = cutoff - count*g
    summaries = []
    for pair in (config["assets"] if pairs is None else pairs):
        batch = db.execute("""INSERT INTO candle_batches(run_id,pair,granularity,start,end,started,status,expected)
            VALUES (?,?,?,?,?,?,'in_progress',?)""",(run,pair,g,start,cutoff,clock(),count)).lastrowid
        db.commit()
        seen, excluded, errors = set(), 0, []
        for left in range(start, cutoff, page_size*g):
            right = min(left+page_size*g,cutoff)
            url = f"{BASE}/products/{pair}/candles?" + urlencode(
                {"granularity":g,"start":iso(left),"end":iso(right)})
            body, status, headers = None, None, {}
            try:
                status, body, headers = transport(url,config)
                received = clock()
                if status != 200:
                    raise ValueError(f"HTTP {status}")
                rows, skipped = parse_candles(body,g,left,right,min(cutoff,received))
                fid = record_fetch(db,run,f"candles:{pair}:{g}",url,received,status,True,None,body,headers)
                ingest_candles(db,fid,pair,g,received,rows)
                seen.update(int(r[0]) for r in rows)
                excluded += skipped
            except (ValueError,TypeError,KeyError,OverflowError,OSError) as exc:
                error = f"{type(exc).__name__}: {str(exc)[:250]}"
                record_fetch(db,run,f"candles:{pair}:{g}",url,clock(),status,False,error,body,headers)
                errors.append(error)
            db.commit()
        missing = count - len(seen)
        state = "error" if errors else ("gaps" if missing else "ok")
        db.execute("""UPDATE candle_batches SET finished=?,status=?,received_count=?,missing_count=?,
            excluded_count=?,error=? WHERE id=?""",
            (clock(),state,len(seen),missing,excluded,"; ".join(errors) or None,batch))
        db.commit()
        summaries.append({"source":f"candles:{pair}:{g}","ok":state=="ok",
            "detail":f"{len(seen)}/{count} closed candles; {excluded} out-of-window rows excluded; status={state}",
            "errors":errors})
    return summaries

def candle_view(db, config, pair, asof):
    p = config["phase4"]
    g = p["candles"]["granularity_seconds"]
    batch = db.execute("""SELECT * FROM candle_batches WHERE pair=? AND granularity=? AND started<=?
        ORDER BY started DESC,id DESC LIMIT 1""",(pair,g,asof)).fetchone()
    result = {"pair":pair,"granularity_seconds":g,"status":"missing","batch":None,"rows":[],
              "latest_close":None,"close_age_seconds":None,
              "source_url":f"{BASE}/products/{pair}/candles"}
    if not batch:
        return result
    b = dict(batch)
    result["batch"] = b
    if b["finished"] is None or b["finished"] > asof:
        result["status"] = "in_progress"
        return result
    result["status"] = b["status"]
    # Keep original observation times and all revisions; a later correction cannot rewrite history.
    rows = db.execute("""SELECT c.*,f.url AS source_url FROM candles c JOIN fetches f ON f.id=c.fetch_id
        WHERE c.pair=? AND c.granularity=? AND c.observed<=? AND c.start>=? AND c.start<?
        AND c.start+c.granularity<=? ORDER BY c.start DESC,c.observed DESC,c.id DESC""",
        (pair,g,asof,b["start"],b["end"],asof)).fetchall()
    unique = {}
    for row in rows:
        unique.setdefault(row["start"],dict(row))
    selected = sorted(unique.values(),key=lambda r:r["start"])[-p["strategy"]["slow_window"]:]
    result["rows"] = selected
    if selected:
        closed = selected[-1]["start"]+g
        result["latest_close"] = iso(closed)
        result["close_age_seconds"] = asof-closed
        if asof-closed > p["candles"]["max_age_seconds"]:
            result["status"] = "stale"
    if result["status"] == "ok":
        required = list(range(b["end"]-p["strategy"]["slow_window"]*g,b["end"],g))
        if [r["start"] for r in selected] != required:
            result["status"] = "insufficient_or_noncontiguous"
    return result
