"""Offline HTML and structured JSON reports; all external text is escaped."""
import html
import json
from pathlib import Path
from .core import iso


def render(report,output):
    target = Path(output)
    target.mkdir(parents=True,exist_ok=True)
    stamp = report['as_of'].replace(':','').replace('-','').replace('.','_')
    stem = f'{report["mode"]}_{stamp}'
    # Never replace a previous snapshot, even with the same --as-of value.
    if (target/(stem+'.json')).exists() or (target/(stem+'.html')).exists():
        import uuid
        stem += '_' + uuid.uuid4().hex[:8]
    e = lambda s: html.escape(str(s if s is not None else 'Not available'),quote=True)
    def table(headers,rows):
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+e(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+e(c)+'</td>' for c in r)+'</tr>' for r in rows)+'</tbody></table></div>'
    health = table(['Source','Status','Last poll (UTC)','Age (s)','Detail'],[
        [h['source'],h['status'],h['last_poll'],h['age_seconds'],h['error'] or ('Required' if h['required'] else 'Optional')] for h in report['health']])
    market = table(['Pair','Last trade (USD)','Bid / ask','Spread (bps)','24h base volume','Last trade time (UTC)','Status'],[
        [m['pair'],f'{m["price"]:,.2f}',f'{m["bid"]:,.2f} / {m["ask"]:,.2f}',f'{m["spread_bps"]:.2f}',f'{m["volume"]:,.3f}',iso(m['event_time']),m['data_status']] for m in report['markets']])
    cards = []
    for a in report['evidence']:
        cards.append(f'<article id="evidence-{a["id"]}"><div class="meta">{e(", ".join(a["assets"]))} · {e(a["source"])} · {e(a["kind"])}</div>'
            f'<h3><a href="{e(a["url"])}" target="_blank" rel="noopener noreferrer">{e(a["title"])}</a></h3>'
            f'<p>{e(a["excerpt"])}</p><div class="meta">Published {e(iso(a["published"]))} · First observed for this revision {e(iso(a["observed"]))}</div>'
            f'<p class="note">Source statement; not independently verified. Review flags: {e(", ".join(a["review_flags"]) or "None detected; this does not establish reliability")}. Evidence #{a["id"]}</p></article>')
    duplicate_rows = table(['Evidence IDs','Headline similarity','Review status'],[
        [str(x['article_ids']),x['similarity'],x['status']] for x in report['possible_duplicate_events']])
    reasons = ''.join('<li>'+e(x)+'</li>' for x in report['blocking_reasons'])
    notes = ''.join('<li>'+e(x)+'</li>' for x in report['limitations'])
    excluded = table(['Reason excluded from current report','Count'],list(report['excluded_evidence'].items()))
    demo = '<div class="demo">SYNTHETIC DEMONSTRATION — prices, dates and headlines below are test fixtures.</div>' if report['mode']=='demo' else ''
    phase4 = ''
    if report.get('phase4'):
        from .phase4_report import section
        phase4 = section(report['phase4'])
    phase_label = 'PHASE 4 · PAPER DECISION ASSISTANT' if phase4 else 'PHASES 1–3 · RESEARCH FOUNDATION'
    text = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
    <title>Crypto Research — {e(report['mode'])}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#0c1422;color:#e8eef6;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:36px 22px}}
    h1{{font-size:36px;line-height:1.15}}h2{{margin-top:36px}}h3{{line-height:1.4;margin:8px 0}}a{{color:#8bd8ff}}.meta,.note{{font-size:13px;color:#b5c4d7}}.demo{{background:#ffe39b;color:#30250d;padding:16px;font-weight:700;border-radius:10px}}
    .banner,article{{background:#172437;padding:20px;border:1px solid #31415a;border-radius:12px;margin:16px 0}}.badge{{display:inline-block;background:#273b52;color:#b4e5ff;border-radius:20px;padding:4px 14px;font-weight:700}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #31415a;vertical-align:top}}th{{color:#b4e5ff}}.scroll{{overflow-x:auto}}li{{margin:8px 0}}footer{{margin-top:36px;color:#b5c4d7}}.evidence-columns{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}summary{{cursor:pointer;color:#8bd8ff;padding:10px 0}}h4{{margin-bottom:8px}}td{{overflow-wrap:anywhere}}@media(max-width:700px){{.evidence-columns{{grid-template-columns:1fr}}main{{padding:22px 14px}}h1{{font-size:30px}}}}
    </style></head><body><main>{demo}<p class="meta">{phase_label}</p><h1>Crypto Research Bot</h1>
    <p>Evidence first. Every source and timestamp remains inspectable.</p><div class="banner"><span class="badge">{e(report['research_status'].upper())}</span><h2>{e(report['action'])}</h2>
    <p>As of {e(report['as_of'])} · Planned horizon {report['forecast_horizon_hours']} hours · Execution disabled</p><ul>{reasons}</ul></div>
    {phase4}<h2>Market observations</h2><p class="note">Snapshot values; no trend or price prediction is inferred.</p>{market or ''}
    <h2>Source health</h2>{health}<h2>Source-linked news claims</h2><p>Unverified source statements. Review both supporting and opposing narratives; these claims do not enter the numerical strategy.</p>{''.join(cards) or '<p>No eligible evidence. Missing data is not a neutral market signal.</p>'}
    <h2>Possible repeated events</h2><p class="note">Similarity does not establish corroboration. Conflicting headlines remain separate for review.</p>{duplicate_rows}
    <h2>Excluded evidence</h2>{excluded}<h2>Known limits</h2><ul>{notes}</ul>
    <footer>Read-only research and paper assessment prototype · No real orders or simulated fills · Open the matching JSON for complete provenance and configuration.</footer></main></body></html>'''
    json_path,html_path = target/(stem+'.json'), target/(stem+'.html')
    json_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    html_path.write_text(text,encoding='utf-8')
    return html_path,json_path
