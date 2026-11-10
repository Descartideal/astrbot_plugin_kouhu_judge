import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from .cache import digest,atomic_write
from .judge_service import PROMPTS
from .models import GameError
from .statement_layout import FENCED_CODE

TOKEN=re.compile(FENCED_CODE.pattern+r'|\$\$\$[\s\S]+?\$\$\$|\$\$[\s\S]+?\$\$|(?<!\\)\$[^$\n]+\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]|```[\s\S]*?```|`[^`]+`|\b\d+(?:\.\d+)?\b')


def protect(s):
    values=[]
    def replace(m):
        values.append(m[0]);return f'⟦KH{len(values)-1}⟧'
    return TOKEN.sub(replace,s),values


def restore(s, values):
    if Counter(re.findall(r'⟦KH\d+⟧',s))!=Counter(f'⟦KH{i}⟧' for i in range(len(values))):raise ValueError('翻译丢失公式或数值')
    return re.sub(r'⟦KH(\d+)⟧',lambda m:values[int(m[1])],s)


def compact_protect(s):
    # Identical formulas share an ID: concise Chinese need not repeat the same
    # variable for every pronoun, while every distinct formula remains protected.
    values=[]
    def replace(m):
        if m[0] not in values:values.append(m[0])
        return f'⟦KH{values.index(m[0])}⟧'
    return TOKEN.sub(replace,s),values


def compact_restore(s,values):
    ids={int(i) for i in re.findall(r'⟦KH(\d+)⟧',s)}
    if any(i>=len(values) for i in ids):raise ValueError('未知公式占位符')
    result=re.sub(r'⟦KH(\d+)⟧',lambda m:values[int(m[1])],s)
    for i,value in enumerate(values):
        # A model may write a protected literal number directly. Check its
        # exact numeric token, never accept 2 because it appears inside 200000.
        if i not in ids:
            if not re.fullmatch(r'\d+(?:\.\d+)?',value) or not re.search(r'(?<![\d.])'+re.escape(value)+r'(?![\d.])',result):
                raise ValueError('精简翻译丢失公式或数值')
    return result


class TranslationService:
    def __init__(self, llm, config, cache_dir):
        self.llm,self.c,self.cache_dir=llm,config,Path(cache_dir);self.locks={}

    async def translate(self, problem, umo='', refresh=False):
        public=problem.public()
        source={k:public[k] for k in ('title','statement','constraints','input_spec','output_spec')}
        source.update({f'explanation_{i}':s['explanation'] for i,s in enumerate(public['samples'])})
        cache_key=[problem.problem_id,public,self.c.translation_version]
        if any(FENCED_CODE.search(value or '') for value in source.values()):
            cache_key.append('preserve-code-v1')
        key=digest(cache_key)
        async with self.locks.setdefault(key,asyncio.Lock()):
            path=self.cache_dir/(key+'.json')
            if path.exists() and not refresh:
                try:return json.loads(await asyncio.to_thread(path.read_text,encoding='utf-8'))
                except (ValueError,OSError):pass
            if not self.c.translate:
                public['_translation']='original'
                return public
            # Standalone illustrative paragraphs are redundant with the unchanged
            # sample page. Never remove definitions, operations or constraints.
            if not FENCED_CODE.search(source['statement']):
                parts=source['statement'].split('\n\n')
                source['statement']='\n\n'.join(p for p in parts if not re.match(r'^\s*(?:For example|For instance)[,:]',p,re.I))
            protected={};values={}
            for k,v in source.items():protected[k],values[k]=compact_protect(v or '')
            feedback='';previous=None
            attempts=self.c.translation_attempts
            for attempt in range(attempts):
                public=problem.public()
                try:
                    payload={'fields':protected}
                    if feedback:
                        payload['validation_feedback']=feedback
                        if isinstance(previous,dict):payload['previous_attempt']=previous
                    answer=await self.llm.call((PROMPTS/'translate.txt').read_text('utf-8'),payload,provider=self.c.translation_provider,umo=umo)
                    previous=answer
                    fs=answer['fields']
                    if set(fs)!=set(source) or any(not isinstance(v,str) for v in fs.values()):raise ValueError('翻译字段不匹配，须原样保留所有字段名')
                    for k,v in fs.items():
                        try:translated=compact_restore(v,values[k])
                        except ValueError:
                            required=' '.join(f'⟦KH{i}⟧' for i in range(len(values[k])))
                            raise ValueError(f'字段 {k} 缺失或改写保护项；本字段必须包含这些占位符：{required}') from None
                        if FENCED_CODE.findall(source[k] or '')!=FENCED_CODE.findall(translated):
                            raise ValueError(f'字段 {k} 须按原顺序保留每个代码块一次。')
                        prose=re.sub(r'⟦KH\d+⟧','',protected[k])
                        if len(re.findall(r'[A-Za-z]',prose))>5 and not re.search(r'[\u4e00-\u9fff]',v):raise ValueError(f'字段 {k} 未译成中文')
                        if (source[k] or '').strip() and not translated.strip():raise ValueError(f'字段 {k} 翻译为空')
                        if k.startswith('explanation_'):public['samples'][int(k.split('_')[1])]['explanation']=translated
                        else:public[k]=translated
                    public['_translation']='zh';public['_version']=self.c.translation_version
                    await asyncio.to_thread(atomic_write,path,json.dumps(public,ensure_ascii=False))
                    return public
                except asyncio.CancelledError:raise
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning('Kouhu translation problem=%s attempt=%s/%s failure=%s',problem.problem_id,attempt+1,attempts,type(e).__name__)
                    # Only locally generated validation errors are sent back. Never
                    # include provider exception text, request objects or secrets.
                    feedback=str(e) if type(e) is ValueError else '上次请求失败或 JSON 格式不合法，请重新输出完整合法 JSON。'
                    if attempt+1<attempts:await asyncio.sleep(min(2,0.5*(attempt+1)))
            raise GameError(f'中文题面生成失败，已尝试 {attempts} 次；请重新 new 重试（已有活动题用 cur），或检查翻译模型配置。') from None
