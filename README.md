# Crypto Research Bot — Phases 1–4 + live scanner

A Python standard-library research assistant for BTC-USD and ETH-USD. It collects public Coinbase Exchange tickers and historical OHLCV, retains source-linked RSS/Atom evidence, and produces offline HTML/JSON reports with transparent, portfolio-aware **paper assessments**.

**Real-money execution is disabled.** There are no wallets, private API keys, order endpoints, fill engine, unattended scheduler or purchased services. BUY/HOLD/REDUCE/EXIT/WAIT labels are unvalidated paper proposals, not a promise of profit or a calibrated probability. Phase 4 does not establish a live trading track record.

## Windows quick start

Open PowerShell in the project folder:

```powershell
Set-Location 'D:\Downloads\Crypto_Research_Bot_Phases_1_to_3\crypto_research_bot'
py -3 --version
py -3 -m crypto_bot validate
py -3 -m unittest discover -s tests -v
py -3 -m crypto_bot collect --out reports/phase4/live
```

Open the HTML path printed by the last command, or open the newest report:

```powershell
$reportFile = Get-ChildItem -LiteralPath reports/phase4/live -Filter *.html |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Start-Process -FilePath $reportFile.FullName
```

If `py` is not available, use `python` or the full path to your installed Python interpreter. Python 3.11+ is recommended. The current verification used Python 3.12.14 on Windows. No pip packages are required. Linux/macOS: use `python3` from the project root.

For synthetic demonstration data in its separate database:

```powershell
py -3 -m crypto_bot demo --out reports/phase4/demo
```

Demo values are explicitly labelled. A demo success never substitutes for live connectivity.

## Live multi-coin scanner and dashboard

The `scan` command widens coverage from BTC/ETH to the most liquid Coinbase Exchange USD markets and keeps a dashboard updated while it runs in the foreground:

```powershell
Set-Location 'D:\Downloads\Crypto_Research_Bot_Phases_1_to_3\crypto_research_bot'
py -3 -m crypto_bot scan
```

Then open **http://127.0.0.1:8765/** in your browser (or the `index.html` file path it prints). The page reloads itself every 60 seconds. Press **Ctrl+C** in PowerShell to stop; the page then shows a "stale" warning. Nothing keeps running after you close the window, and no scheduled task or service is installed.

Useful options:

```powershell
py -3 -m crypto_bot scan --demo                 # synthetic markets, separate demo database
py -3 -m crypto_bot scan --cycles 1 --no-serve  # one refresh, file only, then exit
py -3 -m crypto_bot scan --port 8800            # different local port
py -3 -m crypto_bot scan --portfolio paper_portfolio.example.json
```

What each refresh does:

- **Market discovery (hourly, cached):** `/products`, `/products/stats` and `/currencies`. Keeps online `*-USD` books with no trading restrictions, excludes stablecoins, requires approximate 24h turnover (base volume × last price) of at least $1M, and tracks the top 20 by turnover. Held paper positions are always kept for risk review. Coverage is one venue, not the whole crypto market.
- **Tickers every 60 s; news feeds every 10 min** (CoinDesk, Ethereum Foundation Blog, plus Cointelegraph and Decrypt as optional sources); **hourly candles only when a new candle has closed**, with a 10-minute retry for failed batches. Requests are spaced ≥0.2 s apart.
- **Same phase 4 rule for every coin:** SMA(24)/SMA(72) on closed hourly candles, cost-adjusted entry hurdle, shared cash and exposure caps. In scanner mode, paper cash is reserved for the widest *margin over the hurdle* first.
- **Per-coin gating:** a stale ticker or missing candles blocks only that coin. A failed required news feed (CoinDesk) still pauses new entries for every coin. A coin with no matched news is not blocked; the gap is shown as opposing context. News never changes the numerical rule.
- **Change log:** action changes, coins entering/leaving the universe, source status changes and newly matched headlines are stored in the `scanner_events` table and shown on the page.

The dashboard shows a ranked table with 7-day sparklines, detailed price/SMA/volume charts for the top 6 and any holdings, supporting and opposing evidence, linked unverified news, source health and all assumptions. **The ranking is an ordering heuristic, not a probability of profit or a list of coins "most likely to go up."** The newest refresh is written to `reports/live_dashboard/<mode>/index.html` and `state.json` (overwritten each minute); a full audit report is archived hourly under `reports/scanner/<mode>/`.

