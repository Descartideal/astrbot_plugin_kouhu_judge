import asyncio
import random
import re
import time
from .providers.cf_archive import REVISION


class OfflineDownloader:
    """仅下载空闲时段；数据/游标/失败退避持久化，整个模块没有模型客户端。"""
    def __init__(self,db,config,cf,archive,logger=None):
        self.db,self.c,self.cf,self.archive,self.logger=db,config,cf,archive,logger
        self.last_activity=time.monotonic();self.lock=asyncio.Lock()

    def mark_activity(self):self.last_activity=time.monotonic()

    def idle(self):return time.monotonic()-self.last_activity>=self.c.idle_seconds

    async def run(self):
        while True:
            await asyncio.sleep(min(15,self.c.download_interval))
            if not self.c.background_download or not self.idle():continue
            workers=[('cf',self)]
            if self.c.background_at_download and hasattr(self,'at_downloader'):
                workers.append(('at',self.at_downloader))
            for platform,worker in workers:
                if not self.idle():break
                backoff='at_download_backoff_until' if platform=='at' else 'download_backoff_until'
                if time.time()<(await self.db.state(backoff) or 0):continue
                try:
                    await worker.harvest_page()
                except asyncio.CancelledError:raise
                except Exception as e:
                    if getattr(e,'status_code',None)==429:
                        await self.db.state(backoff,time.time()+e.retry_after)
                    await self.db.state('at_download_last_error' if platform=='at' else 'download_last_error',{'kind':type(e).__name__,'at':time.time()})
                    if self.logger:self.logger.warning('口胡离线题库同步暂不可用，平台='+platform+'，错误类型：'+type(e).__name__)
            await asyncio.sleep(self.c.download_interval)

    async def harvest_page(self,force=False,low=None,high=None,official=True):
        async with self.lock:
            if not force and not self.idle():return 0,0
            if time.time()<(await self.db.state('download_backoff_until') or 0):return 0,0
            low=self.c.download_low if low is None else low;high=self.c.download_high if high is None else high
            metas=await self.cf.metadata()
            mapping={str(m['contestId'])+m['index']:m for m in metas}
            loaded=skipped=0
            unavailable=await self.db.state('cf_html_unavailable_until') or 0
            if official and time.time()>unavailable:
                choices=[m for m in metas if low<=m['rating']<=high and 'interactive' not in m.get('tags',[])]
                random.shuffle(choices)
                for m in choices:
                    pid='cf:'+str(m['contestId'])+m['index']
                    if not await self.db.download_ready(pid):continue
                    if not force and not self.idle():return loaded,skipped
                    try:
                        p=await self.cf.fetch_problem(m)
                        await self.db.add_problem(p);await self.db.record_download(pid,True,source='official');loaded+=1
                        break
                    except asyncio.CancelledError:raise
                    except Exception as e:
                        await self.db.record_download(pid,False,type(e).__name__,'official')
                        await self.db.state('cf_html_unavailable_until',time.time()+6*3600)
                        break  # 正文站不可用时不逐题轰炸；使用公开归档后备。
            if not self.c.archive_fallback:return loaded,skipped
            key='cf_archive_cursor:'+REVISION
            cursor=await self.db.state(key) or {'offset':0,'cursor':0,'length':self.c.download_batch_size}
            if not force and not self.idle():return loaded,skipped
            try:page=await self.archive.page(cursor['offset'],cursor['length'])
            except Exception as e:
                if getattr(e,'status_code',None)==429:await self.db.state('download_backoff_until',time.time()+e.retry_after)
                raise
            rows=page['rows']
            for pos in range(cursor['cursor'],len(rows)):
                if not force and not self.idle():break
                item=rows[pos];row=item['row'];match=re.fullmatch(r'(\d+)/([A-Z][0-9]?)',str(row.get('id','')))
                if match:
                    short=match[1]+match[2];pid='cf:'+short;meta=mapping.get(short)
                    if meta and low<=meta.get('rating',0)<=high and await self.db.download_ready(pid,'archive'):
                        try:
                            p=self.archive.convert(item,mapping)
                            await self.db.add_problem(p);await self.db.record_download(pid,True,source='archive');loaded+=1
                        except (ValueError,KeyError,TypeError) as e:
                            await self.db.record_download(pid,False,type(e).__name__,'archive');skipped+=1
                cursor['cursor']=pos+1
                await self.db.state(key,cursor)
            if cursor['cursor']>=len(rows):
                offset=cursor['offset']+len(rows)
                await self.db.state(key,{'offset':0 if offset>=page['total'] else offset,'cursor':0,'length':self.c.download_batch_size})
            await self.db.state('download_last_success',{'at':time.time(),'loaded':loaded,'skipped':skipped})
            return loaded,skipped
