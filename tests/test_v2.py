import asyncio,copy,json,time
from pathlib import Path
from dataclasses import asdict
import httpx
import pytest
from astrbot_plugin_kouhu_judge.config import Config
from astrbot_plugin_kouhu_judge.models import Identity,Judgment,GameError
from astrbot_plugin_kouhu_judge.commands import parse
from astrbot_plugin_kouhu_judge.dedicated_api import DedicatedJudgeAPI
from astrbot_plugin_kouhu_judge.llm import LLMClient,LLMFailure
from astrbot_plugin_kouhu_judge.judge_service import JudgeService
from astrbot_plugin_kouhu_judge.translation_service import TranslationService
from astrbot_plugin_kouhu_judge.providers.cf_archive import CFArchiveProvider,REVISION
from astrbot_plugin_kouhu_judge.providers.codeforces import CodeforcesProvider
from astrbot_plugin_kouhu_judge.providers.common import FetchError
from astrbot_plugin_kouhu_judge.offline_downloader import OfflineDownloader


def test_v2_defaults():
    c=Config();assert c.bare_commands and c.judge_only and c.translate and not c.judge_two_stage
    assert (c.default_platform,c.default_low,c.default_high)==('cf',1400,1800)
    assert c.api_model=='gpt-6.1-sol' and not c.api_key


async def test_fixed_default_and_explicit_range(setup_game,problem):
    g,db,m,a,b=setup_game;g.c.difficulty_mode='fixed'
    easy=copy.deepcopy(problem);easy.problem_id='cf:EASY';easy.normalized_rating=1600;await db.add_problem(easy)
    r=await g.execute(a,parse('/new'))
    assert 'EASY' in r.images[0]
    await g.execute(a,parse('/giveup'))
    r=await g.execute(a,parse('/new cf 2100-2400'))
    assert 'MOCK' in r.images[0]


async def test_admin_group_default_persistent_and_isolated(setup_game,problem):
    g,db,m,a,b=setup_game
    with pytest.raises(GameError):await g.execute(a,parse('/kh setdefault cf 2000-2400'))
    await g.execute(a,parse('/kh setdefault cf 2000-2400'),admin=True)
    assert await g.problems.defaults(a)==('cf',2000,2400)
    assert await g.problems.defaults(Identity('qq','A','456'))==('cf',1400,1800)
    assert await db.room_default(a.room_id)


@pytest.mark.parametrize('arg',['cf -1-1800','cf 2000-1400','bad 1400-1800'])
async def test_invalid_group_defaults(setup_game,arg):
    g,db,m,a,b=setup_game
    with pytest.raises(GameError):await g.execute(a,parse('/kh setdefault '+arg),admin=True)


async def test_group_defaults_webui(setup_game):
    g,db,m,a,b=setup_game;g.c.group_defaults=['bad config','qq:GroupMessage:123|cf|1700-1900']
    assert await g.problems.defaults(a)==('cf',1700,1900)


async def test_local_actions_cost_zero_llm(setup_game):
    g,db,m,a,b=setup_game;g.c.judge_only=True
    await g.execute(a,parse('/new'))
    for text in ['/cur','/kh ping','/kh help','/kh clear','/ranklist','/bankstatus']:
        await g.execute(a,parse(text,generic=True))
    await g.chat(a,'请翻译题面')
    await g.execute(a,parse('/giveup'))
    assert not m.calls


async def test_translation_works_despite_judge_only(config,tmp_path,problem):
    config.judge_only=True;config.translate=True;calls=[]
    async def main_model(system,prompt,*args):
        calls.append(1)
        return json.dumps({'fields':{k:('中文：'+v if v else '') for k,v in json.loads(prompt)['fields'].items()}})
    result=await TranslationService(LLMClient(main_model,config),config,tmp_path).translate(problem)
    assert result['_translation']=='zh' and len(calls)==1


async def test_single_request_and_minimal_payload(config,problem):
    config.judge_two_stage=False;calls=[]
    async def generate(system,prompt,*args):
        calls.append(json.loads(prompt));return json.dumps(asdict(Judgment('AC',0.99,True,True,True,feedback='ok')))
    result=await JudgeService(LLMClient(generate,config),config).judge(problem,'sum each a_i in O(n)',[{'content':'first idea','verdict':'WA','feedback':'secret feedback'}])
    assert result.verdict=='AC' and len(calls)==1
    assert set(calls[0]['problem'])=={'problem_id','statement','constraints','input_spec','output_spec','samples','time_limit','memory_limit'}
    assert calls[0]['history']==[{'content':'first idea'}]


