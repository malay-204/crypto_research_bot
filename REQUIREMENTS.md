# Phase 1 — Requirements and acceptance checklist

## Scope

Build a source-backed, read-only research foundation for BTC and ETH. Required outputs: raw evidence archive, source-health assessment, attributable research cards and a timestamped HTML/JSON report. The system must remain useful even when its conclusion is that information is insufficient.

## Current defaults

| Setting | Value | Status |
| --- | --- | --- |
| Data venue | Coinbase Exchange public API | Adapter default, not an execution choice |
| Instruments | BTC-USD and ETH-USD | Initial supported universe |
| Planned forecast horizon | 72 hours | Provisional; no model yet |
| Planned review cadence | Hourly plus manual refresh | Documented; no scheduler installed |
| Execution | Disabled | Enforced; no order endpoints implemented |
| Information lookback | 7 days | Draft research window |
| Ticker freshness limit | 5 minutes | Prototype data-quality threshold |
| Feed poll freshness limit | 2 hours | Prototype data-quality threshold |
| Maximum spread | 50 basis points | Prototype filter, not validated trading rule |

Pending user choices: execution exchange, preferred holding period, monthly data/hosting budget, portfolio exposure and loss limits, delivery interface. No account credentials are needed for these phases.

## Acceptance criteria

### Configuration

- Invalid pairs, duplicate sources and execution-enabled configurations are rejected.
- Configuration is recorded for every collection run.
- The interface makes provisional settings and research-only scope clear.

### Collection

- Every poll records success/failure, source URL, UTC receipt time, raw body when received and content digest.
- Market records retain the source's event time separately from collection time.
- Missing, malformed, nonfinite, stale, future or crossed market values cannot produce a healthy report.
- A failed new poll is not hidden by the previous successful poll.
- Failures in one source do not erase or stop other source records.
- Live collection never imports demo data as a fallback.

### Evidence processing

- Original source, article URL, publication time and first observation of each revision are inspectable.
- Unchanged repeated articles are deduplicated; changed and reverted statements remain historically reproducible.
- Exact canonical URL matching strips tracking parameters only. Similar headlines are flagged for human review without claiming independent corroboration.
- Missing/future publication times and out-of-window evidence are excluded from the current evidence set, with counts reported and raw evidence retained.
- Explicit BTC/ETH matching and review hints are labelled as heuristics.
- Potentially opposing statements remain visible; no automated claim of verified causality is made.
- Untrusted HTML/XML cannot run code in the generated report.

## Deployment boundary

Passing synthetic tests demonstrates software behaviour. Live-source checks must independently demonstrate connectivity. Data availability does not demonstrate forecasting value. The first three phases end with research readiness, not trading readiness.

## Phase 4 — Paper assessment acceptance (implemented)

The phase 1 scope above is retained as the original foundation. Phase 4 extends it under an explicitly paper-only boundary.

- Collect closed OHLCV history using bounded pagination, source-linked raw responses and UTC bucket alignment.
- Reject malformed, nonfinite or inconsistent rows; deduplicate identical observations and preserve revisions.
- Freeze the complete-candle watermark for each run; exclude open candles and extra endpoint boundaries.
- Record batch windows, counts, missing intervals and failures. Block numerical assessments on gaps, insufficient/stale candles or a failed/incomplete newest batch.
- Use a transparent 24/72-candle SMA baseline before any machine-learning model. Expose formula, inputs and costs; never call a heuristic score a calibrated probability.
- Produce portfolio-aware BUY, HOLD, REDUCE/EXIT or WAIT paper proposals without filling them or updating balances.
- Account for both-sided fees, conservative observed/floor spread and slippage. Share cash/exposure budgets across assets; account for the effect of costs on equity.
- Apply configurable position/portfolio exposure, entry-size and loss controls. Treat all defaults as provisional simulation assumptions, separate from unset real-money limits.
- Reject unsupported or unpriced held assets and invalid portfolio values; block stale/future manual paper snapshots.
- Preserve supporting and opposing evidence, freshness, limitations, provenance and invalidation conditions in HTML and JSON.
- Keep unverified news outside numerical strategy inputs and retain publication and first-observation times.
- Preserve original data/reports; back up v1 databases before additive migrations; reject demo/live mixing.
- Keep execution disabled. No wallets, real orders, service purchases or unattended schedules.

Acceptance evidence and artifact paths are recorded in BUILD_STATUS.md. Tests establish software behavior only. Phase 5 historical validation and phase 6 live paper simulation remain separate work.
