"""Configuration, append-only evidence storage, parsing and conservative grouping."""
import hashlib
import html
import json
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def utcnow():
    return datetime.now(timezone.utc).timestamp()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z') if ts is not None else None


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        try:
            dt = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    # Never silently assign a timezone to an ambiguous publication time.
    return dt.timestamp() if dt.tzinfo is not None else None


def load_config(path, discoverable=False):
    c = json.loads(Path(path).read_text(encoding='utf-8'))
    if c.get('mode') != 'research_only' or c.get('execution_enabled') is not False:
        raise ValueError('This release only permits research_only with execution_enabled=false')
    if c.get('data_venue') != 'coinbase_exchange':
        raise ValueError('Only the Coinbase Exchange public-data adapter is implemented')
    assets = c.get('assets', [])
    from .universe import valid_pair
    if not assets or len(assets)>100 or len(assets) != len(set(assets)) or any(not valid_pair(a) for a in assets):
        raise ValueError('Use 1–100 unique public USD product identifiers, e.g. BTC-USD')
    for key in ('forecast_horizon_hours', 'review_interval_seconds', 'market_max_age_seconds',
                'feed_max_age_seconds', 'news_lookback_hours', 'http_timeout_seconds', 'max_spread_bps'):
        if not isinstance(c.get(key), (int, float)) or not math.isfinite(c[key]) or c[key] <= 0:
            raise ValueError(f'{key} must be a finite positive number')
    if not 1 <= c.get('http_attempts', 0) <= 3:
        raise ValueError('http_attempts must be 1–3')
    if not 0 <= c.get('future_tolerance_seconds', -1) <= 300:
        raise ValueError('future_tolerance_seconds must be 0–300')
    if c.get('scanner'):
        from .universe import validate_scanner
        validate_scanner(c['scanner'])
    feeds = c.get('feeds', []) + c.get('scanner',{}).get('additional_feeds',[])
    ids = [f['id'] for f in feeds]
    if len(ids) != len(set(ids)):
        raise ValueError('Feed IDs must be unique')
    for f in feeds:
        u = urlsplit(f['url'])
        if u.scheme != 'https' or not u.hostname or u.username or u.password:
            raise ValueError('Feed URLs must use HTTPS without credentials')
        if not re.fullmatch(r'[a-z0-9_]+', f['id']):
            raise ValueError('Invalid feed ID')
        if not f.get('publisher_group') or not f.get('allowed_link_hosts'):
            raise ValueError('Each source needs publisher_group and allowed_link_hosts')
    from .settings import validate_phase4
    validate_phase4(c, discoverable)
    return c


def connect(path, mode):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    from .schema import prepare, upgrade
    try:
        prepare(db, path, mode)
    except Exception:
        db.close()
        raise
    db.executescript('''
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started REAL, finished REAL, config TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS fetches(
      id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), source TEXT NOT NULL,
      url TEXT NOT NULL, received REAL NOT NULL, status INTEGER, ok INTEGER NOT NULL,
      error TEXT, body BLOB, sha256 TEXT, headers TEXT);
    CREATE INDEX IF NOT EXISTS fetch_source_time ON fetches(source, received);
    CREATE TABLE IF NOT EXISTS markets(
      id INTEGER PRIMARY KEY, fetch_id INTEGER REFERENCES fetches(id), pair TEXT,
      event_time REAL, observed REAL, price REAL, bid REAL, ask REAL, volume REAL);
    CREATE TABLE IF NOT EXISTS articles(
      id INTEGER PRIMARY KEY, fetch_id INTEGER REFERENCES fetches(id), source TEXT,
      publisher_group TEXT, kind TEXT, url TEXT, title TEXT, excerpt TEXT,
      published REAL, observed REAL, content_hash TEXT, assets TEXT);
    CREATE INDEX IF NOT EXISTS article_time ON articles(observed);
    ''')
    row = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
    if row and row[0] != mode:
        db.close()
        raise ValueError('Demo and live data must use separate databases')
    db.execute("INSERT OR IGNORE INTO meta VALUES ('mode',?)", (mode,))
    db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version','1')")
    db.commit()
    upgrade(db)
    return db


def record_fetch(db, run, source, url, received, status, ok, error, body, headers=None):
    return db.execute('INSERT INTO fetches(run_id,source,url,received,status,ok,error,body,sha256,headers) VALUES (?,?,?,?,?,?,?,?,?,?)',
        (run, source, url, received, status, int(ok), error, body,
         hashlib.sha256(body).hexdigest() if body is not None else None, json.dumps(headers or {}))).lastrowid


def validate_ticker(payload, now, config):
    values = [float(payload[k]) for k in ('price', 'bid', 'ask', 'volume')]
    if not all(math.isfinite(v) for v in values) or any(v <= 0 for v in values[:3]) or values[3] < 0:
        raise ValueError('Invalid or nonfinite ticker values')
    if values[1] > values[2]:
        raise ValueError('Crossed bid/ask')
    event = parse_time(payload.get('time'))
    if event is None or event > now + config['future_tolerance_seconds']:
        raise ValueError('Invalid or future ticker timestamp')
    return event, values


