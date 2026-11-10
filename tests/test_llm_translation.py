import asyncio
import json
from dataclasses import asdict
import pytest
from astrbot_plugin_kouhu_judge.llm import LLMClient,LLMFailure,json_object
from astrbot_plugin_kouhu_judge.models import Judgment
from astrbot_plugin_kouhu_judge.judge_service import JudgeService
from astrbot_plugin_kouhu_judge.translation_service import TranslationService,protect,restore


def test_math_round_trip():
    original=r'For \(x_i\le 10^{18}\), use $\sum_{i=1}^n a_i$, `N`, 123.'
    protected,values=protect(original)
    assert restore(protected,values)==original
    with pytest.raises(ValueError):restore(protected+'⟦KH0⟧',values)
    with pytest.raises(ValueError):restore('',values)


async def test_translation_cache_no_editorial(tmp_path,config,problem):
    config.translate=True;calls=[]
    async def generate(system,prompt,provider,umo):
        value=json.loads(prompt);calls.append(value)
        assert 'official_editorial' not in prompt and problem.official_editorial not in prompt
        return json.dumps({'fields':{k:('中文：'+v if v else '') for k,v in value['fields'].items()}})
    service=TranslationService(LLMClient(generate,config),config,tmp_path)
    translated=await service.translate(problem)
    assert translated['_translation']=='zh'
    assert translated['samples'][0]['input']==problem.samples[0].input
    assert await service.translate(problem)==translated
    assert len(calls)==1
    await service.translate(problem,refresh=True);assert len(calls)==2


async def test_translation_missing_token_falls_back(tmp_path,config,problem):
    config.translate=True;problem.statement=r'Read \(N\) between 1 and 100.'
    async def bad(system,prompt,provider,umo):
        fields=json.loads(prompt)['fields'];fields['statement']='wrong translation';return json.dumps({'fields':fields})
    from astrbot_plugin_kouhu_judge.models import GameError
    with pytest.raises(GameError,match='中文题面生成失败'):
        await TranslationService(LLMClient(bad,config),config,tmp_path).translate(problem)
    assert not list(tmp_path.glob('*.json'))


async def test_timeout_and_retry(config):
    config.timeout=0.01;config.retries=1;calls=[]
    async def slow(*args):calls.append(1);await asyncio.sleep(0.1);return '{}'
    with pytest.raises(LLMFailure):await LLMClient(slow,config).call('',{})
    assert len(calls)==2


@pytest.mark.parametrize('change',[{'confidence':0.2},{'core_difficulty_covered':False},{'complexity_valid':False},{'main_idea_correct':False},{'confidence':'1'},{'confidence':float('nan')},{'main_idea_correct':'true'}])
async def test_ac_gate(config,problem,change):
    j=asdict(Judgment('AC',0.99,True,True,True,feedback='accepted'));j.update(change)
    async def generate(system,*args):return json.dumps({} if 'explicit_steps' in system else j)
    result=await JudgeService(LLMClient(generate,config),config).judge(problem,'algorithm',[])
    assert result.verdict=='UNCERTAIN'


async def test_history_limit_not_silently_truncated(config,problem):
    config.max_history_chars=10
    async def fail(*args):raise AssertionError('must not call LLM')
    result=await JudgeService(LLMClient(fail,config),config).judge(problem,'new',[{'content':'previous long history'}])
    assert result.verdict=='UNCERTAIN'


async def test_no_unverified_counterexample(config,problem):
    j=asdict(Judgment('WA',0.99,False,False,False,counterexample='fabricated example',feedback='full correct solution leaked'))
    async def generate(system,*args):return json.dumps({} if 'explicit_steps' in system else j)
    result=await JudgeService(LLMClient(generate,config),config).judge(problem,'wrong',[])
    assert not result.counterexample and 'leaked' not in result.feedback


@pytest.mark.parametrize('s',['[]','no JSON','{"verdict":"AC"} trailing'])
def test_invalid_json(s):
    with pytest.raises(ValueError):json_object(s)


def test_compact_formula_dedup_preserves_distinct_values():
    from astrbot_plugin_kouhu_judge.translation_service import compact_protect,compact_restore
    protected,values=compact_protect(r'Given $s$, change $s$ using 0 or 1.')
    assert values==['$s$','0','1']
    assert compact_restore('给定 ⟦KH0⟧，可改为 0 或 1。',values)=='给定 $s$，可改为 0 或 1。'
    with pytest.raises(ValueError):compact_restore('给定 ⟦KH0⟧，上界 100。',values)
    with pytest.raises(ValueError):compact_restore('给定 ⟦KH9⟧。',values)


async def test_translation_repairs_on_third_attempt(tmp_path,config,problem):
    config.translate=True;payloads=[]
    async def generate(system,prompt,*args):
        data=json.loads(prompt);payloads.append(data)
        fields={k:('中文：'+v if v else '') for k,v in data['fields'].items()}
        if len(payloads)<3:fields['input_spec']='中文：丢失字段中的占位符'
        return json.dumps({'fields':fields})
    problem.input_spec='Read $n$ between 1 and 100.'
    t=TranslationService(LLMClient(generate,config),config,tmp_path)
    result=await t.translate(problem)
    assert result['_translation']=='zh' and len(payloads)==3
    assert 'input_spec' in payloads[1]['validation_feedback']
    assert '$n$' in result['input_spec']
    assert await t.translate(problem)==result and len(payloads)==3

async def test_translation_attempt_limit_no_bad_cache(tmp_path,config,problem):
    config.translate=True;config.translation_attempts=4;calls=[]
    async def fail(*args):calls.append(1);return 'invalid JSON'
    from astrbot_plugin_kouhu_judge.models import GameError
    with pytest.raises(GameError,match='4 次'):
        await TranslationService(LLMClient(fail,config),config,tmp_path).translate(problem)
    assert len(calls)==4 and not list(tmp_path.glob('*.json'))
