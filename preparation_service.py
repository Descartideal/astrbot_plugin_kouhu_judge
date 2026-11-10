"""One durable, fully rendered next problem per enabled group. No game or score writes."""
import asyncio
import json
import logging
import uuid
from .cache import digest
from .commands import Command
from .models import Identity,Problem,GameError,RenderContentError
from .renderer import STATEMENT_RENDER_SCHEMA,valid_image


class PreparationService:
    def __init__(self,db,config,problems,renderer,image_validator=valid_image):
        self.db,self.c,self.problems,self.renderer=db,config,problems,renderer
        self.valid=image_validator;self.locks={};self.tasks={};self.worker=None;self.closing=False

    @staticmethod
    def content_key(problem):
        data=json.loads(problem.dump())
        for key in ('updated_at','verification'):data.pop(key,None)
        return digest(data)

    async def signature(self,who):
        defaults=await self.problems.defaults(who)
        return digest([STATEMENT_RENDER_SCHEMA,defaults,self.c.render_version,self.c.translation_version,self.c.translate,
                       self.c.judge_only,self.c.compact_layout,self.c.font_path,self.c.image_page_height,self.c.dedup])

    async def ensure(self,who,umo=''):
        async with self.locks.setdefault(who.room_id,asyncio.Lock()):
            if self.closing:raise GameError('插件正在重载，请稍后重试。')
            await self.db.ensure_room(who)
            for _ in range(5):
                signature=await self.signature(who)
                available=await self.problems.preparation_candidates(who)
                by_id={p.problem_id:p for p in available}
                ids=set(by_id)
                old=await self.db.prepared(who.room_id)
                if old and old['signature']==signature and old['problem_id'] in ids and self.content_key(Problem.load(old['snapshot']))==self.content_key(by_id[old['problem_id']]):
                    try:images=json.loads(old['images'])
                    except (ValueError,TypeError):images=[]
                    if images and all(self.valid(f) for f in images):return old
                await self.db.discard_prepared(who.room_id)
                if not available:raise GameError('这个范围内没有还没通过的题。可以放宽难度或换一个平台。')
                problem=self.problems.rng.choice(available)
                try:images=await self.renderer.render(problem,umo)
                except RenderContentError:
                    await self.db.render_status(problem.problem_id,False);continue
                if not images or not all(self.valid(f) for f in images):raise GameError('预备题图片尚未完整生成，请稍后重试。')
                if self.closing:raise GameError('插件正在重载，请稍后重试。')
                if signature!=await self.signature(who):continue
                if problem.problem_id not in {p.problem_id for p in await self.problems.preparation_candidates(who)}:continue
                record={'room_id':who.room_id,'token':uuid.uuid4().hex,'signature':signature,
                        'problem_id':problem.problem_id,'snapshot':problem.dump(),'images':json.dumps(images)}
                if await self.db.save_prepared(record):return record
            raise GameError('预备题仍在更新，请稍后重试 new。')

    async def consume(self,who,umo=''):
        for _ in range(3):
            ready=await self.ensure(who,umo)
            async with self.locks.setdefault(who.room_id,asyncio.Lock()):
                if self.closing:raise GameError('插件正在重载，请稍后重试。')
                current=await self.db.prepared(who.room_id)
                if not current or current['token']!=ready['token'] or ready['signature']!=await self.signature(who):continue
                await self.db.start_round(who,ready['problem_id'],prepared_token=ready['token'])
                return ready
        raise GameError('预备题已更新，请重试 new。')

    def schedule(self,who,umo=''):
        if self.closing or not self.c.group_prefetch or not who.group_id or not self.c.allowed(who):return
        existing=self.tasks.get(who.room_id)
        if existing and not existing.done():return
        async def run():
            try:await self.ensure(who,umo)
            except asyncio.CancelledError:raise
            except Exception as e:logging.getLogger(__name__).warning('Kouhu next problem preparation failed type=%s',type(e).__name__)
        self.tasks[who.room_id]=asyncio.create_task(run())

    async def invalidate(self,who,umo=''):
        task=self.tasks.get(who.room_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        await self.db.discard_prepared(who.room_id)
        self.schedule(who,umo)

    def start(self):
        if self.worker or not self.c.group_prefetch:return
        async def run():
            while True:
                for room in self.c.enabled_groups:
                    try:platform,group=room.rsplit(':GroupMessage:',1)
                    except (ValueError,AttributeError):continue
                    if not platform or not group:continue
                    who=Identity(platform,'',group)
                    self.schedule(who,room)
                await asyncio.sleep(self.c.prefetch_interval)
        self.worker=asyncio.create_task(run())

    async def close(self):
        self.closing=True
        tasks=[t for t in [self.worker,*self.tasks.values()] if t and not t.done()]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self.worker=None;self.tasks.clear()
