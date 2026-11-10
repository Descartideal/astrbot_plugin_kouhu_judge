import asyncio
import copy
import json
import pytest
from astrbot_plugin_kouhu_judge.preparation_service import PreparationService
from astrbot_plugin_kouhu_judge.models import Identity,GameError
from astrbot_plugin_kouhu_judge.commands import parse
from astrbot_plugin_kouhu_judge.database import Database
from astrbot_plugin_kouhu_judge.renderer import Renderer

async def setup(setup_game,problem):
 g,db,m,a,b=setup_game
 g.c.default_low=2000;g.c.default_high=2500
 for n,rating in enumerate((2300,2400,3000)):
  p=copy.deepcopy(problem);p.problem_id=f'cf:P{n}';p.normalized_rating=rating;await db.add_problem(p)
 prep=PreparationService(db,g.c,g.problems,g.renderer,lambda p:True);g.preparation=prep
 return g,db,prep,a,b

async def drain(prep):
 tasks=[t for t in prep.tasks.values() if not t.done()]
 if tasks:await asyncio.gather(*tasks)

async def test_ready_consumed_exactly_and_refills(setup_game,problem):
 g,db,p,a,b=await setup(setup_game,problem)
 try:
  ready=await p.ensure(Identity('qq','','123'),'qq:GroupMessage:123')
  assert await db.current(a.room_id) is None
  assert await db.run(lambda c:c.execute('SELECT count(*) FROM users').fetchone()[0])==0
  assert await db.run(lambda c:c.execute('SELECT count(*) FROM room_rounds').fetchone()[0])==0
  result=await g.execute(a,parse('/new'))
  assert result.images==json.loads(ready['images'])
  assert (await db.current(a.room_id))['problem_id']==ready['problem_id']
  assert g.renderer.calls==1
  await drain(p)
  next_=await db.prepared(a.room_id)
  assert next_ and next_['problem_id']!=ready['problem_id']
  with pytest.raises(GameError,match='已有活动题'):await g.execute(b,parse('/new'))
  assert (await db.prepared(a.room_id))['token']==next_['token']
  await g.execute(a,parse('/giveup'))
  await g.execute(b,parse('/new'))
  assert (await db.current(a.room_id))['problem_id']==next_['problem_id']
 finally:await p.close()

async def test_defaults_change_invalidate_same_defaults_keep(setup_game,problem):
 g,db,p,a,b=await setup(setup_game,problem)
 try:
  await g.execute(a,parse('/new'));await drain(p)
  active=await db.current(a.room_id);ready=await db.prepared(a.room_id)
  await g.execute(a,parse('/kh setdefault cf 2000-2500'),admin=True);await drain(p)
  assert (await db.prepared(a.room_id))['token']==ready['token']
  await g.execute(a,parse('/kh setdefault cf 3000-3000'),admin=True);await drain(p)
  changed=await db.prepared(a.room_id)
  assert changed['token']!=ready['token'] and changed['problem_id']=='cf:P2'
  assert await db.current(a.room_id)==active
 finally:await p.close()

async def test_prepared_survives_restart(setup_game,problem):
 g,db,p,a,b=await setup(setup_game,problem)
 ready=await p.ensure(a);calls=g.renderer.calls;await p.close()
 restored=Database(db.path,g.c);await restored.initialize()
 other=PreparationService(restored,g.c,g.problems,g.renderer,lambda f:True)
 try:
  assert (await other.ensure(a))['token']==ready['token']
  assert g.renderer.calls==calls
 finally:await other.close()

async def test_explicit_new_preserves_other_default_prepared(setup_game,problem):
 g,db,p,a,b=await setup(setup_game,problem)
 try:
  ready=await p.ensure(a)
  await g.execute(a,parse('/new cf 3000-3000'));await drain(p)
  assert (await db.current(a.room_id))['problem_id']=='cf:P2'
  assert (await db.prepared(a.room_id))['token']==ready['token']
 finally:await p.close()

