"""Public, auditable market discovery. Coverage is venue-scoped, never all tokens."""
import copy
import json
import math
import re
from .core import iso,record_fetch,utcnow
from .pipeline import download

BASE="https://api.exchange.coinbase.com"
STABLES={"USDC","USDT","DAI","USDS","USDP","TUSD","GUSD","PYUSD","RLUSD","USD1","USDE","EURC","EURCV","PAX","UST"}
DEFAULT_SCANNER={
    "max_assets":20,"min_approx_usd_volume_24h":1000000,
    "refresh_seconds":60,"discovery_refresh_seconds":3600,"feed_refresh_seconds":600,
    "candle_retry_seconds":600,"request_interval_seconds":0.2,
    "additional_feeds":[
        {"id":"cointelegraph","name":"Cointelegraph","url":"https://cointelegraph.com/rss",
         "publisher_group":"cointelegraph","kind":"reporting","required":False,
         "allowed_link_hosts":["cointelegraph.com","www.cointelegraph.com"]},
        {"id":"decrypt","name":"Decrypt","url":"https://decrypt.co/feed",
         "publisher_group":"decrypt","kind":"reporting","required":False,
         "allowed_link_hosts":["decrypt.co","www.decrypt.co"]}
    ]
}
def valid_pair(pair):
    return isinstance(pair,str) and bool(re.fullmatch(r"[A-Z0-9]{1,20}-USD",pair))

def finite_number(value):
    try:
        x=float(value)
        return x if math.isfinite(x) and x>=0 else None
    except (ValueError,TypeError,OverflowError):
        return None

def validate_scanner(s):
    from .settings import number
    for k,low,high,integer in [
        ("max_assets",2,50,True),("min_approx_usd_volume_24h",0,1e12,False),
        ("refresh_seconds",30,3600,True),("discovery_refresh_seconds",900,86400,True),
        ("feed_refresh_seconds",300,7200,True),("candle_retry_seconds",300,3600,True),
        ("request_interval_seconds",0.11,5,False)]:
        number(s.get(k),k,low,high,integer)
    if not isinstance(s.get("additional_feeds"),list):
        raise ValueError("scanner.additional_feeds must be a list")

def select_products(products,stats,currencies,settings,held=()):
    if not isinstance(products,list) or not isinstance(stats,dict) or not isinstance(currencies,list):
        raise ValueError("Unexpected universe response schema")
    names={x.get("id"):x.get("name",x.get("id")) for x in currencies if isinstance(x,dict)}
    rows=[];eligible_count=0
    for product in products:
        pair=product.get("id")
        base=product.get("base_currency")
        if not valid_pair(pair) or product.get("quote_currency")!="USD" or pair!=str(base)+"-USD":
            continue
        if product.get("status")!="online" or any(product.get(k) for k in
            ("trading_disabled","cancel_only","post_only","limit_only","auction_mode")):
            continue
        if product.get("fx_stablecoin") or base in STABLES:
            continue
        eligible_count+=1
        daily=stats.get(pair,{}).get("stats_24hour",{})
        last,volume,opening=(finite_number(daily.get(k)) for k in ("last","volume","open"))
        if last is None or last<=0 or volume is None:
            continue
        approximate=last*volume
        if not math.isfinite(approximate) or approximate<settings["min_approx_usd_volume_24h"]:
            continue
        rows.append({"pair":pair,"symbol":base,"name":names.get(base,base),
            "approx_usd_volume_24h":approximate,"base_volume_24h":volume,
            "stats_last":last,"stats_change_24h_pct":(last/opening-1)*100 if opening else None,
            "source_url":f"{BASE}/products/{pair}/stats"})
    rows.sort(key=lambda x:(-x["approx_usd_volume_24h"],x["pair"]))
    selected=rows[:int(settings["max_assets"])]
    present={r["pair"] for r in selected}
    for pair in held:
        if pair not in present:
            if not valid_pair(pair):
                raise ValueError("Held asset is not a supported USD product identifier")
            selected.append(next((r for r in rows if r["pair"]==pair),
                {"pair":pair,"symbol":pair[:-4],"name":names.get(pair[:-4],pair[:-4]),
                 "approx_usd_volume_24h":None,"base_volume_24h":None,
                 "stats_last":None,"stats_change_24h_pct":None,
                 "source_url":f"{BASE}/products/{pair}/stats","retained_for_risk_review":True}))
    if not selected:
        raise ValueError("No eligible liquid USD markets found; no fallback universe substituted")
    return selected,eligible_count,len(rows)

def fetch_json(db,path,source,config,transport,clock):
    now=clock()
    latest=db.execute("SELECT * FROM fetches WHERE source=? ORDER BY received DESC,id DESC LIMIT 1",(source,)).fetchone()
    if latest and latest["ok"] and now-latest["received"]<config["scanner"]["discovery_refresh_seconds"]:
        return json.loads(latest["body"]),latest["received"],latest["id"]
    url=BASE+path;body=None;status=None;headers={}
    try:
        status,body,headers=transport(url,config)
        if status!=200:raise ValueError(f"{source}: HTTP {status}")
        data=json.loads(body)
        if not isinstance(data,(dict,list)):raise ValueError("Unexpected discovery payload")
    except (ValueError,TypeError,OSError) as exc:
        record_fetch(db,None,source,url,clock(),status,False,str(exc),body,headers)
        db.commit()
        raise
    received=clock()
    fid=record_fetch(db,None,source,url,received,status,True,None,body,headers)
    db.commit()
    return data,received,fid

def discover(db,config,transport=download,clock=utcnow):
    runtime=copy.deepcopy(config)
    runtime["scanner"]=dict(copy.deepcopy(DEFAULT_SCANNER),**runtime.get("scanner",{}))
    validate_scanner(runtime["scanner"])
    payloads=[];sources=[]
    for path,source in [("/products","universe:products"),("/products/stats","universe:stats"),
                        ("/currencies","universe:currencies")]:
        data,observed,fid=fetch_json(db,path,source,runtime,transport,clock)
        payloads.append(data)
        sources.append({"url":BASE+path,"observed_at":iso(observed),"fetch_id":fid})
    held=[a for a,p in runtime["phase4"]["portfolio"]["positions"].items() if p["quantity"]>0]
    selected,active,liquid=select_products(*payloads,runtime["scanner"],held)
    runtime["assets"]=[r["pair"] for r in selected]
    runtime["asset_aliases"]={r["symbol"]:[r["name"]] for r in selected}
    runtime["research_universe"]={"scope":"Coinbase Exchange online liquid USD markets",
        "selection":"Largest approximate 24h USD turnover (base volume × last price); excludes stable pairs and restricted books.",
        "observed_at":iso(clock()),"eligible_active_count":active,"liquid_count":liquid,
        "selected_count":len(selected),"members":selected,"sources":sources,
        "limitations":["Venue coverage is not the entire crypto market.",
            "Approximate USD turnover is not exact traded notional, market cap or executable depth.",
            "Current selection introduces survivorship/selection bias; do not use it as a historical universe."]}
    ids={f["id"] for f in runtime["feeds"]}
    runtime["feeds"] += [f for f in runtime["scanner"]["additional_feeds"] if f["id"] not in ids]
    return runtime
