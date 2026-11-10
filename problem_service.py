import asyncio
import json
import math
import random
import time
from pathlib import Path
from .models import Problem,GameError
from .commands import Command
from .editorial_service import EditorialService

EMPTY='这个范围内没有还没通过的题。可以放宽难度或换一个平台。'


class ProblemService:
    def __init__(self, db, config, providers=None, rng=None):
        self.db,self.c,self.providers=db,config,providers or {}
        self.rng=rng or random.Random();self.sync_lock=asyncio.Lock()

    async def import_json(self, path):
        data=await asyncio.to_thread(Path(path).read_text,encoding='utf-8')
        rows=json.loads(data)
        if not isinstance(rows,list):raise GameError('题库根节点须为数组。')
        validated=[]
        for row in rows:
            p=EditorialService.validate_local(Problem.load(row))
            if p.platform not in ('cf','at') or p.normalized_rating<0:raise GameError('题目平台或难度非法。')
            validated.append(p)
        for p in validated:await self.db.add_problem(p)
        return len(validated)

    async def sync(self, platform, low, high, limit=15):
        if platform not in self.providers:raise GameError('平台应为 cf 或 at。')
        if platform=='cf' and hasattr(self,'downloader'):
            total=skipped=0
            for _ in range(max(1,min(10,(limit+4)//5))):
                added,failed=await self.downloader.harvest_page(force=True,low=low,high=high)
                total+=added;skipped+=failed
                if total>=limit:break
            return total,skipped
        async with self.sync_lock:
            provider=self.providers[platform]
            metas=await provider.metadata()
            metas=[m for m in metas if low<=m['rating']<=high]
            self.rng.shuffle(metas);loaded=skipped=0
            for m in metas[:max(1,min(limit,50))]:
                pid=f"{platform}:"+(str(m['contestId'])+m['index'] if platform=='cf' else m['id'])
                if not await self.db.download_ready(pid):continue
                try:
                    p=await provider.fetch_problem(m)
                    if not p.verified:raise ValueError('题解尚未验证')
                    await self.db.add_problem(p);loaded+=1
                    await self.db.record_download(pid,True,source='official')
                except Exception as e:
                    # 按题隔离失败，不把不完整题目写入可抽题池。
                    skipped+=1
                    await self.db.record_download(pid,False,type(e).__name__,'official')
                await asyncio.sleep(0.4)
            return loaded,skipped

    async def choose(self, who, command):
        default_platform,low,high=await self.defaults(who)
        if command.low is None and self.c.difficulty_mode=='fixed':
            command=Command(command.name,command.argument,command.platform or default_platform,low,high)
        elif not command.platform:
            command=Command(command.name,command.argument,default_platform,command.low,command.high)
        now=time.time()
        def read(c):
            candidates=[Problem.load(r[0]) for r in c.execute("SELECT payload FROM problems WHERE cache_status='ready' AND normalized_rating>=0")]
            passed={r[0] for r in c.execute('SELECT problem_id FROM solves WHERE platform_id=? AND user_id=? AND scored=1',(who.platform_id,who.user_id))}
            completed={r[0] for r in c.execute('SELECT problem_id FROM room_rounds WHERE room_id=? AND first_blood_user_id IS NOT NULL',(who.room_id,))}
            recent={r[0] for r in c.execute('SELECT problem_id FROM room_rounds WHERE room_id=? ORDER BY round_id DESC LIMIT 10',(who.room_id,))}
            active=[dict(r) for r in c.execute('''SELECT DISTINCT u.* FROM users u JOIN activity_events a ON a.user_id=u.user_id AND a.platform_id=u.platform_id
             WHERE a.room_id=? AND a.timestamp>=?''',(who.room_id,now-self.c.activity_hours*3600))]
            me=dict(c.execute('SELECT * FROM users WHERE platform_id=? AND user_id=?',(who.platform_id,who.user_id)).fetchone())
            return candidates,passed,completed,recent,active,me
        candidates,passed,completed,recent,active,me=await self.db.run(read)
        excluded=set()
        if self.c.dedup in ('user_and_room','user'):excluded|=passed
        if self.c.dedup in ('user_and_room','room'):excluded|=completed
        candidates=[p for p in candidates if p.problem_id not in excluded and (not command.platform or p.platform==command.platform)]
        if command.low is not None:
            candidates=[p for p in candidates if command.low<=p.normalized_rating<=command.high]
            weights=[self.c.recent_penalty if p.problem_id in recent else 1 for p in candidates]
        else:
            representative=me
            if who.group_id and active:
                weights=[(self.c.zero_weight+self.c.solve_weight*math.log1p(u['scored_solve_count']))*(self.c.initiator_bonus if u['user_id']==who.user_id else 1) for u in active]
                representative=self.rng.choices(active,weights=weights)[0]
            target=max(0,representative['rating']+self.rng.expovariate(1/self.c.difficulty_scale)-self.c.difficulty_scale*0.3)
            # 稀疏池使用平滑距离权重，不要求缓存恰好存在抽样难度。
            weights=[math.exp(-min(700,abs(p.normalized_rating-target)/max(1,self.c.difficulty_window)))*(self.c.recent_penalty if p.problem_id in recent else 1) for p in candidates]
        if not candidates:raise GameError(EMPTY)
        return self.rng.choices(candidates,weights=weights)[0]

    async def preparation_candidates(self,who):
        platform,lo,hi=await self.defaults(who)
        def read(c):
            rows=c.execute("SELECT payload FROM problems WHERE cache_status='ready' AND platform=? AND normalized_rating BETWEEN ? AND ?",(platform,lo,hi)).fetchall()
            latest=c.execute('SELECT problem_id FROM room_rounds WHERE room_id=? ORDER BY round_id DESC LIMIT 1',(who.room_id,)).fetchone()
            excluded={latest[0]} if latest else set()
            if self.c.dedup in ('user_and_room','room'):
                excluded.update(r[0] for r in c.execute('SELECT problem_id FROM room_rounds WHERE room_id=? AND first_blood_user_id IS NOT NULL',(who.room_id,)))
            if self.c.dedup in ('user_and_room','user'):
                if who.user_id:
                    excluded.update(r[0] for r in c.execute('SELECT problem_id FROM solves WHERE platform_id=? AND user_id=? AND scored=1',(who.platform_id,who.user_id)))
                else:
                    excluded.update(r[0] for r in c.execute('SELECT s.problem_id FROM solves s JOIN room_members m ON m.platform_id=s.platform_id AND m.user_id=s.user_id WHERE m.room_id=? AND s.scored=1',(who.room_id,)))
            problems=[Problem.load(row[0]) for row in rows]
            return [p for p in problems if p.problem_id not in excluded]
        return await self.db.run(read)

    async def defaults(self,who):
        saved=await self.db.room_default(who.room_id)
        if saved:return saved['platform'],saved['low'],saved['high']
        session=f'{who.platform_id}:GroupMessage:{who.group_id}' if who.group_id else ''
        for line in self.c.group_defaults:
            try:
                room,platform,limits=line.split('|');lo,hi=map(int,limits.split('-'))
                if room==session and platform in ('cf','at') and 0<=lo<=hi<=5000:return platform,lo,hi
            except (ValueError,AttributeError):continue
        return self.c.default_platform,self.c.default_low,self.c.default_high

    async def bank_status(self):
        return await self.db.run(lambda c:[dict(r) for r in c.execute("SELECT platform,normalized_rating,count(*) AS count FROM problems WHERE cache_status='ready' GROUP BY platform,normalized_rating ORDER BY platform,normalized_rating")])