Storage grows while scanning (raw responses are kept for audit): roughly 50–100 MB per day at the default settings (unchanged feed bodies are stored once and referenced by SHA-256 afterwards). Stop the scanner when you are not using it.

Coin-name matching for news is conservative: ambiguous tickers such as NEAR, OP or LINK only match with a `$` prefix or crypto context. Review matches manually.

## Always-on website (GitHub Pages, free)

`.github/workflows/scanner.yml` runs one scan about every 15 minutes on GitHub's servers and publishes the dashboard as a website you can open from any device, even with your PC off. It uses only public data. There are no secrets, API keys, wallets or orders. Each run starts from a fresh database. Only the change log (`memory/scanner_memory.json`) is carried between runs, using the Actions cache.

One-time setup, using GitHub Desktop (https://desktop.github.com):

1. Sign in to GitHub Desktop (create a free GitHub account first if needed).
2. **File → Add local repository →** choose this project folder. When it says the folder is not a repository, click **create a repository**, then **Create repository**. `.gitignore` keeps `data/`, `reports/` and `backups/` out, so only code, tests, config and docs are uploaded.
3. Click **Commit to main**, then **Publish repository**. **Untick "Keep this code private"**: free GitHub Pages needs a public repository, and public repositories get unlimited Actions minutes.
4. On github.com, open the repository → **Settings → Pages → Build and deployment → Source: GitHub Actions**.
5. **Actions** tab → allow workflows if asked → **Crypto scanner (paper only) → Run workflow**.
6. After about 2 minutes the site is live at `https://<your-username>.github.io/<repository-name>/`. Bookmark it on your phone.

To stop it: **Actions → Crypto scanner (paper only) → ⋯ → Disable workflow**.

Good to know:

- The page shows when it was last updated and turns on a "stale" warning if a run is late. GitHub often delays scheduled runs by 5–20 minutes and occasionally skips them.
- GitHub disables scheduled workflows after 60 days with no commits to the repository (it emails you first). Re-enable the workflow from the Actions tab.
- Everything in the repository and on the page is public. The dashboard shows only the hypothetical $10,000 paper scenario.
- If a news site blocks GitHub's servers, the Sources table shows the error. A failed CoinDesk feed (the required one) pauses all new paper BUYs by design.
- The local `py -3 -m crypto_bot scan` still works and updates every 60 s. The two do not share data.


- Paginated OHLCV collection: 720 closed hourly candles (30 days) per pair by default, with at most 300 time buckets per request.
- UTC timestamp alignment, positive finite prices, OHLC bounds and nonnegative volume validation. Open candles and out-of-range boundary rows are excluded.
- Identical candle observations are deduplicated; changed and reverted candles retain distinct revisions, raw source fetches and first-observed times.
- A failed, incomplete or unfinished newest collection cannot be concealed by previously successful data. Gaps are blocked, never forward-filled.
- A deterministic moving-average baseline and portfolio-aware paper BUY, HOLD, REDUCE/EXIT or WAIT assessments.
- Explicit costs, shared cash reservations, exposure limits, position loss exits and a reference-equity loss pause.
- Supporting numerical evidence, opposing evidence/uncertainty, source links, freshness checks, limitations and invalidation conditions.
- Additive SQLite migration with an integrity-checked backup before upgrading existing v1 databases.

Candles are collected before tickers so the final report uses the freshest available ticker snapshot. Collection runs only when requested. This implementation rechecks the configured historical window on each manual collection, rather than maintaining a streaming cache. Avoid rapid polling; the upstream documentation discourages frequent candle polling.

## Baseline and action rules

The default uses the last 72 contiguous closed hourly closes:

1. Calculate the 24-candle and 72-candle simple moving averages.
2. Calculate the gap in basis points: `(fast / slow - 1) * 10,000`.
3. A bullish screen requires gap greater than `max(25 bps, estimated round-trip cost)` and the latest closed-candle price above the fast average.
4. A bearish screen requires gap below `-25 bps` and the latest close below the fast average.
5. Other valid inputs produce a neutral screen.

This is a **trend heuristic**, not an expected-return model. Comparing its gap with costs is a transparent screening rule; it does not demonstrate that future returns will cover those costs. The provisional 72-hour planning horizon is report context, not a forecast or guaranteed holding period.

| Assessment | Meaning |
| --- | --- |
| BUY | Bullish screen and a positive entry fits shared cash, minimum size and all simulation caps after costs. |
| HOLD | A position exists, usable inputs show no exit trigger, or no additional entry fits the limits. |
| REDUCE/EXIT | An existing position triggers an exposure/loss control or the bearish baseline. The proposed quantity distinguishes partial reduction from full exit. |
| WAIT | A flat portfolio lacks an entry condition, a required input is unusable, cash/capacity is insufficient, or entries are paused. |

Risk exits require usable portfolio and ticker marks, and can still be shown if candles or news are unavailable. Such a report retains its blocked input status and explains the missing evidence. If a held asset cannot be marked, the entire portfolio assessment waits because exposure and losses cannot be measured reliably.

## Simulation assumptions

All values under `phase4` in `config.json` are labelled `provisional_simulation_only`. Existing top-level real-money `risk_limits` remain unset. No execution exchange or personal financial risk approval is implied.

| Cost / limit | Default |
| --- | --- |
| Fee per side | 60 bps |
| Full spread floor | 10 bps; use the greater of this and the observed ticker spread |
| Slippage per side | 10 bps |
| Single-position exposure | 20% |
| Total crypto exposure | 40% |
| Maximum entry per review | 10% of sizing equity per asset |
| Position loss exit | 8% below supplied average cost |
| Portfolio loss pause | 10% below supplied reference equity |
| Minimum new entry | $10 mid notional |
| Manual paper snapshot age | At most 24 hours |

One-way friction = fee + slippage + half spread. It applies on buys and sells. Fees use mid notional; assumed fill prices include half spread and slippage. These are approximate simulation costs, not an actual venue fee quote.

Entry sizing reserves cash and exposure across assets in the configured pair order. It does not spend expected sale proceeds. Existing holdings reserve estimated liquidation costs when sizing exposure ceilings; entry costs further reduce equity. Entry trade-size limits do not prevent full risk exits, and minimum entry sizes do not block necessary reductions. Prices are public snapshots without order-book depth, order-size impact, venue precision/minimum-size rules or guaranteed fills. Loss limits are review triggers; gaps and worse fills can exceed them.

## Portfolio inputs

The default is a **hypothetical $10,000 all-cash scenario**, not your actual holdings or a persistent simulated account. Reports do not update cash, positions, a high-water mark or a realized P&L ledger.

To evaluate another hypothetical portfolio, copy and edit the example:

```powershell
Copy-Item -LiteralPath paper_portfolio.example.json -Destination paper_portfolio.json
notepad paper_portfolio.json
py -3 -m crypto_bot collect --portfolio paper_portfolio.json --out reports/phase4/custom
```

Do not overwrite an existing personal scenario file with the example. Supported fields:

```json
{
  "kind": "hypothetical_scenario",
  "cash_usd": 9000,
  "reference_equity_usd": 10000,
  "positions": {
    "ETH-USD": {"quantity": 0.4, "average_cost_usd": 2500}
  }
}
```

For a manually maintained paper snapshot, use `"kind": "paper_snapshot"` and supply a timezone-aware `"as_of"` timestamp representing when you confirmed that simulated state. Future or stale snapshots block assessments. Quantities must be nonnegative, cost bases positive, and all held instruments must be in the configured BTC/ETH universe. Average cost should reflect your chosen basis consistently; record entry fees there if appropriate.

The portfolio, costs and risk assumptions are recorded with each collection run. `--portfolio` is only accepted for `collect` and `demo`. Offline reports use the recorded run inputs, so an unrelated current config cannot silently replace historical portfolio assumptions. An explicit report as-of time before a candle or news revision was first observed cannot access that revision.

## Reports and freshness

```powershell
py -3 -m crypto_bot report --out reports/phase4/live
py -3 -m crypto_bot report --as-of 2026-09-22T02:33:33.715088Z --out reports/phase4/historical
```

The example historical timestamp corresponds to the first phase 4 live verification in this project. Use a timestamp after an actual collection in your database. Reports are fixed snapshots, not live dashboards; stale inputs can produce WAIT later. The JSON includes configuration, portfolio, candle revisions, fetch IDs, request URLs and raw-evidence references.

- `research_status`: inherited ticker/news readiness (`ready_for_review`, `degraded` or `blocked`). It measures availability, not predictive accuracy.
- `phase4.status`: `reviewable` or `inputs_blocked`. A reviewable assessment can still be WAIT.
- `execution_enabled`: always false.
- Exit code 0: report produced without a blocking input. Exit code 2: report produced with research/assessment inputs blocked. Exit code 1: command, config or storage error.

No report is overwritten on a timestamp collision. Old phase 1–3 reports and data remain available. `report` defaults to the live database; `demo` creates a new synthetic snapshot in the demo database.

## Evidence boundaries

Ethereum Foundation Blog and CoinDesk RSS/Atom statements remain attributed and **not independently verified**. Publication time and the first observation of each revision are separate. Missing, future, old or unmatched articles are excluded from current evidence with counts retained.

Asset mentions, headline similarity and possible common-event flags are heuristic review aids. They are not calibrated probabilities or proof of independent corroboration. Conflicting statements remain visible. News is linked into each assessment for manual supporting/opposing review; no extracted claim, sentiment, similarity score or source text enters the numerical strategy or changes configuration.

No source silence is interpreted as a positive or negative signal. Generic macro headlines without explicit BTC/ETH mentions remain outside this initial matching scope. Full article scraping, verified causality, semantic contradiction resolution, on-chain signals and cross-venue checks are not implemented.

## Storage and preservation

| Path | Purpose |
| --- | --- |
| `crypto_bot/core.py`, `pipeline.py` | Existing evidence, public collectors and snapshots |
| `crypto_bot/schema.py` | Schema v3 and backup-aware upgrade |
| `crypto_bot/candles.py` | Historical collection, validation, revisions and as-of views |
| `crypto_bot/settings.py`, `strategy.py` | Simulation validation and numerical assessments |
| `crypto_bot/report.py`, `phase4_report.py` | Escaped HTML/JSON |
| `crypto_bot/universe.py`, `matching.py` | Liquid-market discovery and conservative coin-name matching |
| `crypto_bot/scanner.py`, `dashboard.py` | Foreground scan loop, change log, local server and live dashboard |
| `tests/test_scanner.py` | Universe, gating, due-polling, look-ahead, escaping and server tests |
| `tests/test_pipeline.py`, `test_phase4.py` | Regression and failure-case tests |
| `data/live.sqlite3`, `data/demo.sqlite3` | Separate actual and synthetic evidence |
| `data/backups/` | Automatic consistent SQLite pre-migration backups |
| `backups/before_phase4_20260922T021800487586Z/` | Full original project backup and SHA-256 manifest |
| `reports/phase4/` | Phase 4 review artifacts |
| `reports/live_dashboard/`, `reports/scanner/` | Latest scanner dashboard and hourly archived scanner reports |
| `backups/before_live_dashboard_20260922T025426394755Z/` | Full project backup taken before the scanner work |

Original tables retain all records. New tables are `candles` (append-only changed OHLCV revisions), `candle_batches` (pagination windows, completeness and failures) and, in v3, `scanner_events` (dashboard change log). Raw candle responses use the existing `fetches` table. No pruning runs automatically. Stop other bot processes before a migration and run one collector at a time. An unknown newer database version is rejected.

To restore a backup, stop the bot and restore to a separate filename first, keeping the current database intact. SQLite viewers should be closed when restoring on Windows. The app automatically upgrades a v1 copy again if opened with this version.

## Remaining phases

**Phase 5:** chronological historical validation with realistic two-sided costs, cash and buy-and-hold comparisons, walk-forward/held-out decisions and explicit selection bias. Do not treat a present-day backfill as data that was historically observable. News must never be used before its first observation, and later revisions must not rewrite earlier assessments.

**Phase 6:** live paper trading with a persistent simulated ledger, realistic fills, partial fills/rejections, restart safety and reconciled accounting. Implementing that simulator does not establish a successful live track record.

No predictive value or profitability is established by phase 4 or its tests.

## Official source documentation

- [Coinbase Exchange candle endpoint](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles): bucket schema, pagination constraints and missing intervals.
- [Coinbase Exchange ticker endpoint](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker): last trade, bid/ask and volume.
- [Ethereum Foundation Blog feed](https://blog.ethereum.org/feed.xml).
- [CoinDesk feed](https://www.coindesk.com/arc/outboundfeeds/rss/).
- [Coinbase Exchange products](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-all-known-trading-pairs), product stats and currencies endpoints used for discovery.
- [Cointelegraph feed](https://cointelegraph.com/rss) and [Decrypt feed](https://decrypt.co/feed) (optional scanner sources).

No wallets, credentials, trading orders, paid services or unattended schedules are activated.