async def test_enabled_groups_prepare_without_users_or_rounds(setup_game,problem):
 g,db,p,a,b=await setup(setup_game,problem)
 g.c.enabled_groups.append('qq:GroupMessage:456')
 try:
  p.start()
  async def ready():
   while await db.run(lambda c:c.execute('SELECT count(*) FROM prepared_problems').fetchone()[0])<2:await asyncio.sleep(.01)
  await asyncio.wait_for(ready(),3)
  assert await db.run(lambda c:c.execute('SELECT count(*) FROM users').fetchone()[0])==0
  assert await db.run(lambda c:c.execute('SELECT count(*) FROM room_rounds').fetchone()[0])==0
  assert await db.run(lambda c:c.execute('SELECT count(*) FROM solves').fetchone()[0])==0
 finally:await p.close()

async def test_two_new_atomic(setup_game,problem):
 g,db,p,a,b=await setup(setup_game,problem)
 try:
  ready=await p.ensure(a)
  results=await asyncio.gather(g.execute(a,parse('/new')),g.execute(b,parse('/new')),return_exceptions=True)
  assert sum(isinstance(r,GameError) for r in results)==1
  assert await db.run(lambda c:c.execute('SELECT count(*) FROM room_rounds').fetchone()[0])==1
  assert (await db.current(a.room_id))['problem_id']==ready['problem_id']
 finally:await p.close()

async def test_stale_generation_cancelled_on_default_change(setup_game,problem):
 g,db,p,a,b=await setup(setup_game,problem);entered=asyncio.Event();release=asyncio.Event()
 original=g.renderer.render
 async def slow(problem,*args,**kwargs):
  if problem.normalized_rating!=3000:entered.set();await release.wait()
  return await original(problem,*args,**kwargs)
 g.renderer.render=slow
 try:
  p.schedule(a)
  await asyncio.wait_for(entered.wait(),2)
  await g.execute(a,parse('/kh setdefault cf 3000-3000'),admin=True)
  release.set();await drain(p)
  assert (await db.prepared(a.room_id))['problem_id']=='cf:P2'
 finally:await p.close()

async def test_bad_file_rebuild(setup_game,problem):
 g,db,p,a,b=await setup(setup_game,problem)
 try:
  ready=await p.ensure(a);p.valid=lambda f:False
  with pytest.raises(GameError):await p.ensure(a)
  assert await db.prepared(a.room_id) is None
  p.valid=lambda f:True
  assert (await p.ensure(a))['token']!=ready['token']
 finally:await p.close()

async def test_cached_renderer_not_blocked_by_background(config,tmp_path,problem):
 r=Renderer(config,tmp_path,None);r._cached_images=lambda p:['cached-statement','cached-samples']
 async with r.render_lock:assert await asyncio.wait_for(r.render(problem),.1)==['cached-statement','cached-samples']
 await r.close()

async def test_target_closed_recovers_once(config,tmp_path,problem):
 r=Renderer(config,tmp_path,None);calls=[];resets=[]
 Error=type('TargetClosedError',(Exception,),{})
 async def once(*args):
  calls.append(1)
  if len(calls)==1:raise Error()
  return ['ok']
 async def reset():resets.append(1)
 r._render_once=once;r._reset_browser=reset
 assert await r.render(problem)==['ok'] and len(calls)==2 and len(resets)==1
 await r.close()

async def test_ready_rebuilt_after_bank_content_change(setup_game,problem):
 from astrbot_plugin_kouhu_judge.models import Problem
 g,db,p,a,b=await setup(setup_game,problem)
 try:
  ready=await p.ensure(a)
  changed=Problem.load(ready['snapshot']);changed.statement+=' corrected source';await db.add_problem(changed)
  assert (await p.ensure(a))['token']!=ready['token']
 finally:await p.close()

async def test_private_new_no_group_preparation(setup_game,problem):
 g,db,p,a,b=await setup(setup_game,problem)
 try:
  private=Identity('qq','private-player')
  await g.execute(private,parse('/new'))
  assert await db.prepared(private.room_id) is None
  assert await db.current(private.room_id)
 finally:await p.close()

def test_archive_bad_math_is_skippable(tmp_path):
 import pytest
 from test_v2 import archive_item
 from astrbot_plugin_kouhu_judge.providers.cf_archive import CFArchiveProvider
 item=archive_item();item['row']['input_format']='Read q (1 ≤ q ≤ 105).'
 with pytest.raises(ValueError):CFArchiveProvider(None,tmp_path).convert(item,{'999999A':{'name':'Mock problem','rating':1600,'tags':[]}})
