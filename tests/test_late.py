import asyncio
import copy
import pytest
from astrbot_plugin_kouhu_judge.commands import parse
from astrbot_plugin_kouhu_judge.models import GameError,Identity
from astrbot_plugin_kouhu_judge.database import Database

@pytest.mark.parametrize('token',['CF1842H','Cf1842h','cF1842H','cf:1842h','1842h'])
def test_cf_case(token):
 c=parse('late '+token+' 先排序',bare=True)
 assert c.problem_id=='cf:1842H' and c.argument=='先排序'

@pytest.mark.parametrize('token',['abc306_g','ABC306_G','at:AbC306_g'])
def test_at_case(token):assert parse('/LATE '+token+' 用最短路').problem_id=='at:abc306_g'

@pytest.mark.parametrize('text',['late','late CF1842H','late abc306_g'])
def test_empty(text):
 with pytest.raises(GameError):parse(text,bare=True)

async def prepare(setup_game,problem):
 g,db,m,a,b=setup_game
 old=copy.deepcopy(problem);old.problem_id='cf:1842H';old.normalized_rating=2300;await db.add_problem(old)
 new=copy.deepcopy(problem);new.problem_id='at:abc306_g';new.platform='at';new.normalized_rating=2500;await db.add_problem(new)
 await g.execute(a,parse('/new cf 2300-2300'))
 return g,db,m,a,b

async def test_late_wa_ac_isolated_and_idempotent(setup_game,problem):
 g,db,m,a,b=await prepare(setup_game,problem)
 await g.execute(a,parse('/submit sum each a_i'),'old-a')
 await g.execute(b,parse('/submit OTHER_USER'),'old-b')
 old=await db.current(a.room_id)
 await g.execute(a,parse('/giveup'))
 await g.execute(a,parse('/new at 2500-2500'))
 await g.execute(a,parse('/submit CURRENT_PROBLEM'),'cur-a')
 current=await db.current(a.room_id)
 before=await db.run(lambda c:tuple(c.execute('SELECT rating,scored_solve_count FROM users WHERE user_id=?',(a.user_id,)).fetchone()))
 response=await g.execute(a,parse('late O(n)',bare=True),'late-1')
 assert response.text.startswith('补交 AC') and '不补发' in response.text
 payload=m.calls[-1][1]
 assert payload['problem']['problem_id']=='cf:1842H'
 assert [h['content'] for h in payload['history']]==['sum each a_i']
 assert (await db.current(a.room_id))==current
 assert (await db.run(lambda c:tuple(c.execute('SELECT rating,scored_solve_count FROM users WHERE user_id=?',(a.user_id,)).fetchone())))==before
 assert await db.run(lambda c:c.execute('SELECT status FROM room_rounds WHERE round_id=?',(old['round_id'],)).fetchone()[0])=='given_up'
 calls=len(m.calls)
 assert '已处理' in (await g.execute(a,parse('late O(n)',bare=True),'late-1')).text
 assert len(m.calls)==calls
 await g.execute(a,parse('late 再补充',bare=True),'late-2')
 assert [h['content'] for h in m.calls[-1][1]['history']]==['sum each a_i','O(n)']
 assert (await db.current(a.room_id))==current
 assert await db.run(lambda c:c.execute('SELECT count(*) FROM solves WHERE scored=1').fetchone()[0])==0
 assert await db.run(lambda c:c.execute('SELECT count(*) FROM submissions WHERE round_id=?',(current['round_id'],)).fetchone()[0])==1

async def test_explicit_without_new_and_other_room_rejected(setup_game,problem):
 g,db,m,a,b=await prepare(setup_game,problem)
 with pytest.raises(GameError):await g.execute(a,parse('/late cf1842h 做法'),'active')
 await g.execute(a,parse('/giveup'))
 with pytest.raises(GameError):await g.execute(a,parse('/late 做法'),'no-new')
 result=await g.execute(a,parse('/late cF1842h sum each a_i in O(n)'),'explicit')
 assert result.text.startswith('补交 AC') and await db.current(a.room_id) is None
 outsider=Identity('qq','A','999')
 g.c.enabled_groups.append('qq:GroupMessage:999')
 with pytest.raises(GameError):await g.execute(outsider,parse('/late CF1842H 做法'),'foreign')