async def test_dedicated_api_request_and_secret_redaction(config):
    config.api_key='test-secret-key';config.api_base_url='https://test.invalid/v1';seen=[]
    def handler(req):
        seen.append(req);return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"verdict":"UNCERTAIN"}'}}]})
    api=DedicatedJudgeAPI(config,httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        assert await api.generate('rules','{"submission":"x"}')=='{"verdict":"UNCERTAIN"}'
        body=json.loads(seen[0].content)
        assert str(seen[0].url)=='https://test.invalid/v1/chat/completions'
        assert body['model']=='gpt-6.1-sol' and body['max_tokens']==2048 and 'reasoning_effort' not in body
        assert 'test-secret-key' not in repr(config) and 'test-secret-key' not in seen[0].content.decode()
        assert seen[0].headers['Authorization']=='Bearer test-secret-key'
    finally:await api.close()


async def test_missing_dedicated_key_never_falls_back(config):
    def fail(req):raise AssertionError('no request without key')
    api=DedicatedJudgeAPI(config,httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    try:
        with pytest.raises(LLMFailure,match='专用 API'):await api.generate('','')
    finally:await api.close()


@pytest.mark.parametrize('status',[401,429,500])
async def test_api_error_does_not_expose_upstream_key(config,status):
    config.api_key='test-key-sensitive'
    api=DedicatedJudgeAPI(config,httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(status,text='test-key-sensitive bad response'))))
    try:
        with pytest.raises(LLMFailure) as exc:await api.generate('','')
        assert 'test-key-sensitive' not in str(exc.value)
    finally:await api.close()


def archive_item():
    return {'row_idx':7,'truncated_cells':[], 'row':{'id':'999999/A','title':'Mock problem','description':'description',
       'input_format':'read n','output_format':'print answer','examples':[{'input':'  1\n','output':'2\n'}],
       'editorial':'Author editorial text. '*10,'rating':1600,'note':'note','interaction_format':None}}


class ArchiveFetcher:
    def __init__(self):self.calls=[];self.error=False
    async def get(self,url,json_data=False):
        self.calls.append(url)
        if self.error:raise FetchError('offline')
        if '/api/datasets/' in url:return {'sha':REVISION}
        return {'num_rows_total':1,'rows':[archive_item()]}


async def test_archive_page_durable_cache_and_no_generated_code(tmp_path):
    f=ArchiveFetcher();a=CFArchiveProvider(f,tmp_path)
    first=await a.page(0,1);f.error=True
    assert await CFArchiveProvider(f,tmp_path).page(0,1)==first
    assert len(f.calls)==2 and 'generated_checker' not in first['rows'][0]['row']


def test_archive_provenance_and_official_rating(tmp_path):
    a=CFArchiveProvider(None,tmp_path);p=a.convert(archive_item(),{'999999A':{'name':'Mock problem','rating':1700,'tags':[]}})
    assert p.normalized_rating==1700 and p.samples[0].input=='  1\n'
    assert p.verification['revision']==REVISION
    assert p.verification['official_editorial_direct_url_verified'] is False
    assert p.editorial_url.startswith('https://huggingface.co/')


@pytest.mark.parametrize('change',[{'editorial':''},{'description':''},{'title':'not matching'},{'interaction_format':'interactive'}])
def test_bad_archive_never_enters_pool(tmp_path,change):
    a=CFArchiveProvider(None,tmp_path);item=archive_item();item['row'].update(change)
    with pytest.raises(ValueError):a.convert(item,{'999999A':{'name':'Mock problem','rating':1600,'tags':[]}})


async def test_api_metadata_outage_uses_cached_snapshot(config,tmp_path):
    class F:
        error=False
        async def get(self,*args):
            if self.error:raise FetchError('offline')
            return {'status':'OK','result':{'problems':[{'contestId':999999,'index':'A','name':'Mock problem','rating':1600}]}}
    f=F();cf=CodeforcesProvider(f,None,tmp_path,config);rows=await cf.metadata()
    f.error=True;config.metadata_ttl_hours=-1
    assert await cf.metadata()==rows


