import copy
import pytest
from astrbot_plugin_kouhu_judge.commands import parse
from astrbot_plugin_kouhu_judge.models import GameError
from astrbot_plugin_kouhu_judge.original_service import OriginalStatementService
from astrbot_plugin_kouhu_judge.renderer import Renderer
from conftest import MockRenderer

@pytest.mark.parametrize('text',['cue','/cue',' CUE ','/CuE [MSG_ID:123]'])
def test_cue_parser(text):assert parse(text,bare=True).name=='cue'

def test_cue_rejects_arguments():
 with pytest.raises(GameError):parse('/cue cf123A')

async def test_cue_original_no_rewrite(problem):
 problem.statement='Once upon a time there was a story.\n\nFind the number of valid arrays.'
 source=await OriginalStatementService().translate(problem)
 assert source['statement']==problem.statement and source['title']==problem.title
 assert source['samples'][0]['input']==problem.samples[0].input
 assert source['_original_view'] and 'official_editorial' not in source

async def test_cue_missing_english_not_backtranslated(problem):
 problem.verification['statement_language']='ja'
 with pytest.raises(GameError,match='暂无英文'):await OriginalStatementService().translate(problem)

async def test_cue_does_not_change_round_or_judge(setup_game):
 g,db,m,a,b=setup_game
 with pytest.raises(GameError,match='没有正在做'):await g.execute(a,parse('/cue'))
 await g.execute(a,parse('/new'))
 before=await db.current(a.room_id);original=MockRenderer();g.original_renderer=original
 first=await g.execute(b,parse('cue',bare=True));second=await g.execute(a,parse('/cue'))
 assert len(first.images)==2 and first.images==second.images and '英文原版' in first.text
 assert await db.current(a.room_id)==before and not m.calls
 assert await db.run(lambda c:c.execute('SELECT count(*) FROM submissions').fetchone()[0])==0

async def test_original_template_complete_and_rating_hidden(config,tmp_path,problem):
 problem.statement='Once upon a time there was a story.\n\nFind the number of valid arrays.'
 p=await OriginalStatementService().translate(problem)
 r=Renderer(config,tmp_path,OriginalStatementService())
 from bs4 import BeautifulSoup
 for samples in (False,True):
  html=r.html(p,samples);page=BeautifulSoup(html,'html.parser')
  assert page.html['lang']=='en' and 'CF 难度' not in page.select_one('.meta').get_text()
  assert ('Examples and explanations' if samples else 'Problem statement') in html
  if not samples:assert 'Once upon a time there was a story.' in html
 await r.close()
