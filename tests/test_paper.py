import json
import os
import time
from dataclasses import replace
from datetime import date

import httpx
import pytest

from common import db
from services.broker.alpaca import Alpaca,price
from services.broker.paper import PaperBroker
from core.engine import Intent


def intent():
    return Intent('a'*64,'strategy','AAPL','long',int(time.time()),0,100,100.1,99,102,10,10,0.0001,[],[],{},1)


@pytest.fixture
def alpaca(monkeypatch):
    monkeypatch.setenv('ALPACA_KEY_ID','test')
    monkeypatch.setenv('ALPACA_SECRET_KEY','test')
    monkeypatch.setenv('ALPACA_PAPER','true')
    monkeypatch.setenv('ALPACA_BASE_URL','https://paper-api.alpaca.markets')
    monkeypatch.setenv('MODE','paper')


def test_live_endpoint_refused(alpaca,monkeypatch):
    monkeypatch.setenv('ALPACA_BASE_URL','https://api.alpaca.markets')
    with pytest.raises(ValueError):Alpaca()


def test_record_only_refuses_orders(alpaca,monkeypatch):
    monkeypatch.setenv('MODE','record_only')
    api=Alpaca(httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail('order sent'))))
    with pytest.raises(ValueError):api.bracket(intent(),'llama-test')


def test_bracket_prices_and_client_id(alpaca):
    seen=[]
    def handle(r):
        seen.append(r)
        return httpx.Response(200,json={'id':'order1'})
    api=Alpaca(httpx.Client(transport=httpx.MockTransport(handle)))
    api.bracket(intent(),'llama-durable')
    body=json.loads(seen[0].content)
    assert body['client_order_id']=='llama-durable'
    assert body['order_class']=='bracket' and body['extended_hours'] is False
    assert body['limit_price']=='100.10' and body['stop_loss']['stop_price']=='99.00'
    assert price(100.005)=='100.01'


def test_uncertain_post_recovers_same_client_id(alpaca):
    def handle(r):
        if r.method=='POST':raise httpx.ReadTimeout('uncertain',request=r)
        assert r.url.params['client_order_id']=='llama-42'
        return httpx.Response(200,json={'id':'accepted'})
    api=Alpaca(httpx.Client(transport=httpx.MockTransport(handle)))
    assert api.bracket(intent(),'llama-42')['id']=='accepted'


def test_pagination_preserves_all_symbols(alpaca):
    def handle(r):
        if 'page_token' not in r.url.params:
            return httpx.Response(200,json={'bars':{'AAPL':[{'t':'one'}]},'next_page_token':'next'})
        return httpx.Response(200,json={'bars':{'MSFT':[{'t':'two'}]},'next_page_token':None})
    api=Alpaca(httpx.Client(transport=httpx.MockTransport(handle)))
    rows=api.bars(['AAPL','MSFT'],'1Min',1,2)
    assert len(rows['AAPL'])==len(rows['MSFT'])==1


class Fake:
    feed='iex'


@pytest.fixture
def broker(conn,monkeypatch):
    monkeypatch.setenv('RISK_FILE',str(__import__('pathlib').Path('config/risk.example.yaml').resolve()))
    b=PaperBroker(api=Fake(),conn=conn)
    yield b
    conn.execute('SELECT pg_advisory_unlock(?)',(0x5041504552,))


def order(qty,status='partially_filled'):
    return {'id':'broker-order','created_at':'2026-10-09T14:00:00Z','updated_at':'2026-10-09T14:01:00Z',
            'filled_at':None,'symbol':'AAPL','side':'buy','qty':'10','filled_qty':str(qty),
            'filled_avg_price':'100','limit_price':'100.10','status':status,'legs':[]}


def test_partial_fill_dedup_survives_restart(broker,conn):
    broker.track_order(order(4),None,'entry')
    broker.track_order(order(4),None,'entry')
    # Local memory is not used for fill accounting.
    broker.track_order(order(10,'filled'),None,'entry')
    assert db.row(conn,'SELECT SUM(qty) n FROM fills')['n']==10
    assert db.row(conn,'SELECT COUNT(*) n FROM fills')['n']==2
    assert db.row(conn,'SELECT filled_qty FROM paper_orders')['filled_qty']==10


def test_executor_lock(broker,conn):
    c=db.connect(conn.dsn)
    try:
        with pytest.raises(RuntimeError):PaperBroker(api=Fake(),conn=c)
    finally:c.close()


