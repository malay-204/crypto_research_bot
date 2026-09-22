import copy
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from crypto_bot.core import connect,load_config,iso,record_fetch
from crypto_bot.pipeline import collect,snapshot
from crypto_bot.__main__ import demo_transport
from crypto_bot.candles import parse_candles,ingest_candles,candle_view
from crypto_bot.settings import validate_phase4
from crypto_bot.report import render

ROOT=Path(__file__).resolve().parents[1]
NOW=1800000000.0
G=3600
END=int(NOW//G)*G

class Phase4Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"demo.sqlite3"
        self.db=connect(self.path,"demo")
        self.c=load_config(ROOT/"config.json")
        self.c["phase4"]["candles"]["lookback_candles"]=100
        self.c["phase4"]["candles"]["page_size"]=40
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def seed(self, transport=None, now=NOW):
        return collect(self.db,self.c,transport or demo_transport(now),lambda:now,with_candles=True)
    def report(self, now=NOW):
        return snapshot(self.db,self.c,now)
    def decisions(self, now=NOW):
        return {a["pair"]:a for a in self.report(now)["phase4"]["assessments"]}
    def portfolio(self, cash, btc=0, eth=0, btc_cost=60000, eth_cost=2500, reference=None):
        self.c["phase4"]["portfolio"]={"kind":"hypothetical_scenario","cash_usd":cash,
            "reference_equity_usd":reference or cash+btc*60000+eth*2500,
            "positions":{a:{"quantity":q,"average_cost_usd":cost} for a,q,cost in
                         [("BTC-USD",btc,btc_cost),("ETH-USD",eth,eth_cost)] if q}}
    def both_bullish(self,url,c):
        if "/candles?" in url and "ETH-USD" in url:
            status,body,h=demo_transport(NOW)(url.replace("ETH-USD","BTC-USD"),c)
            rows=json.loads(body)
            for r in rows:
                r[1:5]=[v/24 for v in r[1:5]]
            return status,json.dumps(rows).encode(),h
        return demo_transport(NOW)(url,c)
    def test_pagination_closed_candles_and_dedup(self):
        requested=[]
        def transport(url,c):
            if "/candles?" in url:
                q=parse_qs(urlsplit(url).query);requested.append(q)
            return demo_transport(NOW)(url,c)
        self.seed(transport);self.seed(transport)
        self.assertEqual(len(requested),12)
        self.assertEqual(self.db.execute("SELECT count(*) FROM candles").fetchone()[0],200)
        self.assertEqual(self.db.execute("SELECT count(*) FROM candle_batches").fetchone()[0],4)
        self.assertEqual(self.db.execute("SELECT count(*) FROM fetches WHERE source LIKE 'candles:%'").fetchone()[0],12)
        for v in self.report()["phase4"]["candle_data"]:
            self.assertEqual(v["status"],"ok")
            self.assertEqual(v["batch"]["missing_count"],0)
            self.assertTrue(all(r["start"]+G<=NOW for r in v["rows"]))
    def test_boundary_unordered_identical_duplicates_and_open_candle(self):
        rows=[[END-G,9,12,10,11,4],[END,9,12,10,11,4],
              [END-3*G,9,12,10,11,4],[END-G,9,12,10,11,4]]
        parsed,excluded=parse_candles(json.dumps(rows),G,END-2*G,END+G,END)
        self.assertEqual(len(parsed),1);self.assertEqual(excluded,2)
    def test_invalid_timestamps_ohlcv_and_conflicting_duplicates(self):
        valid=[END-G,9,12,10,11,4]
        variants=[[END-G+1,*valid[1:]],[END-G+0.5,*valid[1:]],[-G,*valid[1:]],
            [END-G,9,12,10,13,4],[END-G,9,12,10,11,-1],[END-G,9,12,10,float("nan"),4],
            [True,*valid[1:]],[END-G,0,12,10,11,4],["1800000000",*valid[1:]]]
        for row in variants:
            with self.subTest(row=row),self.assertRaises(ValueError):
                parse_candles(json.dumps([row]),G,END-2*G,END,END)
        with self.assertRaises(ValueError):
            parse_candles(json.dumps([valid,[END-G,9,12,10,11,5]]),G,END-2*G,END,END)
    def test_gap_not_forward_filled_or_hidden_by_old_rows(self):
        self.seed()
        def missing(url,c):
            s,b,h=demo_transport(NOW+1)(url,c)
            if "/candles?" in url: b=json.dumps(json.loads(b)[1:]).encode()
            return s,b,h
        self.seed(missing,NOW+1)
        self.assertTrue(all(a["action"]=="WAIT" for a in self.decisions(NOW+1).values()))
        self.assertTrue(all(v["status"]=="gaps" for v in self.report(NOW+1)["phase4"]["candle_data"]))
    def test_failed_page_blocks_despite_prior_success(self):
        self.seed()
        def failed(url,c):
            if "/candles?" in url and "BTC-USD" in url:
                return 503,b"unavailable",{}
            return demo_transport(NOW+1)(url,c)
        self.seed(failed,NOW+1)
        self.assertEqual(self.decisions(NOW+1)["BTC-USD"]["action"],"WAIT")
        self.assertEqual(candle_view(self.db,self.c,"BTC-USD",NOW+1)["status"],"error")
        self.assertTrue(candle_view(self.db,self.c,"BTC-USD",NOW+1)["batch"]["error"])
    def test_empty_history_blocks(self):
        def empty(url,c):
            return (200,b"[]",{}) if "/candles?" in url else demo_transport(NOW)(url,c)
        self.seed(empty)
        self.assertTrue(all(a["action"]=="WAIT" for a in self.decisions().values()))
    def test_revision_reversion_and_observation_cutoff(self):
        self.seed()
        old=candle_view(self.db,self.c,"BTC-USD",NOW)["rows"][-1]
        fid=self.db.execute("SELECT id FROM fetches WHERE source LIKE 'candles:%' LIMIT 1").fetchone()[0]
        values=[old[k] for k in ("start","low","high","open","close","volume")]
        revised=list(values);revised[-1]+=1
        ingest_candles(self.db,fid,"BTC-USD",G,NOW+10,[revised])
        ingest_candles(self.db,fid,"BTC-USD",G,NOW+20,[values])
        self.assertEqual(candle_view(self.db,self.c,"BTC-USD",NOW+9)["rows"][-1]["volume"],values[-1])
        self.assertEqual(candle_view(self.db,self.c,"BTC-USD",NOW+10)["rows"][-1]["volume"],revised[-1])
        self.assertEqual(candle_view(self.db,self.c,"BTC-USD",NOW+20)["rows"][-1]["volume"],values[-1])
        self.assertEqual(candle_view(self.db,self.c,"BTC-USD",NOW-1)["status"],"missing")
    def test_in_progress_batch_blocks_older_success(self):
        self.seed()
        self.db.execute("UPDATE candle_batches SET finished=NULL WHERE pair='BTC-USD'")
        self.assertEqual(candle_view(self.db,self.c,"BTC-USD",NOW)["status"],"in_progress")
        self.assertEqual(self.decisions()["BTC-USD"]["action"],"WAIT")
    def test_candle_staleness_independent_of_fresh_ticker(self):
        self.seed()
        later=NOW+3*G
        collect(self.db,self.c,demo_transport(later),lambda:later)
        self.assertEqual(self.decisions(later)["BTC-USD"]["action"],"WAIT")
        self.assertEqual(candle_view(self.db,self.c,"BTC-USD",later)["status"],"stale")
    def test_buy_and_wait_and_no_database_or_portfolio_mutation(self):
        self.seed();before=self.db.total_changes
        d=self.decisions()
        self.assertEqual(d["BTC-USD"]["action"],"BUY")
        self.assertEqual(d["ETH-USD"]["action"],"WAIT")
        self.assertEqual(self.db.total_changes,before)
        self.assertEqual(self.c["phase4"]["portfolio"]["cash_usd"],10000)
        t=d["BTC-USD"]["proposed_trade"]
        self.assertAlmostEqual(t["total_cost_usd"],t["mid_notional_usd"]*.0075)
        self.assertAlmostEqual(-t["cash_change_usd"],t["mid_notional_usd"]+t["total_cost_usd"])
        self.assertFalse(self.report()["execution_enabled"])
    def test_portfolio_aware_bearish_exit(self):
        self.portfolio(9000,eth=.4)
        self.seed()
        a=self.decisions()["ETH-USD"]
        self.assertEqual(a["action"],"REDUCE/EXIT")
        self.assertEqual(a["proposed_trade"]["quantity"],.4)
        self.assertGreater(a["proposed_trade"]["fee_usd"],0)
        self.assertLess(a["proposed_trade"]["cash_change_usd"],1000)
    def test_hold_neutral_existing_position(self):
        self.portfolio(9500,btc=500/60000)
        def neutral(url,c):
            s,b,h=demo_transport(NOW)(url,c)
            if "/candles?" in url:
                rows=json.loads(b)
                for r in rows:r[1:5]=[59999,60001,60000,60000]
                b=json.dumps(rows).encode()
            return s,b,h
        self.seed(neutral)
        self.assertEqual(self.decisions()["BTC-USD"]["action"],"HOLD")
    def test_high_costs_veto_entry(self):
        self.c["phase4"]["costs"]["fee_bps_per_side"]=1000
        self.seed()
        self.assertEqual(self.decisions()["BTC-USD"]["action"],"WAIT")
    def test_shared_cash_exposure_and_costs(self):
        risk=self.c["phase4"]["risk"]
        risk.update(max_position_pct=100,max_portfolio_exposure_pct=100,max_trade_pct=100)
        self.seed(self.both_bullish)
        r=self.report()["phase4"]
        buys=[a["proposed_trade"] for a in r["assessments"] if a["action"]=="BUY"]
        self.assertLessEqual(sum(-b["cash_change_usd"] for b in buys),10000.00000001)
        self.assertGreaterEqual(r["projected_if_all_proposals_filled"]["cash_usd"],-1e-8)
        self.assertEqual(self.decisions()["ETH-USD"]["action"],"WAIT")
    def test_joint_exposure_caps_after_entry_costs(self):
        risk=self.c["phase4"]["risk"]
        risk.update(max_position_pct=25,max_portfolio_exposure_pct=30,max_trade_pct=25)
        self.seed(self.both_bullish)
        r=self.report()["phase4"]["projected_if_all_proposals_filled"]
        self.assertLessEqual(r["exposure_pct"],30.000001)
        for pair,price in [("BTC-USD",60000),("ETH-USD",2500)]:
            self.assertLessEqual(100*r["quantities"][pair]*price/r["equity_usd"],25.000001)
    def test_position_loss_and_reference_loss_pause_entries(self):
        for kwargs in [dict(btc_cost=70000),dict(reference=12000)]:
            with self.subTest(kwargs=kwargs):
                self.portfolio(9000,btc=1000/60000,**kwargs)
                self.seed(self.both_bullish)
                r=self.report()["phase4"]
                self.assertTrue(r["portfolio"]["new_entries_paused"])
                self.assertEqual(self.decisions()["BTC-USD"]["action"],"REDUCE/EXIT")
                self.assertNotEqual(self.decisions()["ETH-USD"]["action"],"BUY")
    def test_exposure_reduction_accounts_for_costs_across_assets(self):
        self.portfolio(6000,btc=2000/60000,eth=2000/2500)
        self.seed(self.both_bullish)
        r=self.report()["phase4"]["projected_if_all_proposals_filled"]
        self.assertLessEqual(r["exposure_pct"],40.000001)
        for pair,price in [("BTC-USD",60000),("ETH-USD",2500)]:
            self.assertLessEqual(100*r["quantities"][pair]*price/r["equity_usd"],20.000001)
        self.assertTrue(all(a["action"]=="REDUCE/EXIT" for a in self.decisions().values()))
    def test_missing_held_mark_blocks_entire_portfolio(self):
        self.portfolio(9000,eth=.4)
        def bad(url,c):
            return (503,b"down",{}) if "/ETH-USD/ticker" in url else demo_transport(NOW)(url,c)
        self.seed(bad)
        self.assertTrue(all(a["action"]=="WAIT" for a in self.decisions().values()))
        self.assertIsNone(self.report()["phase4"]["portfolio"]["equity_usd"])
    def test_stale_future_portfolio_and_zero_equity_wait(self):
        self.seed()
        for date in [iso(NOW+1),iso(NOW-90000)]:
            self.c["phase4"]["portfolio"].update(kind="paper_snapshot",as_of=date)
            self.assertTrue(all(a["action"]=="WAIT" for a in self.decisions().values()))
        self.c["phase4"]["portfolio"].update(kind="hypothetical_scenario",cash_usd=0)
        self.assertTrue(all(a["action"]=="WAIT" for a in self.decisions().values()))
    def test_risk_exit_not_hidden_by_failed_news_or_candles(self):
        self.portfolio(9000,btc=1000/60000,btc_cost=70000)
        def bad(url,c):
            if "/candles?" in url or "coindesk" in url:return 503,b"down",{}
            return demo_transport(NOW)(url,c)
        self.seed(bad)
        self.assertEqual(self.decisions()["BTC-USD"]["action"],"REDUCE/EXIT")
        self.assertEqual(self.report()["phase4"]["status"],"inputs_blocked")
    def test_news_words_do_not_change_numeric_assessment(self):
        self.seed()
        before=self.decisions()
        self.db.execute("UPDATE articles SET title='Bitcoin Ethereum hack disaster moon profit guaranteed'")
        after=self.decisions()
        self.assertEqual([(a["action"],a["signal"]) for a in before.values()],
                         [(a["action"],a["signal"]) for a in after.values()])
    def test_invalid_simulation_inputs_rejected(self):
        for group,key,value in [
            ("risk","max_position_pct",101),("risk","max_trade_pct",True),
            ("costs","fee_bps_per_side",-1),("strategy","slow_window",24),
            ("candles","page_size",301),("candles","granularity_seconds",123),
            ("portfolio","cash_usd",float("nan"))]:
            c=copy.deepcopy(self.c);c["phase4"][group][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_phase4(c)
        c=copy.deepcopy(self.c)
        c["phase4"]["portfolio"]["positions"]={"SOL-USD":{"quantity":1,"average_cost_usd":10}}
        with self.assertRaises(ValueError):validate_phase4(c)
    def test_report_escape_provenance_and_collision_preservation(self):
        self.seed();r=self.report()
        r["phase4"]["assessments"][0]["reason"]="<script>bad()</script>"
        out=Path(self.temp.name)/"report"
        h,j=render(r,out);original=h.read_bytes()
        h2,j2=render(r,out)
        self.assertNotEqual(h,h2);self.assertEqual(h.read_bytes(),original)
        markup=h.read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;",markup);self.assertNotIn("<script>",markup)
        self.assertIn("PROVISIONAL SIMULATION",markup);self.assertIn("Opposing evidence",markup)
        self.assertTrue(json.loads(j.read_text(encoding="utf-8"))["phase4"]["candle_data"][0]["rows"][0]["fetch_id"])
    def test_v1_migration_backup_preserves_data_and_mode(self):
        path=Path(self.temp.name)/"old.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL); INSERT INTO meta VALUES ('mode','live'); INSERT INTO meta VALUES ('schema_version','1'); CREATE TABLE keep_me(value TEXT); INSERT INTO keep_me VALUES ('original');")
        migrated=connect(path,"live")
        self.assertEqual(migrated.execute("SELECT value FROM keep_me").fetchone()[0],"original")
        self.assertEqual(migrated.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],"3")
        migrated.close()
        backups=list((path.parent/"backups").glob("old.sqlite3.v1.*.bak"))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(backup.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],"1")
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        with self.assertRaises(ValueError):connect(path,"demo")
        migrated=connect(path,"live");migrated.close()
        self.assertEqual(len(list((path.parent/"backups").glob("old.sqlite3.v1.*.bak"))),1)

if __name__=="__main__":unittest.main()
