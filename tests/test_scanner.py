import copy
import json
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from crypto_bot.core import connect, load_config
from crypto_bot.matching import match_entities
from crypto_bot.scanner import (cycle, chart_series, demo_scanner_transport, diff_events, publish,
                                run, scanner_settings, serve, throttled)
from crypto_bot.universe import select_products, DEFAULT_SCANNER

ROOT = Path(__file__).resolve().parents[1]
NOW = 1800000000.0


def product(pair, **kw):
    base = pair.split("-")[0]
    return dict({"id":pair,"base_currency":base,"quote_currency":pair.split("-")[1],"status":"online"}, **kw)


class UniverseTests(unittest.TestCase):
    def test_selection_filters_ranks_and_keeps_held(self):
        products = [product("BTC-USD"), product("SOL-USD"), product("USDC-USD"), product("BTC-EUR"),
                    product("OFF-USD", status="delisted"), product("LIM-USD", limit_only=True),
                    product("TINY-USD"), product("HELD-USD")]
        stats = {p:{"stats_24hour":{"last":str(last),"volume":str(vol),"open":str(last)}} for p,last,vol in
                 [("BTC-USD",60000,100),("SOL-USD",150,1e6),("USDC-USD",1,1e10),("OFF-USD",1,1e9),
                  ("LIM-USD",1,1e9),("TINY-USD",1,10),("HELD-USD",1,5)]}
        settings = dict(DEFAULT_SCANNER, max_assets=1)
        chosen, eligible, liquid = select_products(products, stats, [{"id":"SOL","name":"Solana"}], settings, held=["HELD-USD"])
        self.assertEqual([r["pair"] for r in chosen], ["SOL-USD","HELD-USD"])
        self.assertEqual((eligible, liquid), (4, 2))
        self.assertEqual(chosen[0]["name"], "Solana")
        self.assertTrue(chosen[1]["retained_for_risk_review"])

    def test_no_liquid_market_is_an_error_not_a_fallback(self):
        with self.assertRaises(ValueError):
            select_products([product("TINY-USD")], {"TINY-USD":{"stats_24hour":{"last":"1","volume":"1"}}}, [], DEFAULT_SCANNER)
        with self.assertRaises(ValueError):
            select_products({"bad":1}, {}, [], DEFAULT_SCANNER)

    def test_ambiguous_names_need_crypto_context(self):
        aliases = {"NEAR":["NEAR Protocol","Near"], "SOL":["Solana"], "OP":["Optimism"]}
        self.assertEqual(match_entities("Rates stay high in the near term", aliases), [])
        self.assertEqual(match_entities("$NEAR rallies", aliases), ["NEAR"])
        self.assertEqual(match_entities("Near protocol token upgrade ships", aliases), ["NEAR"])
        self.assertEqual(match_entities("Solana validators upgrade", aliases), ["SOL"])
        self.assertEqual(match_entities("OP ED: markets", aliases), [])
        self.assertEqual(match_entities("Ether and Bitcoin slip", aliases), ["BTC","ETH"])


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.temp.name)/"demo.sqlite3", "demo")
        self.c = load_config(ROOT/"config.json")
        self.c["scanner"] = scanner_settings(self.c)
        self.calls = []
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def transport(self, now=NOW, fail=()):
        base = demo_scanner_transport(now)
        def call(url, c):
            self.calls.append(url)
            if any(f in url for f in fail):
                return 503, b"unavailable", {}
            return base(url, c)
        return call
    def scan(self, state=None, now=NOW, fail=()):
        state = {} if state is None else state
        return cycle(self.db, self.c, state, self.transport(now, fail), lambda: now), state
    def actions(self, report):
        return {a["pair"]:a for a in report["phase4"]["assessments"]}

    def test_universal_scan_ranks_and_allocates_by_margin(self):
        report, _ = self.scan()
        acts = self.actions(report)
        self.assertEqual(set(acts), {"BTC-USD","ETH-USD","SOL-USD","DOGE-USD","LINK-USD","NEAR-USD"})
        self.assertNotIn("USDC-USD", acts)
        self.assertEqual(acts["SOL-USD"]["action"], "BUY")
        self.assertEqual(report["phase4"]["allocation_order"][0], "SOL-USD")
        self.assertEqual(report["scanner"]["ranking"][:2], ["SOL-USD","BTC-USD"])
        self.assertEqual(report["research_status"], "ready_for_review")
        self.assertFalse(report["execution_enabled"])
        self.assertIn("not a probability", report["scanner"]["ranking_note"])
        sol_news = [e for e in report["evidence"] if "SOL" in e["assets"]]
        self.assertTrue(sol_news and all("verified" in e["status"] for e in sol_news))
        self.assertTrue(all(a["proposed_trade"] is None or a["proposed_trade"]["status"]=="proposal_only_not_filled"
                            for a in acts.values()))

    def test_one_bad_coin_does_not_block_others(self):
        report, _ = self.scan(fail=("LINK-USD/ticker",))
        acts = self.actions(report)
        self.assertEqual(acts["LINK-USD"]["action"], "WAIT")
        self.assertIn("market:LINK-USD: error", acts["LINK-USD"]["blocking_reasons"])
        self.assertEqual(acts["SOL-USD"]["action"], "BUY")
        self.assertEqual(report["research_status"], "degraded")
        self.assertEqual(report["blocking_reasons"], [])

    def test_required_feed_failure_still_blocks_new_entries_everywhere(self):
        report, _ = self.scan(fail=("coindesk",))
        self.assertEqual(report["research_status"], "blocked")
        self.assertFalse(any(a["action"]=="BUY" for a in report["phase4"]["assessments"]))

    def test_polls_only_due_sources(self):
        _, state = self.scan()
        first = len(self.calls); self.calls.clear()
        self.scan(state, now=NOW+60)
        self.assertFalse(any("/candles?" in u for u in self.calls))
        self.assertFalse(any("rss" in u or "feed" in u for u in self.calls))
        self.assertFalse(any(u.endswith("/products") for u in self.calls))
        self.assertEqual(sum(u.endswith("/ticker") for u in self.calls), 6)
        self.assertGreater(first, len(self.calls))
        self.calls.clear()
        self.scan(state, now=NOW+self.c["scanner"]["feed_refresh_seconds"]+1)
        self.assertEqual(sum("rss" in u or "feed" in u for u in self.calls), 4)
        self.calls.clear()
        self.scan(state, now=(NOW//3600+1)*3600+5)
        self.assertTrue(any("/candles?" in u for u in self.calls))

    def test_unchanged_feed_body_is_referenced_not_restored(self):
        _, state = self.scan()
        later = NOW+self.c["scanner"]["feed_refresh_seconds"]+1
        cycle(self.db, self.c, state, self.transport(NOW), lambda: later)
        rows = self.db.execute("SELECT body,headers,sha256 FROM fetches WHERE source='feed:coindesk' ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows[0]["body"])
        self.assertIsNone(rows[1]["body"])
        self.assertEqual(json.loads(rows[1]["headers"])["body_sha256"], rows[0]["sha256"])

    def test_failed_candles_retry_after_delay(self):
        _, state = self.scan(fail=("SOL-USD/candles",))
        self.calls.clear()
        self.scan(state, now=NOW+60)
        self.assertFalse(any("SOL-USD/candles" in u for u in self.calls))
        self.scan(state, now=NOW+self.c["scanner"]["candle_retry_seconds"]+1)
        self.assertTrue(any("SOL-USD/candles" in u for u in self.calls))

    def test_discovery_failure_keeps_last_universe_and_says_so(self):
        with self.assertRaises(ValueError):
            self.scan(fail=("/products",))
        report, state = self.scan()
        report, _ = self.scan(state, now=NOW+self.c["scanner"]["discovery_refresh_seconds"]+1, fail=("/products",))
        self.assertIn("discovery_error", report["config"]["research_universe"])
        self.assertEqual(len(report["phase4"]["assessments"]), 6)
        self.assertTrue(any("discovery failed" in e["message"] for e in report["scanner"]["events"]))

    def test_change_events(self):
        report, state = self.scan()
        state["previous"]["actions"]["SOL-USD"] = "WAIT"
        state["previous"]["actions"]["GONE-USD"] = "WAIT"
        state["previous"]["evidence"] = set()
        diff_events(self.db, state["previous"], report, NOW+1)
        messages = [r["message"] for r in self.db.execute("SELECT message FROM scanner_events")]
        self.assertIn("SOL-USD: paper assessment WAIT → BUY", messages)
        self.assertIn("GONE-USD left the tracked universe", messages)
        self.assertTrue(any(m.startswith("New unverified item") for m in messages))

    def test_chart_has_no_look_ahead(self):
        report, _ = self.scan()
        rows = chart_series(self.db, report["config"], "SOL-USD", NOW)
        self.assertEqual(len(rows), 168)
        self.assertTrue(all(r["start"]+3600 <= NOW and r["observed"] <= NOW for r in rows))
        self.assertEqual(chart_series(self.db, report["config"], "SOL-USD", NOW-1), [])

    def test_dashboard_escapes_and_labels(self):
        base = demo_scanner_transport(NOW)
        def hostile(url, c):
            if "cointelegraph" in url:
                return 200, (b'<rss><channel><item><title>&lt;script&gt;alert(1)&lt;/script&gt; Solana "A&amp;B" launch</title>'
                             b'<link>https://cointelegraph.com/x</link><pubDate>Thu, 15 Jan 2027 07:00:00 +0000</pubDate>'
                             b'</item></channel></rss>'), {}
            return base(url, c)
        report = cycle(self.db, self.c, {}, hostile, lambda: NOW)
        out = Path(self.temp.name)/"dash"
        page = publish(report, out).read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", page)
        self.assertIn("Solana &quot;A&amp;B&quot; launch", page)
        self.assertIn("PAPER ONLY", page)
        self.assertIn("SYNTHETIC DEMO", page)
        self.assertIn("<svg", page)
        self.assertIn('http-equiv="refresh"', page)
        self.assertEqual(json.loads((out/"state.json").read_text(encoding="utf-8"))["execution_enabled"], False)

    def test_bounded_run_archives_and_publishes(self):
        out, archive = Path(self.temp.name)/"live", Path(self.temp.name)/"archive"
        clock = iter([NOW+i for i in range(100)])
        run(self.db, self.c, out, archive, cycles=2, transport=self.transport(),
            clock=lambda: next(clock), sleep=lambda s: None, echo=lambda s: None)
        self.assertTrue((out/"index.html").exists())
        self.assertEqual(len(list(archive.glob("*.html"))), 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM runs").fetchone()[0], 2)

    def test_hosted_runs_carry_memory_across_fresh_databases(self):
        memory, out = Path(self.temp.name)/"memory.json", Path(self.temp.name)/"site"
        quiet = dict(sleep=lambda s: None, echo=lambda s: None, memory_path=memory, page_refresh=900, hosted=True)
        run(self.db, self.c, out, Path(self.temp.name)/"a1", cycles=1, transport=self.transport(), clock=lambda: NOW, **quiet)
        fresh = connect(Path(self.temp.name)/"fresh.sqlite3", "demo")
        try:
            later = NOW+900
            state = run(fresh, self.c, out, Path(self.temp.name)/"a2", cycles=1, transport=self.transport(NOW),
                        clock=lambda: later, **quiet)
            kinds = [r["kind"] for r in fresh.execute("SELECT kind FROM scanner_events")]
        finally:
            fresh.close()
        self.assertEqual(state["failures"], 0)
        self.assertEqual(kinds.count("started"), 1)
        self.assertEqual(kinds.count("news"), 0)
        page = (out/"index.html").read_text(encoding="utf-8")
        self.assertIn("GitHub Actions run about every 15 min", page)
        self.assertIn('content="300"', page)

    def test_scanner_portfolio_may_hold_discoverable_pair(self):
        c = copy.deepcopy(self.c)
        c["phase4"]["portfolio"]["positions"] = {"SOL-USD":{"quantity":2,"average_cost_usd":150}}
        path = Path(self.temp.name)/"c.json"
        path.write_text(json.dumps(c), encoding="utf-8")
        load_config(path, discoverable=True)
        with self.assertRaises(ValueError):
            load_config(path)
        self.c = load_config(ROOT/"config.json")
        self.c["scanner"] = scanner_settings(self.c)
        self.c["phase4"]["portfolio"]["positions"] = {"SOL-USD":{"quantity":2,"average_cost_usd":150}}
        report, _ = self.scan()
        sol = self.actions(report)["SOL-USD"]
        self.assertEqual(sol["current_quantity"], 2)
        self.assertIn(sol["action"], ("BUY","HOLD"))


class InfrastructureTests(unittest.TestCase):
    def test_throttle_spaces_requests(self):
        waits, now = [], [0.0]
        t = throttled(lambda u, c: (200, b"", {}), 0.2, sleep=lambda s: (waits.append(s), now.__setitem__(0, now[0]+s)),
                      clock=lambda: now[0])
        t("a", {}); t("b", {})
        self.assertAlmostEqual(waits[0], 0.2)

    def test_server_is_local_only(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "index.html").write_text("ok", encoding="utf-8")
            server = serve(d, 0)
            try:
                host, port = server.server_address[:2]
                self.assertEqual(host, "127.0.0.1")
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=5) as r:
                    self.assertEqual(r.read(), b"ok")
                    self.assertEqual(r.headers["Cache-Control"], "no-store")
            finally:
                server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
