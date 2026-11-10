import logging
from dataclasses import fields
from pathlib import Path
from .models import Judgment,RenderContentError
from .llm import LLMFailure
from .cache import digest
from .commands import clean_message
from .wa_feedback import concise_uncertain,concise_wa,verified_formula_example

PROMPTS=Path(__file__).parent/'prompts'


class JudgeService:
    def __init__(self, llm, config):
        self.llm,self.c=llm,config
        self.fingerprint=digest(['payload-v5-verified-formula',config.api_base_url,config.api_model,config.judge_two_stage,config.ac_confidence,config.wa_feedback_chars,
                                 (PROMPTS/'judge.txt').read_text('utf-8'),(PROMPTS/'extract.txt').read_text('utf-8')])[:12]

    async def judge(self, problem, content, history, umo='', *, heavy=False):
        if sum(len(x['content']) for x in history)+len(content)>self.c.max_history_chars:
            return Judgment(feedback='本题提交历史过长，无法完整核对；请放弃后重新开始练习。')
        # 只发送完整题意、必要历史和当前解法；不发送昵称、头像、聊天、路由或缓存元数据。
        try:public=problem.public()
        except RenderContentError as e:return Judgment(feedback=f'{e} 本次暂不判题、不计分。')
        example=verified_formula_example(problem,content)
        if example and not heavy:
            return Judgment('WA',1,counterexample=example,feedback=example)
        compact={k:public[k] for k in ('problem_id','statement','constraints','input_spec','output_spec','samples','time_limit','memory_limit')}
        prior=[{'content':clean_message(x['content'])} for x in history]
        common={'problem':compact,'history':prior,'current_submission':content}
        try:
            extraction={}
            if self.c.judge_two_stage:
                extraction=await self.llm.call((PROMPTS/'extract.txt').read_text('utf-8'),common,provider=self.c.judge_provider,umo=umo)
            options={'reasoning_effort':'low'} if heavy else {}
            result=await self.llm.call((PROMPTS/'judge.txt').read_text('utf-8'),dict(common,extraction=extraction,
                 official_editorial=problem.official_editorial,core_difficulties=problem.core_difficulties,complexity=problem.complexity),
                 provider=self.c.judge_provider,umo=umo,**options)
            if example:
                return Judgment('WA',1,counterexample=example,feedback=example)
            required={f.name for f in fields(Judgment)}
            if set(result)!=required:raise ValueError('判题字段不完整')
            if result['verdict'] not in ('AC','WA','UNCERTAIN'):raise ValueError('invalid verdict')
            for key in ('main_idea_correct','core_difficulty_covered','complexity_valid'):
                if type(result[key]) is not bool:raise ValueError('invalid boolean')
            for key in ('missing_key_points','logical_errors'):
                if not isinstance(result[key],list) or any(not isinstance(x,str) for x in result[key]):raise ValueError('invalid list')
            if type(result['confidence']) not in (int,float) or not 0<=result['confidence']<=1:raise ValueError('invalid confidence')
            if not isinstance(result['feedback'],str) or not isinstance(result['counterexample'],str):raise ValueError('invalid text')
            j=Judgment(**result)
            # 模型生成的反例没有可信验证器，绝不向玩家当作已证实反例发送。
            j.counterexample=''
            if j.verdict=='AC' and (j.confidence<self.c.ac_confidence or not all((j.main_idea_correct,j.core_difficulty_covered,j.complexity_valid))):
                return Judgment(feedback=concise_uncertain(j,self.c.wa_feedback_chars))
            if j.verdict=='WA' and not j.logical_errors and j.missing_key_points:
                return Judgment(feedback=concise_uncertain(j,self.c.wa_feedback_chars))
            if j.verdict!='AC':
                # 外发采用保守模板，避免自由文本把官方核心观察或未经验证反例泄漏给玩家。
                if j.verdict=='WA':
                    j.feedback=concise_wa(j,self.c.wa_feedback_chars)
                else:j.feedback=concise_uncertain(j,self.c.wa_feedback_chars)
            return j
        except (LLMFailure,ValueError,TypeError) as e:
            logging.getLogger(__name__).warning('Kouhu judgment failed type=%s cause=%s',type(e).__name__,type(e.__cause__).__name__ if e.__cause__ else 'none')
            if heavy:
                return Judgment(feedback='轻度思考复判失败，请检查独立判题 API 和模型是否支持 low 思考。')
            return Judgment(feedback='判题服务暂时不可用或返回格式异常，本次不计分，请稍后重新 submit。')
