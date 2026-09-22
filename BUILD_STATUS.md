# Build status — Phase 4 complete + live multi-coin scanner (added 22 Sep 2026 UTC)

## Live scanner and dashboard (new)

Requested after phase 4: track many liquid coins (not only BTC/ETH), update a report live, show graphs, changes and linked news, and rank candidates. Codex started this work and stopped part-way (universe discovery, coin matching, schema v3). It was finished in a later session.

- `py -3 -m crypto_bot scan` runs in the foreground: hourly-cached discovery of the top 20 liquid Coinbase USD markets (stablecoins and restricted books excluded, ≥$1M approximate 24h turnover, held paper positions always kept), tickers every 60 s, four news feeds every 10 min, candles only when a new hourly candle closes.
- Dashboard at http://127.0.0.1:8765/ (local only) and `reports/live_dashboard/<mode>/index.html`, auto-refreshing, with a ranked table, 7-day sparklines, price/SMA/volume charts, supporting/opposing evidence, linked unverified news, a change log (`scanner_events`), source health and assumptions. Hourly audit reports archive to `reports/scanner/<mode>/`.
- Per-coin gating: one coin's stale data no longer blocks every other coin. A failed required feed still pauses all new entries. Missing news for a coin is shown as opposing context and never alters the rule.
- Paper cash is reserved for the widest margin over the cost hurdle first. The ranking is an ordering heuristic, not a probability.
- Unchanged feed bodies are stored once and referenced by SHA-256 afterwards, to limit database growth.
- Schema v3 adds `scanner_events`. The first `scan` or `collect` on Windows will back up each database automatically before upgrading.

- **Hosted option:** `.github/workflows/scanner.yml` publishes the dashboard to GitHub Pages about every 15 min (fresh database per run; change log carried in the Actions cache). Setup steps are in README "Always-on website". It has not run on GitHub yet.
- **First live Windows scan (22 Sep 2026 03:18 UTC):** 20 markets, all sources healthy, 4 paper BUY proposals (SUI, PEPE, NEAR, TAO) hitting the 40% exposure cap, and 9 more bullish screens left unfunded. The live database was backed up before the v3 upgrade.

### Verification

- **61 automated tests pass** (43 existing + 18 scanner tests). The first 60 were confirmed on Windows Python on 22 Sep 2026; the newest hosted-mode test has so far run only on Python 3.10 in a sandbox.
- Live Coinbase responses were checked from Chrome on this computer on 22 Sep 2026 ~03:13 UTC: 95 eligible liquid USD markets; the selected top 20 (BTC, ETH, XRP, SOL, ZEC, NEAR, SUI, DOGE, HYPE, TAO, LINK, AVAX, UNI, XLM, ADA, VVV, PEPE, LTC, HBAR, ONDO) each returned 720/720 valid closed hourly candles and spreads between 0 and 20 bps.
- Dashboard rendered and visually checked at desktop width (light) and phone width (dark) from synthetic demo data: `reports/preview_scanner_demo/` (safe to delete).
- A partial backup left by the failed sandbox attempt was moved to `data/backups/failed_vm_attempt/` (safe to delete).

### Scanner limitations

- One venue (Coinbase Exchange) and four feeds. Current-liquidity selection introduces survivorship bias and must not be used as a historical universe in phase 5.
- Coins with infrequent trades may be blocked as `stale_event` under the 5-minute ticker freshness limit.
- Coin-name news matching is heuristic. Ambiguous tickers need a `$` prefix or crypto context.
- Storage grows roughly 50–100 MB per day while scanning. No automatic pruning.

---


Verified on Windows on 21 September 2026 (America/Toronto), 22 September 2026 UTC.

## Delivered

Phases 1–3 remain intact. Phase 4 adds historical OHLCV collection, a transparent closed-candle SMA baseline, portfolio-aware paper assessments, configurable simulation costs/risk controls, explanatory source-linked HTML/JSON, and a backup-aware additive schema v2 upgrade.

The application uses the Python standard library only. Real-money execution remains disabled. No wallet, account credentials, paid service, order API, scheduler or live fill simulator was activated.

## Current verification

