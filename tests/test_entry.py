import importlib
import json
import sys
import types
from pathlib import Path
from enum import Flag,auto
import pytest
from conftest import MockLLM,MockRenderer,MockRank


@pytest.fixture
def plugin(tmp_path,monkeypatch,config):
    class Star:
        def __init__(self,context):self.context=context
    class At:
        def __init__(self,qq):self.qq=qq
    class E(Flag):
        GROUP_MESSAGE=auto();PRIVATE_MESSAGE=auto()
    class CustomFilter:
        def __init__(self,*args,**kwargs):pass
    def decorate(*args,**kwargs):
        assert kwargs['priority']==100
        return lambda fn:fn
    api=types.ModuleType('astrbot.api');api.AstrBotConfig=dict;api.logger=types.SimpleNamespace(warning=lambda *a:None,error=lambda *a:None)
    events=types.ModuleType('astrbot.api.event');events.AstrMessageEvent=object;events.filter=types.SimpleNamespace(EventMessageType=E,event_message_type=decorate,CustomFilter=CustomFilter,custom_filter=decorate)
    star=types.ModuleType('astrbot.api.star');star.Context=object;star.Star=Star;star.StarTools=types.SimpleNamespace(get_data_dir=lambda name:tmp_path/name)
    components=types.ModuleType('astrbot.api.message_components');components.At=At
    for key,value in {'astrbot':types.ModuleType('astrbot'),'astrbot.api':api,'astrbot.api.event':events,'astrbot.api.star':star,'astrbot.api.message_components':components}.items():monkeypatch.setitem(sys.modules,key,value)
    name='astrbot_plugin_kouhu_judge.main';sys.modules.pop(name,None)
    module=importlib.import_module(name)
    class Context:
        async def get_current_chat_provider_id(self,umo):return 'mock-provider'
        async def llm_generate(self,**kwargs):return types.SimpleNamespace(completion_text='{}')
    p=module.KouhuJudgePlugin(Context(),{'enabled_groups':['qq:GroupMessage:123'],'bare_commands':True,'translate':False,'judge_only':False,'judge_two_stage':True,'difficulty_mode':'adaptive','background_download':False,'group_prefetch':False})
    p.renderer=MockRenderer();p.game.renderer=p.renderer;p.game.rank_renderer=MockRank()
    mock=MockLLM();p.llm.generate=mock
    return p,At,mock


class MessageResult(tuple):
    def __new__(cls,kind,value):return super().__new__(cls,(kind,value))
    @property
    def chain(self):return [(self[0],self[1])]


class Event:
    def __init__(self,text,group='123',uid='A',mid='1',mentions=None):
        self.message_str=text;self.group=group;self.uid=uid;self.sent=[];self.stopped=False;self.llm=True;self.mentions=mentions or []
        self.message_obj=types.SimpleNamespace(message_id=mid)
        self.unified_msg_origin=f'qq:GroupMessage:{group}'
    def get_platform_id(self):return 'qq'
    def get_sender_id(self):return self.uid
    def get_group_id(self):return self.group
    def get_sender_name(self):return 'Player '+self.uid
    def get_messages(self):return self.mentions
    def get_self_id(self):return 'BOT'
    def should_call_llm(self,value):self.llm=value
    def stop_event(self):self.stopped=True
    def plain_result(self,value):return MessageResult('text',value)
    def image_result(self,value):return MessageResult('image',value)
    def chain_result(self,value):return ('chain',value)
    def is_admin(self):return False
    async def send(self,value):self.sent.append(value)
    async def get_group(self):return types.SimpleNamespace(members=[])


async def test_route_sends_two_images_in_order(plugin,problem):
    p,At,m=plugin;await p.initialize();await p.db.add_problem(problem)
    e=Event('/new cf 2000-2400');await p.route(e)
    assert [x[0] for x in e.sent]==['text','chain']
    assert [x[0] for x in e.sent[1][1]]==['image','image','text']
    assert e.sent[0][1]=='正在抽题，题面马上发出。'
    assert e.stopped and not e.llm
    c=Event('cur',uid='B',mid='2');await p.route(c)
    assert c.sent==[e.sent[1]]
    await p.fetcher.close()


@pytest.mark.parametrize('text,group',[('hello','123'),('/new','999'),('import sys\nprint(input())','123')])
async def test_router_does_not_capture_other_messages(plugin,text,group):
    p,At,m=plugin;await p.initialize()
    e=Event(text,group=group);await p.route(e)
    assert not e.stopped and not e.sent and e.llm
    await p.fetcher.close()


async def test_mention_answer_does_not_reach_default_chat(plugin,problem):
    p,At,m=plugin;await p.initialize();await p.db.add_problem(problem)
    await p.route(Event('new'))
    e=Event('请给我答案',mentions=[At('BOT')]);await p.route(e)
    assert e.stopped and not e.llm and '只提供翻译' in e.sent[0][1]
    assert not m.calls
    await p.fetcher.close()