def clean_text(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', text or ''))).strip()


def canonical_url(url, allowed):
    u = urlsplit(url.strip())
    if u.scheme != 'https' or u.hostname not in allowed or u.username or u.password or u.port not in (None,443):
        raise ValueError('Unapproved article link')
    # Keep semantically significant query parameters, remove tracking only.
    params = [(k,v) for k,v in parse_qsl(u.query, keep_blank_values=True)
              if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')]
    return urlunsplit(('https', u.netloc.lower(), u.path or '/', urlencode(sorted(params)), ''))


def match_assets(text, aliases=None):
    from .matching import match_entities
    return match_entities(text, aliases)


def parse_feed(body, source):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise ValueError('XML entities and DTDs are not accepted')
    root = ET.fromstring(body)
    local = lambda tag: tag.rsplit('}',1)[-1]
    if local(root.tag) not in ('rss','feed','RDF'):
        raise ValueError('Response is not an RSS or Atom feed')
    items = [n for n in root.iter() if local(n.tag) in ('item','entry')]
    output, skipped = [], 0
    for item in items[:500]:
        fields = {}
        link = ''
        for child in item:
            key = local(child.tag)
            val = ''.join(child.itertext())
            fields.setdefault(key, val)
            if key == 'link' and child.attrib.get('rel','alternate') == 'alternate':
                link = child.attrib.get('href', val)
        title = clean_text(fields.get('title'))[:500]
        try:
            url = canonical_url(link, source['allowed_link_hosts'])
        except (ValueError, TypeError):
            skipped += 1
            continue
        if not title:
            skipped += 1
            continue
        # Short feed excerpt only; no external article body is scraped.
        excerpt = clean_text(fields.get('description') or fields.get('summary') or fields.get('content'))[:600]
        published = parse_time(fields.get('pubDate') or fields.get('published') or fields.get('date'))
        # Atom updated is not necessarily the original publication timestamp.
        output.append(dict(url=url,title=title,excerpt=excerpt,published=published,
                           assets=match_assets(title+' '+excerpt,source.get('asset_aliases'))))
    if items and not output:
        raise ValueError('No usable items: check source link allowlist or feed schema')
    return output, skipped


def ingest_articles(db, fetch_id, source, observed, articles):
    count = 0
    for a in articles:
        signature = hashlib.sha256(json.dumps([a['title'],a['excerpt'],a['published']],ensure_ascii=False).encode()).hexdigest()
        previous = db.execute('SELECT content_hash FROM articles WHERE source=? AND url=? ORDER BY observed DESC,id DESC LIMIT 1', (source['id'],a['url'])).fetchone()
        if previous and previous[0] == signature:
            continue
        cursor = db.execute('''INSERT INTO articles(fetch_id,source,publisher_group,kind,url,title,excerpt,published,observed,content_hash,assets)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (fetch_id,source['id'],source['publisher_group'],source['kind'],a['url'],a['title'],a['excerpt'],a['published'],observed,signature,json.dumps(a['assets'])))
        count += cursor.rowcount
    return count


def evidence(db, asof, config):
    # Select the latest revision actually observed by the requested as-of time.
    rows = db.execute('''SELECT * FROM articles WHERE observed<=? ORDER BY observed DESC,id DESC''',(asof,)).fetchall()
    latest = {}
    for r in rows:
        latest.setdefault((r['source'],r['url']),dict(r))
    cards, excluded = [], {'missing_publication_time':0,'future_publication_time':0,'old':0,'unmatched_asset':0}
    for a in latest.values():
        a['assets'] = (match_assets(a['title']+' '+a['excerpt'],config['asset_aliases'])
            if config.get('asset_aliases') else json.loads(a['assets']))
        pub = a['published']
        if not a['assets']:
            excluded['unmatched_asset'] += 1; continue
        if pub is None:
            excluded['missing_publication_time'] += 1; continue
        if pub > asof:
            excluded['future_publication_time'] += 1; continue
        if asof-pub > config['news_lookback_hours']*3600:
            excluded['old'] += 1; continue
        a['status'] = 'attributed_source_statement_not_independently_verified'
        # Hints are for human review, not market direction or investment signals.
        a['review_flags'] = [term for term in ('rumor','rumour','unconfirmed','reportedly','hack','exploit','denies')
                             if re.search(r'\b'+term+r'\b', a['title']+' '+a['excerpt'], re.I)]
        cards.append(a)
    # Similar headlines are candidates only: numbers or negation can change the meaning.
    candidates = []
    for i,a in enumerate(cards[:150]):
        for b in cards[:150][i+1:]:
            if not set(a['assets']) & set(b['assets']) or abs(a['published']-b['published']) > 86400:
                continue
            similarity = SequenceMatcher(None,a['title'].lower(),b['title'].lower()).ratio()
            if similarity >= 0.75:
                candidates.append({'article_ids':[a['id'],b['id']], 'similarity':round(similarity,3),
                    'status':'possible_same_event_manual_review_required',
                    'independently_corroborated':False})
    return cards, candidates, excluded