- **43 automated tests passed** on Python 3.12.14 / Windows.
- All original 14 tests passed before development. Their behavior remains covered; the JSON report test now explicitly reads UTF-8 for Windows compatibility.
- Latest live report as of **2026-09-22T02:40:36.785555Z**: mode `live`, research status `ready_for_review`, phase 4 status `reviewable`, `execution_enabled=false`.
- Both Coinbase tickers succeeded. Both configured feeds succeeded: 500 Ethereum Blog items and 25 CoinDesk items parsed.
- BTC-USD: **720/720 closed hourly candles**, zero gaps, 3 extra boundary rows excluded.
- ETH-USD: **720/720 closed hourly candles**, zero gaps, 3 extra boundary rows excluded.
- The default hypothetical $10,000 cash scenario produced paper BUY proposals for both assets at this snapshot. These are outputs of an unvalidated heuristic, not personal recommendations, expected-return forecasts or a proven trading edge.
- Synthetic demo remained separate and produced BTC BUY / ETH WAIT with clearly marked fixture data.
- Original database records were compared against the full pre-change backup: every original run, fetch, market and article record remained unchanged.
- Original report SHA-256 digests matched the pre-change manifest. Both upgraded databases passed `PRAGMA integrity_check`.
- Existing v1 live and demo databases received separate consistent SQLite backups before migration.
- Chrome visual QA completed using Computer Use: original report; final phase 4 assumption banner, portfolio summary, proposal/cost table, supporting/opposing evidence, source links, freshness table and expanded invalidation conditions. Encoding artifacts found in intermediate labels were fixed and rechecked in the final report. No desktop clipping or overlap was observed in the inspected sections; mobile-specific visual QA was not performed.

The earlier Coinbase timeout and unavailable-browser notes described a different environment and are superseded by these successful local checks. Connectivity is a point-in-time observation, not a permanent guarantee.

## Review artifacts

- [Final live HTML](reports/phase4/live/live_20260922T024036_785555Z.html)
- [Final live JSON](reports/phase4/live/live_20260922T024036_785555Z.json)
- [Final synthetic demo HTML](reports/phase4/demo/demo_20260922T024037_154660Z.html)
- [Reproduced first phase 4 live snapshot](reports/phase4/review/live_20260922T023333_715088Z.html)
- [Windows instructions and model rules](README.md)
- [Example hypothetical paper portfolio](paper_portfolio.example.json)

Old and intermediate reports are preserved. Use the final report linked above for the corrected labels. All reports are fixed historical snapshots; re-run collection to assess later conditions.

## Preservation

Full original project backup, including source/config, databases and reports:

`backups/before_phase4_20260922T021800487586Z/`

Automatic pre-migration database backups:

- `data/backups/live.sqlite3.v1.20260922T023331704382Z.bak`
- `data/backups/demo.sqlite3.v1.20260922T023334033504Z.bak`

No original data or report was removed. Original financial `risk_limits` remain null; phase 4 limits are separately labelled provisional simulation assumptions.

## Tests exercised

- Pagination across multiple time windows; unordered/duplicate rows; boundary and open-candle exclusion.
- Invalid, unaligned, fractional and nonfinite timestamps/values; OHLC bounds, negative volume and conflicting duplicates.
- Empty/gapped history, failed latest pages, in-progress batches, and stale candles despite fresh tickers.
- Candle revisions/reversions and observation cutoffs; no backfilled candle visibility before its first observation.
- BUY/HOLD/REDUCE/EXIT/WAIT behavior, both-sided cost arithmetic, minimum entry and shared cash/exposure sizing.
- Exposure reductions accounting for costs across assets; position loss exits and portfolio loss pauses.
- Missing held-asset marks, stale/future paper snapshots, zero equity, invalid simulation inputs and unsupported positions.
- Risk-reduction proposals despite unavailable news/candles; no news headline wording entering the numerical strategy.
- HTML escaping, preserved report collisions, source provenance and demo/live separation.
- v1 migration preservation, backup integrity and Windows connection cleanup.
- Historical v1 configurations, recorded portfolio/config reproduction, rejection of unrecorded portfolio overrides and current offline freshness checks.

Run:

```powershell
py -3 -m unittest discover -s tests -v
```

## Remaining uncertainty and later phases

- No chronological strategy validation, cash/buy-and-hold comparisons or out-of-sample predictive evidence exists yet. Phase 5 must implement these with realistic costs and observation-aware data.
- No automatic paper ledger, fills, partial fills, order rejections or live paper performance record exists. Those belong to phase 6.
- SMA periods, planning horizon, cost assumptions and risk limits are provisional; they are not approved real-money settings.
- The trend gap is not expected return and is not a probability. A threshold crossing does not prove costs will be recovered.
- Fees/spread/slippage approximate friction without depth, impact, precision rules or guaranteed liquidity.
- Risk controls are manual-review triggers, not guaranteed maximum realized losses.
- News statements remain unverified; evidence matching, contradiction review and publisher independence still need human evaluation.
- Historical backfills observed today cannot be treated as known at their original candle time in an observation-based historical replay.
- Public source availability, completeness and revisions can change. Coverage remains limited to one market venue and two news feeds.