async def test_idle_worker_pauses_and_deduplicates(setup_game,tmp_path):
    g,db,m,a,b=setup_game
    class CF:
        async def metadata(self):return [{'contestId':999999,'index':'A','name':'Mock problem','rating':1600}]
    fetcher=ArchiveFetcher();archive=CFArchiveProvider(fetcher,tmp_path)
    worker=OfflineDownloader(db,g.c,CF(),archive)
    assert await worker.harvest_page(official=False)==(0,0) and not fetcher.calls
    assert await worker.harvest_page(force=True,official=False)==(1,0)
    first=await db.run(lambda c:c.execute("SELECT attempts FROM download_jobs WHERE problem_id='cf:999999A'").fetchone()[0])
    assert await worker.harvest_page(force=True,official=False)==(0,0)
    assert await db.run(lambda c:c.execute("SELECT attempts FROM download_jobs WHERE problem_id='cf:999999A'").fetchone()[0])==first
    assert not m.calls


async def test_cf_api_failure_does_not_affect_local_selection(setup_game,problem):
    g,db,m,a,b=setup_game;g.c.difficulty_mode='fixed'
    p=copy.deepcopy(problem);p.problem_id='cf:LOCAL';p.normalized_rating=1500;await db.add_problem(p)
    class Broken:
        async def metadata(self):raise FetchError('offline')
    g.problems.providers['cf']=Broken()
    reply=await g.execute(a,parse('/new'))
    assert 'LOCAL' in reply.images[0] and not m.calls


async def test_source_rate_limit_is_not_immediately_retried():
    from astrbot_plugin_kouhu_judge.providers.common import Fetcher
    seen=[]
    def handler(req):seen.append(1);return httpx.Response(429,headers={'Retry-After':'120'})
    f=Fetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(FetchError) as e:await f.get('https://test.invalid')
        assert e.value.status_code==429 and e.value.retry_after==120 and len(seen)==1
    finally:await f.close()


async def test_offline_worker_respects_persistent_backoff(setup_game,tmp_path):
    g,db,m,a,b=setup_game
    class Fail:
        async def metadata(self):raise AssertionError('must not contact source in backoff')
    await db.state('download_backoff_until',time.time()+100)
    worker=OfflineDownloader(db,g.c,Fail(),CFArchiveProvider(ArchiveFetcher(),tmp_path))
    assert await worker.harvest_page(force=True)==(0,0)


async def test_identical_submission_new_message_reuses_verdict(setup_game):
    g,db,m,a,b=setup_game
    await g.execute(a,parse('/new'))
    first=await g.execute(a,parse('/submit sum each a_i in O(n)'),'one')
    calls=len(m.calls)
    second=await g.execute(a,parse('/submit sum each a_i in O(n)'),'two')
    assert first.text.startswith('AC') and second.text.startswith('AC') and '+0' in second.text
    assert len(m.calls)==calls
    assert await db.run(lambda c:c.execute('SELECT count(*) FROM submissions').fetchone()[0])==2


async def test_bad_math_problem_is_quarantined_before_new_round(setup_game,problem):
    from astrbot_plugin_kouhu_judge.models import RenderContentError
    g,db,m,a,b=setup_game
    bad=copy.deepcopy(problem);bad.problem_id='cf:BAD';await db.add_problem(bad)
    choices=iter([bad,problem]);original_render=g.renderer.render
    async def choose(*args):return next(choices)
    async def render(p,*args):
        if p.problem_id=='cf:BAD':raise RenderContentError('formula error')
        return await original_render(p,*args)
    g.problems.choose=choose;g.renderer.render=render
    reply=await g.execute(a,parse('/new'))
    assert 'MOCK' in reply.images[0]
    assert await db.run(lambda c:c.execute("SELECT cache_status FROM problems WHERE problem_id='cf:BAD'").fetchone()[0])=='render_error'
    assert not await db.download_ready('cf:BAD')


def test_download_proxy_is_explicit_and_independent(monkeypatch):
    from astrbot_plugin_kouhu_judge.providers.common import Fetcher
    values=[]
    def fake(**kwargs):values.append(kwargs);return object()
    monkeypatch.setattr(httpx,'AsyncClient',fake)
    Fetcher(proxy='http://127.0.0.1:7897')
    assert values[0]['proxy']=='http://127.0.0.1:7897' and values[0]['trust_env'] is False
    assert 'Authorization' not in values[0]['headers']
