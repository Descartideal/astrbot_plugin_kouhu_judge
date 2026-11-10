import asyncio
import copy
import json
import sys
from pathlib import Path
from dataclasses import asdict
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from astrbot_plugin_kouhu_judge.config import Config
from astrbot_plugin_kouhu_judge.models import Problem,Sample,Identity,Judgment
from astrbot_plugin_kouhu_judge.database import Database
from astrbot_plugin_kouhu_judge.llm import LLMClient
from astrbot_plugin_kouhu_judge.judge_service import JudgeService
from astrbot_plugin_kouhu_judge.room_service import RoomService
from astrbot_plugin_kouhu_judge.problem_service import ProblemService


@pytest.fixture
def config():return Config(enabled_groups=['qq:GroupMessage:123'],retries=0,translate=False,judge_only=False,judge_two_stage=True,difficulty_mode='adaptive',background_download=False)


@pytest.fixture
def problem():
    # 显式 Mock 题库，只用于游戏流程测试，不作为随插件发行的可导入题库。
    return Problem('cf:MOCK','cf','Mock problem','https://codeforces.com/problemset/problem/999999/A','999999',2200,2200,
        'Mock full statement: compute a sum.', 'Read n and a_i.','Print the sum.',[Sample('3\n1 2 3','6')],
        'Official editorial placeholder for isolated tests only. '+('Verified algorithm evidence. '*4),
        'https://codeforces.com/blog/entry/999999',verified=True)


class MockLLM:
    def __init__(self):self.calls=[];self.options=[];self.entered=asyncio.Event();self.release=asyncio.Event();self.block=False;self.invalid=False;self.error=False

    async def __call__(self,system,prompt,provider,umo,**options):
        payload=json.loads(prompt);self.calls.append((system,payload,provider,umo))
        self.options.append(options)
        if self.error:raise TimeoutError()
        if self.invalid:return 'not JSON'
        if 'explicit_steps' in system:
            if self.block:self.entered.set();await self.release.wait()
            return json.dumps({'algorithm':payload['current_submission'],'explicit_steps':[],'complexity':'','covered_points':[],'contradictions':[],'unsupported_inferences':[]})
        if 'verdict' in system:
            combined=' '.join(s['content'] for s in payload['history'])+' '+payload['current_submission']
            ok='sum each a_i' in combined and 'O(n)' in combined
            j=Judgment('AC' if ok else 'WA',0.99,ok,ok,ok,feedback='OK' if ok else 'do not leak official solution')
            return json.dumps(asdict(j))
        if 'fields' in payload:return json.dumps({'fields':payload['fields']})
        return json.dumps({'scope':'statement' if payload.get('active') else 'general','reply':'这是题目定义的含义。'})


class MockRenderer:
    def __init__(self):self.calls=0;self.files={}
    async def render(self,p,umo='',refresh=False):
        self.calls+=1
        return self.files.setdefault(p.problem_id,[p.problem_id+'-statement.png',p.problem_id+'-samples.png'])


class MockRank:
    async def render(self,room,rows):return ['rank.png']


@pytest.fixture
async def setup_game(tmp_path,config,problem):
    db=Database(tmp_path/'game.sqlite3',config);await db.initialize();await db.add_problem(problem)
    mock=MockLLM();llm=LLMClient(mock,config);renderer=MockRenderer()
    game=RoomService(db,config,ProblemService(db,config),renderer,JudgeService(llm,config),MockRank(),llm)
    return game,db,mock,Identity('qq','A','123','甲'),Identity('qq','B','123','乙')
