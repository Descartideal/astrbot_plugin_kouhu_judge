import re
import time
from bs4 import BeautifulSoup
from .common import text,section_text,FetchError
from ..models import Problem,Sample
from ..difficulty import atcoder_to_cf


class AtCoderProvider:
    def __init__(self, fetcher, editorials, config):self.fetcher,self.editorials,self.config=fetcher,editorials,config

    async def metadata(self):
        tasks=await self.fetcher.get('https://kenkoooo.com/atcoder/resources/problems.json',True)
        models=await self.fetcher.get('https://kenkoooo.com/atcoder/resources/problem-models.json',True)
        result={}
        for t in tasks:
            d=models.get(t['id'],{}).get('difficulty')
            nr=atcoder_to_cf(d,self.config)
            if nr is not None:
                existing=result.get(t['id'])
                if existing is None or t['contest_id']==t['id'].rsplit('_',1)[0]:
                    result[t['id']]=dict(t,difficulty=d,rating=nr)
        return list(result.values())

    async def fetch_problem(self, meta):
        cid,tid=meta['contest_id'],meta['id']
        url=f'https://atcoder.jp/contests/{cid}/tasks/{tid}'
        soup=BeautifulSoup(await self.fetcher.get(url),'html.parser')
        lang=soup.select_one('#task-statement .lang-en') or soup.select_one('#task-statement .lang-ja')
        if lang is None or lang.find('img'):raise FetchError('题面不存在或含未支持的图示')
        parts={}; inputs={};outputs={};description=[]
        for sec in lang.select('section'):
            heading=sec.find(['h3','h2'])
            if heading is None:continue
            label=heading.get_text(' ',strip=True)
            heading.extract()
            value=section_text(sec)
            m=re.search(r'(?:Sample Input|入力例)\s*(\d+)',label)
            n=re.search(r'(?:Sample Output|出力例)\s*(\d+)',label)
            if m:inputs[int(m[1])]=text(sec.find('pre'))
            elif n:
                pre=sec.find('pre');outputs[int(n[1])]=(text(pre),'')
                if pre:pre.extract()
                outputs[int(n[1])]=(outputs[int(n[1])][0],section_text(sec))
            elif label in ('Input','入力'):parts['input_spec']=value
            elif label in ('Output','出力'):parts['output_spec']=value
            elif label in ('Constraints','制約'):parts['constraints']=value
            else:description.append(value)
        if not inputs or set(inputs)!=set(outputs) or not parts.get('input_spec') or not parts.get('output_spec'):
            raise FetchError('AtCoder 结构化题面解析失败')
        samples=[Sample(inputs[k],*outputs[k]) for k in sorted(inputs)]
        editorial,eurl,verification=await self.editorials.fetch('at',f'https://atcoder.jp/contests/{cid}/editorial',tid)
        verification['statement_language']='en' if 'lang-en' in lang.get('class',[]) else 'ja'
        limits=text(soup.select_one('#main-container .row .col-sm-12 > p'))
        time_limit=limits;memory_limit=''
        limit_match=re.search(r'Time Limit:\s*(.*?)\s*/\s*Memory Limit:\s*(.*)',limits)
        if limit_match:time_limit,memory_limit=limit_match[1],limit_match[2]
        heading=soup.select_one('#main-container span.h2')
        if heading:
            for a in heading.find_all('a'):a.decompose()
        title=heading.get_text(' ',strip=True) if heading else meta.get('title',meta.get('name',tid))
        return Problem(f'at:{tid}','at',title,url,cid,meta['difficulty'],meta['rating'],
         '\n\n'.join(description),parts['input_spec'],parts['output_spec'],samples,editorial,eurl,
         constraints=parts.get('constraints',''),time_limit=time_limit,memory_limit=memory_limit,updated_at=time.time(),verified=True,verification=verification)
