import argparse
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit, parse_qs
from .core import connect, load_config, parse_time, utcnow, iso
from .pipeline import collect, snapshot
from .report import render


def demo_transport(now):
    """Synthetic fixtures only. Never used as a fallback for live collection."""
    def transport(url,config):
        if '/candles?' in url:
            q = parse_qs(urlsplit(url).query)
            g = int(q['granularity'][0]); start = int(parse_time(q['start'][0])); end = int(parse_time(q['end'][0]))
            base = 60000 if 'BTC-USD' in url else 2500
            slope = 0.001 if 'BTC-USD' in url else -0.0005
            cutoff = int(now//g)*g
            rows=[]
            for t in range(start,end,g):
                close = base*(1+slope*(t-cutoff)/g)
                rows.append([t,close*0.998,close*1.002,close*0.999,close,123.4])
            return 200,json.dumps(rows[::-1]).encode(),{}
        if '/products/' in url:
            price = 60000 if 'BTC-USD' in url else 2500
            return 200,json.dumps(dict(price=str(price),bid=str(price-1),ask=str(price+1),volume='123.4',time=iso(now-10))).encode(),{}
        published = iso(now-3600)
        if 'blog.ethereum.org' in url:
            data = f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>DEMO: Ethereum research update</title><link href="https://blog.ethereum.org/demo-fixture"/><published>{published}</published><summary>Synthetic test statement about Ethereum. This is not a real announcement.</summary></entry></feed>'''
        else:
            data = f'''<rss><channel>
            <item><title>DEMO: Bitcoin service incident reportedly resolved</title><link>https://www.coindesk.com/demo-bitcoin</link><pubDate>{published}</pubDate><description>Synthetic headline for evidence processing. No real incident is claimed.</description></item>
            <item><title>DEMO: Bitcoin service incident reportedly not resolved</title><link>https://www.coindesk.com/demo-bitcoin-conflict</link><pubDate>{published}</pubDate><description>Synthetic conflicting report. Keep both claims for manual review.</description></item>
            <item><title>DEMO: Ethereum test feed duplicate</title><link>https://www.coindesk.com/demo-ethereum?utm_source=fixture</link><pubDate>{published}</pubDate></item>
            <item><title>DEMO: Ethereum test feed duplicate</title><link>https://www.coindesk.com/demo-ethereum</link><pubDate>{published}</pubDate></item>
            <item><title>DEMO: Bitcoin undated claim</title><link>https://www.coindesk.com/demo-undated</link></item>
            </channel></rss>'''
        return 200,data.encode(),{}
    return transport


def main():
    parser = argparse.ArgumentParser(description='Read-only crypto research: configuration, collectors and evidence reports.')
    parser.add_argument('command',choices=['validate','collect','report','demo','scan'])
    parser.add_argument('--demo',action='store_true',help='scan only: synthetic markets in the demo database')
    parser.add_argument('--cycles',type=int,help='scan only: stop after N refreshes (default: run until Ctrl+C)')
    parser.add_argument('--port',type=int,default=8765,help='scan only: local dashboard port on 127.0.0.1')
    parser.add_argument('--no-serve',action='store_true',help='scan only: write the dashboard file without a local web server')
    parser.add_argument('--memory',help='scan only: JSON file that carries the change log between separate runs')
    parser.add_argument('--hosted-interval',type=int,help='scan only: expected minutes between scheduled hosted runs (GitHub Pages)')
    parser.add_argument('--config',default='config.json')
    parser.add_argument('--db',default=None)
    parser.add_argument('--portfolio',help='Manual paper scenario JSON; collect/demo only; stored with the run')
    parser.add_argument('--out',default='reports')
    parser.add_argument('--as-of',help='Timezone-aware ISO timestamp for historical report; only for report')
    args = parser.parse_args()
    try:
        config = load_config(args.config, discoverable=args.command=='scan')
        if args.portfolio:
            if args.command not in ('collect','demo','scan'):
                raise ValueError('--portfolio is only valid for collect/demo; reports use recorded inputs')
            if not config.get('phase4'):
                raise ValueError('--portfolio requires phase4 settings')
            from .settings import validate_phase4
            config['phase4']['portfolio'] = json.loads(Path(args.portfolio).read_text(encoding='utf-8-sig'))
            validate_phase4(config, discoverable=args.command=='scan')
        if args.command=='validate':
            print(json.dumps({'valid':True,'execution_enabled':False,'pending_choices':config['pending_user_choices']},indent=2)); return 0
        if args.as_of and args.command!='report': raise ValueError('--as-of is only valid for report')
        if args.command=='scan':
            return scan(args,config)
        mode = 'demo' if args.command=='demo' else 'live'
        path = args.db or f'data/{mode}.sqlite3'
        if args.command=='report' and not Path(path).exists(): raise ValueError('Database missing; run collect first')
        db = connect(path,mode)
        try:
            if args.command in ('collect','demo'):
                now = utcnow()
                summary = collect(db,config,transport=demo_transport(now),clock=lambda:now,with_candles=True) if mode=='demo' else collect(db,config,with_candles=True)
                print(json.dumps(summary,indent=2))
            asof = parse_time(args.as_of) if args.as_of else utcnow()
            if asof is None: raise ValueError('Invalid --as-of; include UTC Z or an explicit timezone')
            if args.command=='report':
                row = db.execute('SELECT config FROM runs WHERE started<=? ORDER BY started DESC,id DESC LIMIT 1',(asof,)).fetchone()
                if row is None: raise ValueError('No configuration was recorded at or before that time')
                config = json.loads(row[0])
            report = snapshot(db,config,asof)
            paths = render(report,args.out)
            print(json.dumps({'status':report['research_status'],'mode':mode,'html':str(paths[0]),'json':str(paths[1]),'execution_enabled':False,
                'phase4_status':report.get('phase4',{}).get('status'),
                'assessments':[{k:a[k] for k in ('pair','action','reason')} for a in report.get('phase4',{}).get('assessments',[])]},indent=2))
            # Nonzero status lets an external scheduler detect blocked research.
            return 2 if report['research_status']=='blocked' or report.get('phase4',{}).get('status')=='inputs_blocked' else 0
        finally: db.close()
    except (ValueError,OSError,sqlite3.Error) as exc:
        parser.exit(1,f'Error: {exc}\n')


def scan(args,config):
    from .scanner import run, serve, demo_scanner_transport
    from .pipeline import download
    if args.cycles is not None and args.cycles < 1: raise ValueError('--cycles must be at least 1')
    mode = 'demo' if args.demo else 'live'
    db = connect(args.db or f'data/{mode}.sqlite3',mode)
    out = Path(args.out)/'live_dashboard'/mode
    server = None
    try:
        if not args.no_serve:
            server = serve(out,args.port)
            print(f'Dashboard: http://127.0.0.1:{args.port}/  (this computer only; Ctrl+C stops the scanner)')
        print(f'Dashboard file: {(out/"index.html").resolve()}')
        print('PAPER ONLY - execution disabled. No orders, wallets or background service.')
        if mode=='demo':
            transport = lambda url,c: demo_scanner_transport(utcnow())(url,c)
        else:
            transport = download
        hosted = args.hosted_interval is not None
        if hosted and not 5 <= args.hosted_interval <= 1440: raise ValueError('--hosted-interval must be 5-1440 minutes')
        state = run(db,config,out,Path(args.out)/'scanner'/mode,cycles=args.cycles,transport=transport,
            memory_path=args.memory,page_refresh=args.hosted_interval*60 if hosted else None,hosted=hosted)
        if args.cycles and state.get('failures')==args.cycles:
            return 1
    except KeyboardInterrupt:
        print('Scanner stopped. The dashboard keeps its last refresh and will show as stale.')
    finally:
        if server: server.shutdown(); server.server_close()
        db.close()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