async def test_late_during_giveup_and_new(setup_game,problem):
 g,db,m,a,b=await prepare(setup_game,problem)
 await g.execute(a,parse('/giveup'));await g.execute(a,parse('/new at 2500-2500'))
 m.block=True
 task=asyncio.create_task(g.execute(a,parse('/late CF1842H sum each a_i in O(n)'),'racing'))
 await asyncio.wait_for(m.entered.wait(),2)
 await g.execute(b,parse('/giveup'));await g.execute(b,parse('/new cf 2300-2300'))
 newest=await db.current(a.room_id)
 m.release.set();assert (await task).text.startswith('补交 AC')
 assert (await db.current(a.room_id))==newest
 assert newest['status']=='active'

async def test_v3_migration_and_late_restart(setup_game,problem):
 g,db,m,a,b=await prepare(setup_game,problem)
 await db.run(lambda c:c.execute('DROP TABLE late_submissions'),True)
 await db.run(lambda c:c.execute('PRAGMA user_version=3'),True)
 restored=Database(db.path,g.c);await restored.initialize()
 assert await restored.run(lambda c:c.execute('PRAGMA user_version').fetchone()[0])==5
 await g.execute(a,parse('/giveup'))
 await g.execute(a,parse('/late CF1842H sum each a_i in O(n)'),'persist')
 await restored.initialize()
 assert await restored.run(lambda c:c.execute('SELECT verdict FROM late_submissions').fetchone()[0])=='AC'

async def test_late_failure_keeps_current_and_normal_ids(setup_game,problem):
 g,db,m,a,b=await prepare(setup_game,problem)
 await g.execute(a,parse('/giveup'));await g.execute(a,parse('/new at 2500-2500'))
 current=await db.current(a.room_id)
 result=await g.execute(a,parse('/late CF1842H greedy'),'failed-late')
 assert result.text.startswith('补交 WA') and await db.current(a.room_id)==current
 assert await db.history(current['round_id'],a.user_id)==[]
 calls=len(m.calls)
 await g.execute(a,parse('/submit sum each a_i in O(n)'),'failed-late')
 assert len(m.calls)==calls
 m.error=True
 result=await g.execute(a,parse('/late CF1842H another'),'llm-failure')
 assert result.text.startswith('补交 UNCERTAIN') and await db.current(a.room_id)==current

async def test_closed_solved_round_no_second_first_blood(setup_game,problem):
 g,db,m,a,b=await prepare(setup_game,problem)
 await g.execute(a,parse('/submit sum each a_i in O(n)'),'first-ac')
 old=await db.current(a.room_id)
 await g.execute(a,parse('/new at 2500-2500'))
 current=await db.current(a.room_id)
 result=await g.execute(b,parse('/late cF1842h sum each a_i in O(n)'),'late-b')
 assert result.text.startswith('补交 AC')
 rr=await db.run(lambda c:dict(c.execute('SELECT * FROM room_rounds WHERE round_id=?',(old['round_id'],)).fetchone()))
 assert rr['first_blood_user_id']==a.user_id and rr['status']=='closed'
 assert await db.run(lambda c:c.execute('SELECT count(*) FROM solves WHERE scored=1').fetchone()[0])==1
 assert await db.current(a.room_id)==current


def test_late_cpp_not_misrouted_as_current_submit():
 command=parse('/late cF1842h #include <bits/stdc++.h>\nint main(){return 0;}',direct_code=True)
 assert command.name=='late' and command.problem_id=='cf:1842H'
 assert command.argument.startswith('#include')


async def test_implicit_late_waits_for_new_round(setup_game,problem):
    game,db,llm,player,other=await prepare(setup_game,problem)
    await game.execute(player,parse('/submit sum each a_i in O(n)'),'first-solved')
    entered=asyncio.Event()
    release=asyncio.Event()
    render=game.renderer.render

    async def delayed_render(*args):
        entered.set()
        await release.wait()
        return await render(*args)

    game.renderer.render=delayed_render
    new=asyncio.create_task(game.execute(other,parse('/new at 2500-2500')))
    await asyncio.wait_for(entered.wait(),2)
    late=asyncio.create_task(game.execute(player,parse('/late O(n)'),'during-new'))
    try:
        # While the renderer holds the room lock, no late target can be reserved.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(late),0.1)
    finally:
        release.set()
    await new
    assert 'cf:1842H' in (await late).text
    assert llm.calls[-1][1]['problem']['problem_id']=='cf:1842H'
