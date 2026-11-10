import pytest
from astrbot_plugin_kouhu_judge.commands import parse,looks_like_code
from astrbot_plugin_kouhu_judge.models import GameError,Identity


@pytest.mark.parametrize('s',['new','/new',' /NEW ',' NeW  '])
def test_new(s):assert parse(s,bare=True).name=='new'


@pytest.mark.parametrize('s',['/new cf 1400-1800','new CF 1400 - 1800'])
def test_range(s):
    c=parse(s,bare=True);assert (c.platform,c.low,c.high)==('cf',1400,1800)


def test_minimum():assert parse('/new at 0-1300').low==0


@pytest.mark.parametrize('s',['/new cf 1200-1100','/new cf 2000-1200','/new xx 2000-2400','/cur arg'])
def test_bad_arguments(s):
    with pytest.raises(GameError):parse(s)


@pytest.mark.parametrize('s',['你好','这个 new 应该怎么用','我已经 AC','def f(): pass','print(1)','ping','/help'])
def test_do_not_capture(s):assert parse(s) is None


def test_bare_switch():
    assert parse('new') is None
    assert parse('new',bare=True).name=='new'
    assert parse('/kh help').name=='help'
    assert parse('/ping',generic=True).name=='ping'


def test_arbitrary_submit():
    content='a\n'+('x '*10000)
    assert parse('/SuBmIt '+content).argument==content.strip()


@pytest.mark.parametrize('code',['#include <bits/stdc++.h>\nint main(){return 0;}','import sys\na=list(map(int,input().split()))\nprint(sum(a))','```python\nimport sys\nprint(sys.stdin.read())\n```','n=int(input())\nprint(n)'])
def test_code(code):
    assert looks_like_code(code)
    assert parse(code,direct_code=True).name=='submit'


def test_whitelist(config):
    assert config.allowed(Identity('qq','A','123'))
    assert not config.allowed(Identity('other','A','123'))
    assert not config.allowed(Identity('qq','A','999'))
    config.enabled_groups.append('qq:GroupMessage:10001')
    assert config.allowed(Identity('qq','A','10001'))


def test_namespaces():
    assert len({Identity('qq','123').room_id,Identity('qq','X','123').room_id,Identity('other','123').room_id})==3


@pytest.mark.parametrize('text,platform,rating',[
    ('new cf 1200','cf',1200),('/new CF 1200','cf',1200),
    (' /NeW cF 1200 [MSG_ID:123] ','cf',1200),
    ('/kh new At 1800','at',1800),('new 1200',None,1200),
    ('/new 0',None,0),('/new cf 25','cf',25),('/new at 50','at',50),
])
def test_single_rating(text,platform,rating):
    cmd=parse(text,bare=True)
    assert (cmd.platform,cmd.low,cmd.high)==(platform,max(0,rating-50),rating+50)

@pytest.mark.parametrize('text',['/new cf -1','/new cf 1200 1300','/new cf 1200-','/new cf 12.5'])
def test_invalid_single_rating(text):
    with pytest.raises(GameError):parse(text)

async def test_single_rating_selects_range_instead_of_default(setup_game,problem):
    import copy
    g,db,*rest=setup_game
    who=rest[-2]
    for rating in [1150,1200,1250,1300]:
        p=copy.deepcopy(problem);p.problem_id='cf:MOCK'+str(rating);p.normalized_rating=rating
        await db.add_problem(p)
    result=await g.execute(who,parse('new cf 1200',bare=True))
    assert any('MOCK'+str(x) in result.images[0] for x in [1150,1200,1250])
    assert '1300' not in result.images[0]
