"""Public, read-only HTTP collectors and reproducible research snapshots."""
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .core import (utcnow, iso, record_fetch, validate_ticker, parse_feed,
                   ingest_articles, evidence)

BASE = 'https://api.exchange.coinbase.com'
MAX_BYTES = 4 * 1024 * 1024


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        a,b = urlsplit(req.full_url),urlsplit(newurl)
        if b.scheme != 'https' or a.hostname != b.hostname:
            raise ValueError('Cross-host or non-HTTPS redirect blocked; review source configuration')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def download(url, config):
    opener = urllib.request.build_opener(SameHostRedirect())
    for attempt in range(config['http_attempts']):
        try:
            req = urllib.request.Request(url,headers={'User-Agent':'CryptoResearchBot/0.1 (read-only research)',
                'Accept':'application/json,application/rss+xml,application/atom+xml,application/xml,text/xml'})
            with opener.open(req,timeout=config['http_timeout_seconds']) as response:
                body = response.read(MAX_BYTES+1)
                if len(body)>MAX_BYTES: raise ValueError('Source response exceeds 4 MiB limit')
                headers = {k:v for k,v in response.headers.items() if k.lower() in ('date','etag','last-modified','content-type')}
                return response.status, body, headers
        except urllib.error.HTTPError as e:
            # Never try to bypass a blocked feed. Preserve the failed response.
            if e.code not in (429,500,502,503,504) or attempt+1 == config['http_attempts']:
                return e.code,e.read(MAX_BYTES),{}
        except (urllib.error.URLError,TimeoutError,OSError):
            if attempt+1 == config['http_attempts']: raise
        time.sleep(min(2**attempt,4))
    raise RuntimeError('HTTP attempt limit reached')


def collect(db, config, transport=download, clock=utcnow, with_candles=False, feeds=None, candle_pairs=None):
    """feeds/candle_pairs=None polls everything; the live scanner passes only sources that are due."""
    run = db.execute('INSERT INTO runs(started,config) VALUES (?,?)',(clock(),json.dumps(config,sort_keys=True))).lastrowid
    summary = []
    if with_candles and config.get('phase4'):
        from .candles import collect_candles
        summary.extend(collect_candles(db,config,run,transport,clock,pairs=candle_pairs))
    jobs = [(f'market:{p}',f'{BASE}/products/{p}/ticker',p,None) for p in config['assets']]
    jobs += [(f'feed:{f["id"]}',f['url'],None,f) for f in (config['feeds'] if feeds is None else feeds)]
    for source,url,pair,feed in jobs:
        body,status,headers = None,None,{}
        received = clock()
        try:
            status,body,headers = transport(url,config)
            received = clock()
            if status != 200: raise ValueError(f'HTTP {status}')
            if pair:
                event,values = validate_ticker(json.loads(body),received,config)
                parsed = (event,values)
            else:
                parsed = parse_feed(body,feed)
            stored = body
            if feed and config.get('research_universe'):
                # Scanner polls feeds often; an unchanged body is referenced by digest instead of re-stored.
                import hashlib
                prev = db.execute('SELECT id,sha256 FROM fetches WHERE source=? AND ok=1 AND body IS NOT NULL ORDER BY received DESC,id DESC LIMIT 1',(source,)).fetchone()
                if prev and prev['sha256'] == hashlib.sha256(body).hexdigest():
                    stored, headers = None, dict(headers, unchanged_body_of_fetch_id=prev['id'], body_sha256=prev['sha256'])
            fid = record_fetch(db,run,source,url,received,status,True,None,stored,headers)
            if pair:
                event,values = parsed
                db.execute('INSERT INTO markets(fetch_id,pair,event_time,observed,price,bid,ask,volume) VALUES (?,?,?,?,?,?,?,?)',
                           (fid,pair,event,received,*values))
                detail = 'Ticker stored'
            else:
                articles,skipped = parsed
                count = ingest_articles(db,fid,feed,received,articles)
                detail = f'{len(articles)} parsed; {count} new revisions; {skipped} rejected items'
            summary.append({'source':source,'ok':True,'detail':detail})
        except (ValueError,TypeError,KeyError,OverflowError,urllib.error.URLError,TimeoutError,OSError) as e:
            # A failed current poll never silently falls back to a healthy status.
            fid = record_fetch(db,run,source,url,received,status,False,f'{type(e).__name__}: {str(e)[:300]}',body,headers)
            summary.append({'source':source,'ok':False,'detail':str(e)[:300]})
        except Exception as e:
            # XML parsing and unexpected provider schema failures still leave an audit trail.
            record_fetch(db,run,source,url,received,status,False,f'{type(e).__name__}: {str(e)[:300]}',body,headers)
            summary.append({'source':source,'ok':False,'detail':str(e)[:300]})
        db.commit()
    db.execute('UPDATE runs SET finished=? WHERE id=?',(clock(),run))
    db.commit()
    return summary


