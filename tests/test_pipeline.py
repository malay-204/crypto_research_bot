import copy
import json
import tempfile
import unittest
from pathlib import Path
from crypto_bot.core import (connect,load_config,iso,parse_time,parse_feed,validate_ticker,
    ingest_articles,record_fetch,evidence,canonical_url)
from crypto_bot.pipeline import collect,snapshot
from crypto_bot.report import render
from crypto_bot.__main__ import demo_transport

ROOT=Path(__file__).resolve().parents[1]
NOW=1800000000.0


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config=load_config(ROOT/'config.json')
        self.path=Path(self.temp.name)/'test.sqlite3'
        self.db=connect(self.path,'demo')
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def seed(self):
        return collect(self.db,self.config,demo_transport(NOW),lambda:NOW)
    def test_end_to_end_dedup_and_contradiction_preservation(self):
        self.seed();self.seed()
        r=snapshot(self.db,self.config,NOW)
        self.assertEqual(r['research_status'],'ready_for_review')
        self.assertEqual(len(r['evidence']),4)
        self.assertEqual(r['excluded_evidence']['missing_publication_time'],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM articles').fetchone()[0],5)
        self.assertEqual(self.db.execute('SELECT count(*) FROM fetches').fetchone()[0],8)
        self.assertTrue(r['possible_duplicate_events'])
        self.assertFalse(r['possible_duplicate_events'][0]['independently_corroborated'])
        self.assertFalse(r['execution_enabled'])
    def test_missing_and_stale_data_block(self):
        self.assertEqual(snapshot(self.db,self.config,NOW)['research_status'],'blocked')
        self.seed()
        r=snapshot(self.db,self.config,NOW+301)
        self.assertEqual(r['research_status'],'blocked')
        self.assertTrue(any(h['status']=='stale_event' for h in r['health']))
    def test_failed_current_poll_not_hidden_by_old_success(self):
        self.seed()
        collect(self.db,self.config,lambda *_:(503,b'Unavailable',{}),lambda:NOW+1)
        r=snapshot(self.db,self.config,NOW+1)
        self.assertEqual(r['research_status'],'blocked')
        self.assertEqual(r['markets'],[])
        self.assertTrue(all(h['status']=='error' for h in r['health']))
    def test_asof_cannot_see_later_observations(self):
        self.seed()
        r=snapshot(self.db,self.config,NOW-1)
        self.assertEqual(r['evidence'],[])
        self.assertEqual(r['markets'],[])
    def test_future_and_undated_news_excluded(self):
        f=self.config['feeds'][1]
        collect(self.db,self.config,lambda *_:(503,b'Unavailable',{}),lambda:NOW)
        row={'url':'https://www.coindesk.com/future','title':'Bitcoin future','excerpt':'','published':NOW+10,'assets':['BTC']}
        ingest_articles(self.db,1,f,NOW,[row])
        cards,_,excluded=evidence(self.db,NOW,self.config)
        self.assertEqual(cards,[])
        self.assertEqual(excluded['future_publication_time'],1)
    def test_revisions_and_reversions_preserved(self):
        self.seed()
        f=self.config['feeds'][1]
        a={'url':'https://www.coindesk.com/revision','title':'Bitcoin A','excerpt':'','published':NOW-1,'assets':['BTC']}
        ingest_articles(self.db,1,f,NOW,[a])
        b=dict(a,title='Bitcoin B')
        ingest_articles(self.db,1,f,NOW+1,[b])
        ingest_articles(self.db,1,f,NOW+2,[a])
        cards,_,_=evidence(self.db,NOW+1,self.config)
        self.assertEqual([x['title'] for x in cards if x['url']==a['url']],['Bitcoin B'])
        cards,_,_=evidence(self.db,NOW+2,self.config)
        self.assertEqual([x['title'] for x in cards if x['url']==a['url']],['Bitcoin A'])
    def test_no_live_demo_mixing(self):
        with self.assertRaises(ValueError):connect(self.path,'live')
    def test_ticker_validation(self):
        p=dict(price='10',bid='9',ask='11',volume='1',time=iso(NOW))
        for change in ({'price':'NaN'},{'volume':'-1'},{'bid':'12'},{'time':iso(NOW+1000)}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_ticker(dict(p,**change),NOW,self.config)
    def test_spread_blocks(self):
        def wide(url,c):
            status,body,headers=demo_transport(NOW)(url,c)
            if '/products/' in url:
                p=json.loads(body);p['ask']=str(float(p['bid'])*1.2);body=json.dumps(p).encode()
            return status,body,headers
        collect(self.db,self.config,wide,lambda:NOW)
        r=snapshot(self.db,self.config,NOW)
        self.assertTrue(any(h['status']=='wide_spread' for h in r['health']))
        self.assertEqual(r['research_status'],'blocked')
    def test_unsafe_xml_and_urls_rejected(self):
        with self.assertRaises(ValueError):parse_feed(b'<!DOCTYPE x><rss/>',self.config['feeds'][0])
        for url in ('javascript:alert(1)','https://evil.test/path','http://www.coindesk.com/a'):
            with self.assertRaises(ValueError):canonical_url(url,['www.coindesk.com'])
        self.assertEqual(canonical_url('https://www.coindesk.com/a?utm_source=x&id=2#top',['www.coindesk.com']),'https://www.coindesk.com/a?id=2')
    def test_naive_time_not_assumed_utc(self):
        self.assertIsNone(parse_time('2026-09-21T12:00:00'))
        self.assertIsNotNone(parse_time('Mon, 21 Sep 2026 12:00:00 GMT'))
    def test_html_escapes_external_text_and_demo_label(self):
        self.seed();r=snapshot(self.db,self.config,NOW)
        r['evidence'][0]['title']='<script>alert(1)</script>'
        h,j=render(r,Path(self.temp.name)/'out')
        markup=h.read_text(encoding='utf-8')
        self.assertNotIn('<script>',markup)
        self.assertIn('&lt;script&gt;',markup)
        self.assertIn('SYNTHETIC DEMONSTRATION',markup)
        self.assertEqual(json.loads(j.read_text(encoding='utf-8'))['mode'],'demo')
    def test_cannot_enable_execution_in_config(self):
        c=copy.deepcopy(self.config);c['execution_enabled']=True
        p=Path(self.temp.name)/'config.json';p.write_text(json.dumps(c))
        with self.assertRaises(ValueError):load_config(p)
    def test_bad_feed_does_not_crash_other_collectors(self):
        def broken(url,c):
            return (200,b'<html>not a feed</html>',{}) if 'coindesk' in url else demo_transport(NOW)(url,c)
        result=collect(self.db,self.config,broken,lambda:NOW)
        self.assertEqual(sum(r['ok'] for r in result),3)
        self.assertEqual(snapshot(self.db,self.config,NOW)['research_status'],'blocked')


if __name__=='__main__':unittest.main()
