import asyncio
import copy
import json
import time
import pytest
from astrbot_plugin_kouhu_judge.at_downloader import AtCoderDownloader, PacedFetcher
from astrbot_plugin_kouhu_judge.offline_downloader import OfflineDownloader
from astrbot_plugin_kouhu_judge.providers.common import FetchError
from astrbot_plugin_kouhu_judge.config import Config

class Provider:
    def __init__(self, problem, count=4):
        self.p=problem;self.calls=[];self.meta_calls=0;self.error=None;self.meta_error=False
        self.rows=[{'id':f'abc999_{chr(97+i)}','contest_id':'abc999','rating':1600,'difficulty':1200} for i in range(count)]
    async def metadata(self):
        self.meta_calls+=1
        if self.meta_error:raise FetchError('offline')
        return self.rows
    async def fetch_problem(self,m):
        self.calls.append(m['id'])
        if self.error:raise self.error
        p=copy.deepcopy(self.p);p.problem_id='at:'+m['id'];p.platform='at';p.normalized_rating=m['rating']
        return p

async def test_resume_cache_dedup_no_model(setup_game,tmp_path,problem):
    g,db,llm,*_=setup_game;g.c.at_download_batch_size=1;p=Provider(problem)
    w=AtCoderDownloader(db,g.c,p,tmp_path)
    assert await w.harvest_page()==(1,0)
    first=p.calls[:];state=await db.state(w.CURSOR)
    p2=Provider(problem);p2.meta_error=True
    w2=AtCoderDownloader(db,g.c,p2,tmp_path)
    assert await w2.harvest_page()==(1,0)
    assert not p2.meta_calls and p2.calls!=first
    assert (await db.state(w.CURSOR))['scanned']==state['scanned']+1
    for _ in range(6):await w2.harvest_page()
    assert len(p.calls)+len(p2.calls)==4 and not llm.calls

async def test_activity_and_range_skip(setup_game,tmp_path,problem):
    g,db,*_=setup_game;p=Provider(problem);p.rows[0]['rating']=999
    g.c.download_low=1400;g.c.download_high=1800
    w=AtCoderDownloader(db,g.c,p,tmp_path,idle=lambda:False)
    assert await w.harvest_page()==(0,0) and not p.meta_calls
    w.idle=lambda:True
    assert await w.harvest_page()==(3,0)
    assert 'abc999_a' not in p.calls

async def test_unverified_and_failure_backoff(setup_game,tmp_path,problem):
    g,db,*_=setup_game;g.c.at_download_batch_size=1;p=Provider(problem,1);p.error=FetchError('no official editorial')
    w=AtCoderDownloader(db,g.c,p,tmp_path)
    assert await w.harvest_page()==(0,1)
    assert await w.harvest_page()==(0,0) and len(p.calls)==1
    assert not await db.problem('at:abc999_a')

async def test_rate_limit_stops_requests_and_persists(setup_game,tmp_path,problem):
    g,db,*_=setup_game;p=Provider(problem);p.error=FetchError('rate',429,180)
    w=AtCoderDownloader(db,g.c,p,tmp_path)
    assert await w.harvest_page()==(0,1) and len(p.calls)==1
    assert await db.state('at_download_backoff_until')>time.time()+100
    assert await AtCoderDownloader(db,g.c,p,tmp_path).harvest_page()==(0,0)
    assert len(p.calls)==1

async def test_cancel_does_not_advance_cursor(setup_game,tmp_path,problem):
    g,db,*_=setup_game;p=Provider(problem);p.error=asyncio.CancelledError()
    w=AtCoderDownloader(db,g.c,p,tmp_path)
    with pytest.raises(asyncio.CancelledError):await w.harvest_page()
    assert not await db.state(w.CURSOR)
    assert not await db.problem('at:abc999_d')

async def test_expired_metadata_uses_old_snapshot(setup_game,tmp_path,problem):
    g,db,*_=setup_game;p=Provider(problem);w=AtCoderDownloader(db,g.c,p,tmp_path)
    rows=await w.metadata();p.meta_error=True;g.c.metadata_ttl_hours=-1
    assert await w.metadata()==rows and p.meta_calls==2

async def test_empty_cache_is_refreshed(setup_game,tmp_path,problem):
    g,db,*_=setup_game;p=Provider(problem);w=AtCoderDownloader(db,g.c,p,tmp_path)
    w.path.write_text(json.dumps({'schema':1,'updated_at':time.time(),'problems':[]}))
    assert await w.metadata() and p.meta_calls==1

@pytest.mark.parametrize('cf_backoff',[False,True])
async def test_cf_failure_or_cooldown_cannot_starve_at(setup_game,monkeypatch,cf_backoff):
    g,db,*_=setup_game;g.c.background_download=True;g.c.background_at_download=True
    w=OfflineDownloader(db,g.c,None,None);w.idle=lambda:True
    called=[]
    async def cf():called.append('cf');raise FetchError('offline')
    class At:
        async def harvest_page(self):called.append('at')
    w.harvest_page=cf;w.at_downloader=At()
    if cf_backoff:await db.state('download_backoff_until',time.time()+500)
    ticks=0
    async def sleep(_):
        nonlocal ticks
        ticks+=1
        if ticks==3:raise asyncio.CancelledError()
    monkeypatch.setattr('astrbot_plugin_kouhu_judge.offline_downloader.asyncio.sleep',sleep)
    with pytest.raises(asyncio.CancelledError):await w.run()
    assert called==(['at'] if cf_backoff else ['cf','at'])

async def test_pacing_serializes_calls():
    calls=[]
    class F:
        async def get(self,x):calls.append(time.monotonic());return x
    f=PacedFetcher(F(),interval=0.02)
    assert await asyncio.gather(f.get(1),f.get(2))==[1,2]
    assert calls[1]-calls[0]>=0.015

def test_at_config_defaults_and_bounds():
    assert Config().background_at_download
    assert Config.from_mapping({'at_download_batch_size':100}).at_download_batch_size==10
