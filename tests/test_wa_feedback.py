import pytest
from astrbot_plugin_kouhu_judge.models import Judgment,Sample
from astrbot_plugin_kouhu_judge.wa_feedback import concise_uncertain,concise_wa,verified_formula_example

def test_specific_reason_short():
 j=Judgment('WA',logical_errors=['你把操作当成只改相邻两项，但题面要求同时对所有项取模。'])
 assert concise_wa(j,120)==j.logical_errors[0]

def test_no_correct_solution_or_unverified_counterexample():
 j=Judgment('WA',logical_errors=['正确做法是先排序再贪心'],feedback='full solution',counterexample='fabricated')
 result=concise_wa(j,120)
 assert '排序' not in result and 'fabricated' not in result

def test_total_limit():
 j=Judgment('WA',logical_errors=['某一步没有说明。'*100])
 assert len(concise_wa(j,120))<=120

def test_verified_formula_sample(problem):
 problem.input_spec='Read integer n.';problem.statement='Count arrangements.';problem.output_spec='Print answer.'
 problem.samples=[Sample('6','720')]
 assert '该式得6' in verified_formula_example(problem,'输出C(n,5)')
 assert verified_formula_example(problem,'输出A(n,5)')==''
 assert verified_formula_example(problem,'输出组合数A(n,5)')==''
 assert verified_formula_example(problem,"输出__import__('os').system('anything')") == ''
 problem.samples=[Sample('2 6','720')]
 assert verified_formula_example(problem,'输出C(n,5)')==''

@pytest.mark.parametrize('expression',['输出C(n,999999999)','输出n**99999999','输出1//0'])
def test_restricted_arithmetic(problem,expression):
 problem.input_spec='Read n.';problem.samples=[Sample('6','720')]
 assert verified_formula_example(problem,expression)==''


def test_unverified_example_removed_without_dangling_clause():
 j=Judgment('WA',logical_errors=['直接输出0与要求最大可能值不符，题面样例1答案是4。'])
 assert concise_wa(j,120)=='直接输出0与要求最大可能值不符'


def test_example_clause_and_long_sentence_do_not_leave_fragments():
    judgment=Judgment('WA',logical_errors=['公式给出的边数偏小，但题面样例中答案更大。'])
    assert concise_wa(judgment,120)=='公式给出的边数偏小'
    judgment.logical_errors=['公式给出的边数偏小。'+'附加内容'*100]
    assert concise_wa(judgment,60)=='公式给出的边数偏小。'


def test_uncertain_keeps_specific_question():
    question='你统计区间外原本命中的箱子时，后缀从哪个位置开始？'
    assert concise_uncertain(Judgment(missing_key_points=[question]),120)==question


async def test_missing_detail_alone_cannot_be_wa(config,problem):
    from dataclasses import asdict
    from astrbot_plugin_kouhu_judge.judge_service import JudgeService

    question='后缀从窗口的哪个端点开始统计？'

    class Model:
        async def call(self,*args,**kwargs):
            return asdict(Judgment('WA',0.95,missing_key_points=[question]))

    config.judge_two_stage=False
    result=await JudgeService(Model(),config).judge(problem,'加上后缀命中数量',[])
    assert (result.verdict,result.feedback)==('UNCERTAIN',question)


@pytest.mark.parametrize('rounding,actual',[('floor',2),('ceil',4)])
async def test_level_generation_uses_checked_batch_sample(config,problem,rounding,actual):
    from astrbot_plugin_kouhu_judge.judge_service import JudgeService

    class NoModel:
        async def call(self,*args,**kwargs):
            pytest.fail('已核验的样例错误无需调用模型')

    problem.problem_id='cf:818F'
    problem.input_spec='Then q lines follow, i-th line contains one positive integer ni (1 ≤ ni ≤ 2·109).'
    problem.samples=[Sample('3\n3\n4\n6','2\n3\n6')]
    assert r'2\cdot 10^{9}' in problem.public()['input_spec']
    result=await JudgeService(NoModel(),config).judge(
        problem,f'取x={rounding}（n+0.5-0.5sqrt（8n+1）），答案为2x',[])
    assert result.verdict=='WA'
    assert result.feedback==f'样例1：n=4，该式得{actual}，正确输出3。'
