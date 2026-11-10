"""公开的 Open R1 原作者题解归档。只取 editorial，不读取生成的解答/测试器。"""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode
from ..models import Problem,Sample,RenderContentError
from ..cache import atomic_write
from .common import FetchError

DATASET='open-r1/codeforces-cots'
SUBSET='solutions_w_editorials'
REVISION='39ac85c150806230473c70ad72c31f6232fe3f41'
SOURCE='https://huggingface.co/datasets/open-r1/codeforces-cots'
FIELDS={'id','aliases','contest_id','index','title','description','input_format','output_format','examples','editorial','rating','note','time_limit','memory_limit','interaction_format'}
CRITICAL={'id','title','description','input_format','output_format','examples','editorial','rating','note'}


class CFArchiveProvider:
    def __init__(self,fetcher,cache_dir):
        self.fetcher=fetcher;self.dir=Path(cache_dir);self.dir.mkdir(parents=True,exist_ok=True)
        self.revision_checked=False

    async def page(self,offset,length=20):
        import asyncio
        path=self.dir/f'{REVISION}-{int(offset)}-{int(length)}.json'
        if path.exists():return json.loads(await asyncio.to_thread(path.read_text,encoding='utf-8'))
        if not self.revision_checked:
            current=await self.fetcher.get('https://huggingface.co/api/datasets/'+DATASET,True)
            if current.get('sha')!=REVISION:raise FetchError('归档版本已改变，需维护者核验新版本；继续使用已有离线题库')
            self.revision_checked=True
        url='https://datasets-server.huggingface.co/rows?'+urlencode({'dataset':DATASET,'config':SUBSET,'split':'train','offset':offset,'length':length})
        response=await self.fetcher.get(url,True)
        # 不保留/加载模型生成的 generation/checker 或巨量测试数据到持久化缓存。
        data={'total':int(response['num_rows_total']),'revision':REVISION,'rows':[]}
        for item in response['rows']:
            data['rows'].append({'row_idx':item['row_idx'],'truncated_cells':item.get('truncated_cells',[]),
                                 'row':{k:v for k,v in item['row'].items() if k in FIELDS}})
        await asyncio.to_thread(atomic_write,path,json.dumps(data,ensure_ascii=False))
        return data

    def convert(self,item,metadata):
        row=item['row']
        if set(item.get('truncated_cells',[]))&CRITICAL:raise ValueError('归档关键字段被截断')
        match=re.fullmatch(r'(\d+)/([A-Z][0-9]?)',str(row.get('id','')))
        if not match:raise ValueError('非法 CF ID')
        cid,index=match.groups();key=cid+index
        meta=metadata.get(key)
        if not meta or not isinstance(meta.get('rating'),int):raise ValueError('没有官方难度快照')
        if not row.get('editorial') or len(row['editorial'].strip())<80:raise ValueError('没有原作者题解')
        if row.get('interaction_format') or 'interactive' in meta.get('tags',[]):raise ValueError('交互题暂不支持')
        if not row.get('description') or not row.get('input_format') or not row.get('output_format') or not row.get('examples'):
            raise ValueError('题面不完整')
        title=lambda s:re.sub(r'\W+','',s).casefold()
        if title(row['title'])!=title(meta['name']):raise ValueError('归档标题与官方元数据不一致')
        # 不隐去未本地化图示，不将缺图题伪装为完整文本题。
        if re.search(r'<img\b|!\[[^]]*\]\(|https?://[^\s]+\.(?:png|jpg|gif|svg)',row['description']+'\n'+row['editorial'],re.I):
            raise ValueError('归档依赖未支持的图示')
        editorial=row['editorial'];url=f'https://codeforces.com/problemset/problem/{cid}/{index}'
        archive_url=f'{SOURCE}/viewer/{SUBSET}/train?row={item["row_idx"]}'
        evidence={'method':'publisher-official-editorial-archive','publisher':'Open R1 / Hugging Face','dataset':DATASET,
                  'revision':REVISION,'source':archive_url,'row_idx':item['row_idx'],'official_problem_url':url,
                  'official_directory_url':f'https://codeforces.com/contest/{cid}',
                  'official_editorial_direct_url_verified':False,'checked_at':time.time(),
                  'sha256':hashlib.sha256(editorial.encode()).hexdigest()}
        if any(not isinstance(s.get('input'),str) or not isinstance(s.get('output'),str) for s in row['examples']):raise ValueError('样例值非法')
        samples=[Sample(s['input'],s['output'],(row.get('note') or '') if n==0 else '') for n,s in enumerate(row['examples'])]
        p=Problem('cf:'+key,'cf',meta['name'],url,cid,meta['rating'],meta['rating'],row['description'],row['input_format'],row['output_format'],
                       samples,editorial,archive_url,time_limit=str(row.get('time_limit') or ''),memory_limit=str(row.get('memory_limit') or ''),
                       updated_at=time.time(),verified=True,verification=evidence)

        try:p.public()  # Invalid source must be skipped, not stall the download cursor.
        except RenderContentError as e:raise ValueError(str(e)) from None
        return p
