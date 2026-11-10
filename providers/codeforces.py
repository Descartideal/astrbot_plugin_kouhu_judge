import time
import asyncio
import json
from pathlib import Path
from ..cache import atomic_write
from bs4 import BeautifulSoup
from .common import text,section_text,FetchError
from ..models import Problem, Sample


class CodeforcesProvider:
    def __init__(self, fetcher, editorials,cache_dir=None,config=None):
        self.fetcher,self.editorials=fetcher,editorials;self.cache_dir=Path(cache_dir) if cache_dir else None;self.config=config

    async def metadata(self):
        path=self.cache_dir/'cf-metadata.json' if self.cache_dir else None
        cached=None
        if path and path.exists():
            try:cached=json.loads(await asyncio.to_thread(path.read_text,encoding='utf-8'))
            except (OSError,ValueError):pass
        if cached and cached.get('schema')==2 and time.time()-cached['updated_at']<self.config.metadata_ttl_hours*3600:return cached['problems']
        try:
            result=await self.fetcher.get('https://codeforces.com/api/problemset.problems',True)
            if result.get('status')!='OK':raise FetchError('CF API 失效')
            rows=[dict(p,rating=p.get('rating') if isinstance(p.get('rating'),int) else 0) for p in result['result']['problems'] if p.get('contestId')]
            if path:await asyncio.to_thread(atomic_write,path,json.dumps({'schema':2,'updated_at':time.time(),'problems':rows},ensure_ascii=False))
            return rows
        except (FetchError,KeyError,ValueError):
            if cached:return cached['problems']
            raise

    async def fetch_problem(self, meta):
        cid,index=str(meta['contestId']),meta['index']
        url=f'https://codeforces.com/problemset/problem/{cid}/{index}'
        soup=BeautifulSoup(await self.fetcher.get(url),'html.parser')
        body=soup.select_one('.problem-statement')
        if body is None:raise FetchError('CF 题面不存在')
        if body.find('img'):raise FetchError('此题包含尚未本地化的图示，跳过')
        description=[]
        for child in body.find_all(recursive=False):
            if not set(child.get('class',[])) & {'header','input-specification','output-specification','sample-tests','note'}:
                description.append(section_text(child))
        ins=body.select('.sample-test .input pre'); outs=body.select('.sample-test .output pre')
        if not ins or len(ins)!=len(outs):raise FetchError('样例不完整')
        note=section_text(body.select_one('.note'))
        samples=[Sample(text(i),text(o),note if n==0 else '') for n,(i,o) in enumerate(zip(ins,outs))]
        editorial,eurl,verification=await self.editorials.fetch('cf',f'https://codeforces.com/contest/{cid}',index)
        problem=Problem(f'cf:{cid}{index}','cf',meta['name'],url,cid,meta['rating'],meta['rating'],
          '\n\n'.join(description),section_text(body.select_one('.input-specification')),section_text(body.select_one('.output-specification')),
          samples,editorial,eurl,time_limit=text(body.select_one('.time-limit')),memory_limit=text(body.select_one('.memory-limit')),
          updated_at=time.time(),verified=True,verification=verification)
        problem.public()
        return problem