async def test_private_common_commands_only_scoped(plugin,problem):
    p,At,m=plugin;await p.initialize();await p.db.add_problem(problem)
    e=Event('/help',group='');await p.route(e);assert not e.stopped
    e=Event('/kh help',group='');await p.route(e);assert e.stopped
    await p.route(Event('/new',group=''))
    e=Event('/clear',group='');await p.route(e);assert e.stopped
    await p.fetcher.close()


@pytest.mark.parametrize('text,group,expected',[('ordinary conversation','123',False),('/new','999',False),('/new','123',True),('/help','',False),('/kh help','',True)])
async def test_scope_filter_does_not_wake_unrelated_messages(plugin,text,group,expected):
    p,At,m=plugin;await p.initialize()
    assert p.accepts_event(Event(text,group=group)) is expected
    await p.fetcher.close()


async def test_wake_prefix_removed_by_real_pipeline(plugin,problem):
    p,At,m=plugin;await p.initialize();await p.db.add_problem(problem);p.c.bare_commands=False
    e=Event('new cf 2000-2400');e.message_obj.message_str='/new cf 2000-2400'
    assert p.accepts_event(e)
    await p.route(e)
    assert [r[0] for r in e.sent]==['text','chain']
    assert [x[0] for x in e.sent[1][1]]==['image','image','text']
    assert not p.accepts_event(Event('new'))
    await p.fetcher.close()


async def test_scope_active_rooms_recovered_after_initialize(plugin,problem):
    p,At,m=plugin;await p.initialize();await p.db.add_problem(problem)
    await p.route(Event('/new',group=''))
    assert p.accepts_event(Event('/clear',group=''))
    p.active_rooms.clear();await p.initialize()
    assert p.accepts_event(Event('/clear',group=''))
    await p.fetcher.close()

async def test_main_model_fallback_no_chat_context(plugin):
    p,_,_=plugin;seen=[]
    async def generate(**kw):
        seen.append(kw);return types.SimpleNamespace(completion_text='{}')
    p.context.llm_generate=generate
    assert await p.generate('judge rules','submission payload','','qq:FriendMessage:A')=='{}'
    assert seen==[{'chat_provider_id':'mock-provider','prompt':'submission payload','system_prompt':'judge rules'}]
    await p.fetcher.close();await p.api.close()

async def test_dedicated_overrides_main(plugin):
    p,_,_=plugin;p.c.api_key='test'
    async def dedicated(*args):return 'dedicated'
    async def forbidden(**kw):raise AssertionError('must not fallback when dedicated is configured')
    p.api.generate=dedicated;p.context.llm_generate=forbidden
    assert await p.generate('s','p','','room')=='dedicated'
    await p.fetcher.close();await p.api.close()


async def test_translation_always_uses_main_provider_even_with_dedicated_key(plugin):
    p,_,_=plugin;p.c.api_key='test';p.c.translation_use_dedicated=False;calls=[]
    async def main(**kw):calls.append(kw);return types.SimpleNamespace(completion_text='{}')
    async def forbidden(*args):raise AssertionError('translation cannot call dedicated judge')
    p.context.llm_generate=main;p.api.generate=forbidden
    assert await p.generate_translation('translate','fields','','room')=='{}'
    assert calls[0]['chat_provider_id']=='mock-provider' and 'contexts' not in calls[0]
    await p.fetcher.close();await p.api.close()


async def test_translation_prefers_dedicated_api(plugin):
    p,_,_=plugin;p.c.api_key='test';seen=[]
    async def dedicated(*args,**kw):seen.append(kw);return '{}'
    async def forbidden(**kw):raise AssertionError('main model must not be called')
    p.api.generate=dedicated;p.context.llm_generate=forbidden
    assert await p.generate_translation('s','p','','room')=='{}'
    assert seen==[{'max_output_tokens':4096}]
    await p.fetcher.close();await p.api.close()

async def test_cue_route_single_original_chain(plugin,problem):
    p,_,m=plugin;await p.initialize();await p.db.add_problem(problem)
    await p.route(Event('/new'))
    room_id=json.dumps(['qq','group','123'],separators=(',',':'))
    before=await p.db.current(room_id)
    p.game.original_renderer=MockRenderer()
    e=Event('cue',mid='cue-id');assert p.accepts_event(e)
    await p.route(e)
    assert len(e.sent)==1 and [x[0] for x in e.sent[0][1]]==['image','image','text']
    assert '英文原版' in e.sent[0][1][-1][1] and not m.calls
    assert await p.db.current(room_id)==before
    await p.preparation.close();await p.original_renderer.close();await p.fetcher.close();await p.api.close()