def snapshot(db,config,asof=None):
    asof = utcnow() if asof is None else asof
    mode = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()[0]
    required = {f'market:{p}':True for p in config['assets']}
    required.update({f'feed:{f["id"]}':bool(f['required']) for f in config['feeds']})
    scanner = bool(config.get('research_universe'))
    health, reasons, markets, asset_blocks = [],[],[],{}
    for source,must in required.items():
        r = db.execute('SELECT * FROM fetches WHERE source=? AND received<=? ORDER BY received DESC,id DESC LIMIT 1',(source,asof)).fetchone()
        limit = config['market_max_age_seconds'] if source.startswith('market:') else config['feed_max_age_seconds']
        state = 'missing' if r is None else ('error' if not r['ok'] else ('stale' if asof-r['received']>limit else 'ok'))
        h = {'source':source,'required':must,'status':state,'last_poll':iso(r['received']) if r else None,
             'age_seconds':round(asof-r['received']) if r else None,'error':r['error'] if r else None}
        if source.startswith('market:') and r and r['ok']:
            m = db.execute('SELECT * FROM markets WHERE fetch_id=?',(r['id'],)).fetchone()
            if not m:
                h['status']='invalid'
            else:
                x = dict(m)
                x['spread_bps'] = (m['ask']-m['bid'])/((m['ask']+m['bid'])/2)*10000
                x['event_age_seconds'] = round(asof-m['event_time'])
                if m['event_time'] > asof or asof-m['event_time']>limit:
                    h['status']='stale_event' if m['event_time']<=asof else 'future_event'
                if x['spread_bps']>config['max_spread_bps']:
                    h['status']='wide_spread'
                x['data_status']=h['status']
                prior = db.execute('SELECT price,observed FROM markets WHERE pair=? AND observed<? ORDER BY observed DESC,id DESC LIMIT 1',(m['pair'],m['observed'])).fetchone()
                x['change_since_previous_poll_pct'] = (m['price']/prior['price']-1)*100 if prior else None
                x['previous_poll_time'] = iso(prior['observed']) if prior else None
                markets.append(x)
        health.append(h)
        if must and h['status']!='ok':
            # A broad scanner must not let one illiquid coin's stale ticker block every other coin.
            if scanner and source.startswith('market:'):
                asset_blocks.setdefault(source[7:],[]).append(f'{source}: {h["status"]}')
            else:
                reasons.append(f'{source}: {h["status"]}')
    cards,candidates,excluded = evidence(db,asof,config)
    coverage = {p.split('-')[0]:sum(p.split('-')[0] in a['assets'] for a in cards) for p in config['assets']}
    news_gaps = [asset for asset,count in coverage.items() if count==0]
    if not scanner:
        reasons.extend(f'{asset}: no recent timestamped relevant evidence' for asset in news_gaps)
    # Availability is separate from reliability or forecast usefulness.
    status = 'blocked' if reasons else ('degraded' if asset_blocks or any(h['status']!='ok' for h in health) else 'ready_for_review')
    report = {'project':config['project'],'version':'0.1.0','mode':mode,'as_of':iso(asof),
        'forecast_horizon_hours':config['forecast_horizon_hours'],'research_status':status,
        'action':'RESEARCH ONLY — NO TRADING DECISION','execution_enabled':False,
        'blocking_reasons':reasons,'asset_blocks':asset_blocks,'news_gaps':news_gaps,'health':health,'markets':markets,'evidence':cards,
        'possible_duplicate_events':candidates,'excluded_evidence':excluded,'coverage':coverage,
        'limitations':['No predictive model, trading signals or order execution in phases 1–3.',
        'Headlines/excerpts are attributed claims, not verified facts or causal explanations.',
        'Semantic contradiction detection and independent corroboration require manual review.',
        'Price/spread are public REST snapshots, not streaming or executable quotations.',
        'On-chain, macro, full-text news and cross-venue validation are not yet connected.',
        'Asset matching uses explicit BTC/Bitcoin and ETH/Ether/Ethereum mentions; ambiguous matches need review.',
        'ready_for_review describes data availability, never permission to trade.'],
        'config':config}
    if config.get('phase4'):
        from .strategy import assess
        report['version'] = '0.4.0'
        report['action'] = 'PAPER ASSESSMENTS ONLY — EXECUTION DISABLED'
        report['limitations'][0] = 'Unvalidated numerical baseline; no forecasts, fills or order execution.'
        report['phase4'] = assess(db,report,asof)
        report['limitations'].extend(report['phase4']['limitations'])
    return report
