import copy
import pytest
from bs4 import BeautifulSoup
from astrbot_plugin_kouhu_judge.providers.common import text
from astrbot_plugin_kouhu_judge.config import Config
from astrbot_plugin_kouhu_judge.commands import parse
from astrbot_plugin_kouhu_judge.models import RenderContentError
from astrbot_plugin_kouhu_judge.renderer import Renderer

def test_dom_preserves_powers_and_subscripts():
 value=text(BeautifulSoup('<p><var>n<sub>i</sub></var> ≤ <span class="tex-span">10<sup>9</sup></span>; q ≤ 10<sup>5</sup></p>','html.parser'))
 assert r'\(n_{i}\)' in value and r'\(10^{9}\)' in value and r'\(10^{5}\)' in value

def test_zero_configuration():
 c=Config.from_mapping({'default_low':0,'default_high':0,'download_low':0})
 assert c.default_low==0 and parse('new cf 0-0',bare=True).low==0

async def test_zero_candidate_and_default(setup_game,problem):
 g,db,m,a,b=setup_game
 p=copy.deepcopy(problem);p.problem_id='cf:UNRATED';p.original_rating=p.normalized_rating=0
 await db.add_problem(p)
 await db.touch(a,'new')
 selected=await g.problems.choose(a,parse('new cf 0-0',bare=True))
 assert selected.problem_id==p.problem_id
 await g.execute(a,parse('/kh setdefault cf 0-800'),admin=True)
 assert (await g.problems.defaults(a))[1:]==(0,800)

def test_unverified_flattened_bounds_rejected(problem):
 problem.input_spec='Read q (1 ≤ q ≤ 105).'
 with pytest.raises(RenderContentError):problem.public()

def test_rating_absent_from_both_pages(config,tmp_path,problem):
 r=Renderer(config,tmp_path,None)
 for samples in (False,True):
  html=r.html(problem.public(),samples)
  meta=BeautifulSoup(html,'html.parser').select_one('.meta').get_text()
  assert 'CF 难度' not in meta and '2200' not in meta

async def test_cf_missing_rating_kept_as_zero(config,tmp_path):
 from astrbot_plugin_kouhu_judge.providers.codeforces import CodeforcesProvider
 class Fetch:
  async def get(self,*args):return {'status':'OK','result':{'problems':[{'contestId':9,'index':'A','name':'Unrated'},{'contestId':9,'index':'B','name':'Rated','rating':800}]}}
 rows=await CodeforcesProvider(Fetch(),None,tmp_path,config).metadata()
 assert [r['rating'] for r in rows]==[0,800]

def test_katex_annotation_preserved():
 value=text(BeautifulSoup('<span class="katex"><span>fake rendered</span><annotation encoding="application/x-tex">10^{9}</annotation></span>','html.parser'))
 assert value==r'\(10^{9}\)'
