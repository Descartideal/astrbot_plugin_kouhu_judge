import asyncio
import copy
import json
import sqlite3
import pytest
from astrbot_plugin_kouhu_judge.commands import parse
from astrbot_plugin_kouhu_judge.models import Judgment,Identity,Problem,GameError
from astrbot_plugin_kouhu_judge.database import Database,SCHEMA
from astrbot_plugin_kouhu_judge.rating import RatingStrategy


async def test_database_transaction_across_instances(setup_game):
    g,db,m,a,b=setup_game;await g.execute(a,parse('/new'));await db.touch(b,'submit')
    round_=await db.current(a.room_id)
    sa,_=await db.reserve(a,round_,'a','algorithm');sb,_=await db.reserve(b,round_,'b','algorithm')
    other=Database(db.path,g.c)
    j=Judgment('AC',1,True,True,True,feedback='ok')
    results=await asyncio.gather(db.finish(a,round_,sa['submission_id'],j,'mock',g.rating),other.finish(b,round_,sb['submission_id'],j,'mock',g.rating))
    assert sum(r['scored'] for r in results)==1
    assert await db.run(lambda c:c.execute('SELECT SUM(scored_solve_count) FROM users').fetchone()[0])==1


async def test_round_snapshot_survives_bank_update(setup_game,problem):
    g,db,m,a,b=setup_game;await g.execute(a,parse('/new'))
    changed=copy.deepcopy(problem);changed.normalized_rating=3000;changed.statement='changed problem'
    await db.add_problem(changed)
    r=await g.execute(a,parse('/submit sum each a_i in O(n)'),'msg')
    assert '1990' in r.text
    assert m.calls[-1][1]['problem']['statement']==problem.statement


async def test_chat_history_not_reused_across_rounds(setup_game):
    g,db,m,a,b=setup_game
    await g.chat(a,'general conversation')
    assert await db.chat(a)
    await g.execute(a,parse('/new'))
    await g.chat(a,'解释这句英文的含义')
    assert m.calls[-1][1]['history']==[]
    await g.execute(a,parse('/giveup'))
    await g.chat(a,'general again')
    assert m.calls[-1][1]['history']==[]


async def test_history_limit_does_not_score(setup_game):
    g,db,m,a,b=setup_game;await g.execute(a,parse('/new'));g.c.max_history_chars=5
    r=await g.execute(a,parse('/submit sum each a_i in O(n)'),'long-history')
    assert r.text.startswith('UNCERTAIN') and not m.calls


async def test_no_rescore_same_problem_dedup_disabled(setup_game):
    g,db,m,a,b=setup_game;g.c.dedup='none'
    for i in range(2):
        await g.execute(a,parse('/new'))
        await g.execute(a,parse('/submit sum each a_i in O(n)'),str(i))
    assert await db.run(lambda c:c.execute('SELECT SUM(scored) FROM solves').fetchone()[0])==1


async def test_practice_option_disabled(setup_game):
    g,db,m,a,b=setup_game;g.c.record_practice=False;await g.execute(a,parse('/new'))
    await g.execute(a,parse('/submit sum each a_i in O(n)'),'a')
    await g.execute(b,parse('/submit sum each a_i in O(n)'),'b')
    assert await db.run(lambda c:c.execute('SELECT COUNT(*) FROM solves').fetchone()[0])==1


async def test_missing_message_id(setup_game):
    g,db,m,a,b=setup_game;await g.execute(a,parse('/new'))
    with pytest.raises(GameError,match='消息 ID'):await g.execute(a,parse('/submit idea'))


async def test_future_db_version_rejected(tmp_path,config):
    path=tmp_path/'future.sqlite3'
    with sqlite3.connect(path) as c:c.execute('PRAGMA user_version=999')
    with pytest.raises(RuntimeError):await Database(path,config).initialize()


async def test_v1_migration(tmp_path,config,problem):
    path=tmp_path/'v1.sqlite3'
    with sqlite3.connect(path) as c:
        old=SCHEMA.replace(',problem_snapshot TEXT NOT NULL','').replace('user_version=5','user_version=1')
        c.executescript(old)
        c.execute('INSERT INTO problems VALUES(?,?,?,?,?,?,?,?,?,?,?)',(problem.problem_id,problem.platform,problem.title,2200,2200,problem.statement,problem.official_editorial,problem.editorial_url,'ready',0,problem.dump()))
        c.execute("INSERT INTO rooms VALUES('r','group','qq',?,1,0)",(problem.problem_id,))
        c.execute("INSERT INTO room_rounds(room_id,problem_id,generation,status,started_at) VALUES('r',?,1,'active',0)",(problem.problem_id,))
    db=Database(path,config);await db.initialize()
    row=await db.current('r');assert Problem.load(row['problem_snapshot']).statement==problem.statement
    assert await db.run(lambda c:c.execute('PRAGMA user_version').fetchone()[0])==5