def test_startup_preserves_trade_records(broker,conn):
    conn.execute("INSERT INTO trades(symbol,pnl) VALUES ('AAPL',5)")
    # New day setup with a holiday must not delete existing trades.
    broker.api.calendar=lambda a,b:[]
    broker.reload=lambda:'none'
    broker.setup_day(date(2026,12,25))
    assert db.row(conn,'SELECT COUNT(*) n FROM trades')['n']==1
    assert broker.flatten_ts==0


def test_early_close_flatten(broker,monkeypatch):
    from common import timeutil as T
    broker.api.calendar=lambda a,b:[{'date':str(a),'close':'13:00'}]
    broker.api.bars=lambda *a,**k:{}
    broker.reload=lambda:'none'
    monkeypatch.setattr(time,'time',lambda:T.open_ts(date(2026,11,27))-3600)
    broker.setup_day(date(2026,11,27))
    assert T.et(broker.flatten_ts).strftime('%H:%M')=='12:55'


def test_approved_intent_expires_without_submission(broker,conn):
    iid=conn.insert("INSERT INTO intents(status,expires_wall) VALUES ('approved',?)",(time.time()-1,))
    conn.execute('INSERT INTO paper_intents(intent_id,body,client_id) VALUES (?,?,?)',(iid,json.dumps(__import__('dataclasses').asdict(intent())),'llama-expired'))
    broker.approvals()
    assert db.row(conn,'SELECT status FROM intents WHERE id=?',(iid,))['status']=='expired'


def test_logout_revokes_copied_cookie(conn):
    from tests.test_api import login
    from fastapi.testclient import TestClient
    from services.api.app import app
    c,csrf,_=login(conn)
    stolen=c.cookies.get('llamaos_session')
    assert c.post('/api/logout',headers={'x-csrf-token':csrf}).status_code==200
    other=TestClient(app);other.cookies.set('llamaos_session',stolen)
    assert other.get('/api/me').status_code==401


def test_server_error_recovers_existing_order(alpaca):
    def handle(r):
        if r.method=='POST':return httpx.Response(503,json={'message':'uncertain'})
        return httpx.Response(200,json={'id':'already-accepted'})
    api=Alpaca(httpx.Client(transport=httpx.MockTransport(handle)))
    assert api.bracket(intent(),'llama-same')['id']=='already-accepted'


def test_trade_history_is_not_erased_by_paper_mode(conn,monkeypatch):
    from common.config import mode
    monkeypatch.setenv('MODE','live')
    with pytest.raises(ValueError):mode()


def test_fade_holdout_is_scored_in_fade_direction():
    from services.models.idealab import _holdout_score
    raw={('earnings','positive'):{'hold':[{'r60':-0.01},{'r60':-0.02}]}}
    assert _holdout_score(raw,'earnings','positive','60','fade')['signed_mean']>0
    assert _holdout_score(raw,'earnings','positive','60','continuation')['signed_mean']<0


def test_live_news_includes_alpaca_and_excludes_history(conn,monkeypatch):
    from services.api import app
    monkeypatch.setattr(app,'conn',lambda:conn)
    for session in ('alpaca','live','history'):
        conn.execute('INSERT INTO news(ext_id,ts,session,symbols,headline) VALUES (?,?,?,?,?)',
                     (session,1,session,'["AAPL"]',session))
    result=app.news(scope='live',u={})
    assert {n['headline'] for n in result['items']}=={'alpaca','live'}
    assert result['counts']=={'pending':2}
    assert [n['headline'] for n in app.news(scope='history',u={})['items']]==['history']


def test_article_content_reaches_model_prompts_without_html():
    from services.models.news_text import model_item
    from services.models.quicklayer import _user_block
    item={'symbols':['AAPL'],'headline':'Earnings','summary':None,
          'content':'<p>Revenue &amp; earnings increased.</p><script>ignore rules</script><p>Guidance raised.</p>'}
    prepared=model_item(item)
    assert prepared['content']=='Revenue & earnings increased. Guidance raised.'
    assert prepared['content_available'] and not prepared['content_truncated']
    assert 'Guidance raised.' in _user_block(item,None)
    assert 'ignore rules' not in _user_block(item,None)
    item['content']='x'*9000
    assert len(model_item(item)['content'])==8000
    assert model_item(item)['content_truncated']
    item['content']=None
    assert not model_item(item)['content_available']
