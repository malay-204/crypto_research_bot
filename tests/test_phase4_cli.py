"""CLI provenance and historical-report regressions, without network access."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from crypto_bot.core import connect,load_config,iso,utcnow
from crypto_bot.pipeline import collect
from crypto_bot.__main__ import demo_transport

ROOT=Path(__file__).resolve().parents[1]
NOW=1800000000.0

class Phase4CLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.path=self.base/"live.sqlite3"
        self.config=load_config(ROOT/"config.json")
        self.config["phase4"]["candles"]["lookback_candles"]=80
        self.db=connect(self.path,"live") # Isolated fixture database, never the real live file.
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def run_cli(self,*extra):
        return subprocess.run([sys.executable,"-m","crypto_bot","report","--db",str(self.path),
            "--out",str(self.base/"out"),*extra],cwd=ROOT,capture_output=True,text=True,
            encoding="utf-8",env=dict(os.environ,PYTHONUTF8="1",PYTHONDONTWRITEBYTECODE="1"))
    def test_report_uses_recorded_config_and_portfolio(self):
        self.config["phase4"]["portfolio"]["cash_usd"]=3210
        self.config["phase4"]["portfolio"]["reference_equity_usd"]=3210
        collect(self.db,self.config,demo_transport(NOW),lambda:NOW,with_candles=True)
        result=self.run_cli("--as-of",iso(NOW))
        self.assertEqual(result.returncode,0,result.stderr)
        r=json.loads(Path(json.loads(result.stdout)["json"]).read_text(encoding="utf-8"))
        self.assertEqual(r["phase4"]["input_portfolio"]["cash_usd"],3210)
        self.assertTrue(all(row["observed"]<=NOW for v in r["phase4"]["candle_data"] for row in v["rows"]))
    def test_historical_v1_config_does_not_invent_phase4(self):
        old=copy.deepcopy(self.config);old.pop("phase4")
        collect(self.db,old,demo_transport(NOW),lambda:NOW)
        collect(self.db,self.config,demo_transport(NOW+10),lambda:NOW+10,with_candles=True)
        result=self.run_cli("--as-of",iso(NOW))
        self.assertEqual(result.returncode,0,result.stderr)
        r=json.loads(Path(json.loads(result.stdout)["json"]).read_text(encoding="utf-8"))
        self.assertNotIn("phase4",r)
        self.assertEqual(r["action"],"RESEARCH ONLY — NO TRADING DECISION")
    def test_backfilled_candles_not_visible_before_observation(self):
        collect(self.db,self.config,demo_transport(NOW),lambda:NOW)
        collect(self.db,self.config,demo_transport(NOW+10),lambda:NOW+10,with_candles=True)
        result=self.run_cli("--as-of",iso(NOW))
        self.assertEqual(result.returncode,2,result.stderr)
        r=json.loads(Path(json.loads(result.stdout)["json"]).read_text(encoding="utf-8"))
        self.assertTrue(all(a["action"]=="WAIT" for a in r["phase4"]["assessments"]))
        self.assertTrue(all(v["rows"]==[] for v in r["phase4"]["candle_data"]))
    def test_report_rejects_unrecorded_portfolio_override(self):
        example=ROOT/"paper_portfolio.example.json"
        result=self.run_cli("--portfolio",str(example),"--as-of",iso(NOW))
        self.assertEqual(result.returncode,1)
        self.assertIn("only valid for collect/demo",result.stderr)
    def test_current_offline_report_keeps_recorded_portfolio_and_flags_staleness(self):
        self.config["phase4"]["portfolio"]["cash_usd"]=3210
        self.config["phase4"]["portfolio"]["reference_equity_usd"]=3210
        stale=utcnow()-10800
        collect(self.db,self.config,demo_transport(stale),lambda:stale,with_candles=True)
        result=self.run_cli()
        self.assertEqual(result.returncode,2,result.stderr)
        r=json.loads(Path(json.loads(result.stdout)["json"]).read_text(encoding="utf-8"))
        self.assertEqual(r["phase4"]["input_portfolio"]["cash_usd"],3210)
        self.assertEqual(r["phase4"]["status"],"inputs_blocked")

if __name__=="__main__":unittest.main()
