import asyncio
import json
import re
import time
from pathlib import Path
from .models import GameError,Reply,Judgment,Problem,RenderContentError
from .rating import RatingStrategy
from .judge_service import PROMPTS
from .llm import LLMFailure

HELP='''口胡判题：
new [cf|at] [难度或0-2400]：抽题；单数字按±50，默认 cf 1400-1800
cur：查看当前题与样例
cue：查看当前题的英文原版与样例（不翻译）
submit 做法或代码：提交判题
submit heavy [做法]：本人本轮已有 WA 或 UNCERTAIN 后，用轻度思考复判
late [题号] 做法：补交本房间已结束题，仅练习；有活动题时省略题号默认补上一题
giveup：结束当前题，不扣分
clear：只清空普通聊天
ranklist：群排行榜
ping / help：状态与帮助
new 和 /new 等价；群内无参数 new 优先启用提前准备的下一题。
私聊通用命令默认使用 /kh help、/kh ping、/kh clear。
First Blood 后其他人可继续练习，new 开始下一题。
bankstatus：离线题库数量
管理员：/kh setdefault cf 1400-1800（本群默认难度）、/kh sync cf|at 1200-2400 [数量]、/kh importbank 已配置路径、/kh refresh 题目ID、/kh recalculate。
独立 API 可用于判题及题面翻译；在 WebUI 插件配置填写。'''


class RoomService:
    def __init__(self,db,config,problems,renderer,judge,rank_renderer,llm):
        self.db,self.c,self.problems,self.renderer,self.judge,self.rank_renderer,self.llm=db,config,problems,renderer,judge,rank_renderer,llm
        self.rating=RatingStrategy(config);self.room_locks={};self.user_locks={};self.chat_locks={}

    async def initialize(self):
        await self.db.initialize()
        errors=[]
        for path in self.c.import_paths:
            try:await self.problems.import_json(path)
            except Exception:errors.append(path)
        return errors

    async def execute(self,who,cmd,message_id='',umo='',admin=False,model=''):
        if not self.c.allowed(who):return Reply()
        await self.db.touch(who,cmd.name)
        if cmd.name=='new':
            async with self.room_locks.setdefault(who.room_id,asyncio.Lock()):
                current=await self.db.current(who.room_id)
                if current and current['status']=='active':raise GameError('已有活动题，请先完成或 giveup。使用cur可查看当前题目与样例')
                preparation=getattr(self,'preparation',None)
                if preparation and self.c.group_prefetch and who.group_id and not cmd.argument:
                    ready=await preparation.consume(who,umo)
                    p=Problem.load(ready['snapshot'])
                    preparation.schedule(who,umo)
                    return Reply('看完两张图后，用 submit 写下做法。',json.loads(ready['images']))
                for attempt in range(5):
                    p=await self.problems.choose(who,cmd)
                    try:
                        images=await self.renderer.render(p,umo)
                        break
                    except RenderContentError:
                        await self.db.render_status(p.problem_id,False)
                else:raise GameError('此范围内部分题面暂不能完整渲染，已隔离坏格式；请换个范围或稍后重试。')
                # 渲染成功后才提交新题，失败不会留下看不到题面的活动回合。
                await self.db.start_round(who,p.problem_id)
                if preparation and who.group_id:preparation.schedule(who,umo)
                return Reply('看完两张图后，用 submit 写下做法。',images)
        if cmd.name=='cur':
            async with self.room_locks.setdefault(who.room_id,asyncio.Lock()):
                current=await self.db.current(who.room_id)
                if not current:raise GameError('当前没有正在做的题。')
                p=Problem.load(current['problem_snapshot'])
                return Reply('看完两张图后，用 submit 写下做法。',await self.renderer.render(p,umo))
        if cmd.name=='cue':
            async with self.room_locks.setdefault(who.room_id,asyncio.Lock()):
                current=await self.db.current(who.room_id)
                if not current:raise GameError('当前没有正在做的题。')
                p=Problem.load(current['problem_snapshot'])
                images=await self.original_renderer.render(p,umo)
                return Reply('英文原版题面与样例；使用 cur 返回中文题面。',images)
        if cmd.name=='giveup':
            async with self.room_locks.setdefault(who.room_id,asyncio.Lock()):
                await self.db.giveup(who.room_id)
            return Reply('本题已结束，不扣分。可以 new 抽下一题。')
        if cmd.name=='submit':return await self.submit(who,cmd.argument,message_id,umo,model,heavy=cmd.heavy)
        if cmd.name=='late':return await self.late(who,cmd,message_id,umo,model)
        if cmd.name=='clear':
            async with self.chat_locks.setdefault((who.room_id,who.user_id),asyncio.Lock()):await self.db.chat(who,[])
            return Reply('普通聊天上下文已清空，submit 历史保留。')
        if cmd.name=='ping':return Reply('口胡判题运行中。题库、模型与图片服务会在对应操作时核验。')
        if cmd.name=='help':return Reply(HELP)
        if cmd.name=='bankstatus':
            rows=await self.problems.bank_status()
            totals={}
            for r in rows:totals[r['platform']]=totals.get(r['platform'],0)+r['count']
            platform,lo,hi=await self.problems.defaults(who)
            in_range=sum(r['count'] for r in rows if r['platform']==platform and lo<=r['normalized_rating']<=hi)
            return Reply(f'离线可用题：CF {totals.get("cf",0)}，AtCoder {totals.get("at",0)}。\n本房间默认：{platform} {lo}-{hi}，范围内 {in_range} 题。')
        if cmd.name=='ranklist':
            if not who.group_id:raise GameError('ranklist 仅用于群聊。')
            rows=await self.db.leaderboard(who.room_id)
            return Reply(images=await self.rank_renderer.render(who.room_id,rows))
        if cmd.name in {'recalculate','refresh','importbank','sync','setdefault'}:
            if not admin:raise GameError('此命令仅限 AstrBot 管理员。')
            if cmd.name=='setdefault':
                if not who.group_id:raise GameError('请在需要设置默认难度的群中使用；私聊默认值在 WebUI 配置。')
                m=re.fullmatch(r'(cf|at)\s+(\d+)-(\d+)',cmd.argument,re.I)
                if not m or not 0<=int(m[2])<=int(m[3])<=5000:raise GameError('用法：/kh setdefault cf 1400-1800，最低难度 0。')
                async with self.room_locks.setdefault(who.room_id,asyncio.Lock()):
                    before=await self.problems.defaults(who)
                    await self.db.set_default(who,m[1].lower(),int(m[2]),int(m[3]))
                    preparation=getattr(self,'preparation',None)
                    if preparation:
                        if before!=(m[1].lower(),int(m[2]),int(m[3])):await preparation.invalidate(who,umo)
                        else:preparation.schedule(who,umo)
                return Reply(f'本群默认 new 已设置为 {m[1].lower()} {m[2]}-{m[3]}。')
            if cmd.name=='recalculate':
                backup=self.db.path.with_name(f'before-rating-recalculate-{time.time_ns()}.sqlite3')
                await self.db.backup(backup)
                await self.db.recalculate(self.rating)
                return Reply(f'历史积分已按 {self.c.rating_version} 重算，重算前数据库已备份。')
            if cmd.name=='refresh':
                p=await self.db.problem(cmd.argument)
                if not p:raise GameError('缓存中没有此题。')
                images=await self.renderer.render(p,umo,refresh=True)
                await self.db.render_status(p.problem_id,True)
                return Reply('题目翻译和图片缓存已刷新。',images)
            if cmd.name=='importbank':
                # 聊天命令不能读取任意本地文件；只接受 WebUI 明确配置的题库路径。
                if cmd.argument not in self.c.import_paths:raise GameError('请先在 WebUI import_paths 配置允许导入的 JSON 路径。')
                count=await self.problems.import_json(cmd.argument)
                return Reply(f'已导入 {count} 道经过维护者核验的题。')
            m=re.fullmatch(r'(cf|at)\s+(\d+)-(\d+)(?:\s+(\d+))?',cmd.argument,re.I)
            if not m:raise GameError('用法：/kh sync cf|at 1200-2400 [最多检查数量，默认15]')
            low,high=int(m[2]),int(m[3])
            if high<low:raise GameError('难度范围无效。')
            loaded,skipped=await self.problems.sync(m[1].lower(),low,high,int(m[4] or 15))
            return Reply(f'同步完成：{loaded} 道进入可抽取池，{skipped} 道因抓取或官方题解核验失败跳过。')
        return Reply()

    async def submit(self,who,content,message_id,umo,model,*,heavy=False):
        if not heavy and not content.strip():raise GameError('submit 后请写下算法思路或完整代码。')
        if not message_id:raise GameError('未获得稳定消息 ID，无法保证幂等；请重新发送。')
        async with self.user_locks.setdefault((who.room_id,who.user_id),asyncio.Lock()):
            async with self.room_locks.setdefault(who.room_id,asyncio.Lock()):
                current=await self.db.current(who.room_id)
                if not current:raise GameError('当前没有正在做的题，请先 new。')
                history=await self.db.history(current['round_id'],who.user_id)
                if heavy:
                    last_failure=next((item for item in reversed(history) if item['verdict'] in ('WA','UNCERTAIN')),None)
                    if last_failure is None:
                        raise GameError('你需要先在本轮普通 submit，得到 WA 或 UNCERTAIN 后才能使用 submit heavy。')
                    if not self.c.api_key.strip():
                        raise GameError('轻度思考复判需要配置支持 low 思考的独立判题 API。')
                    content=content.strip() or last_failure['content']
                if len(content)>self.c.max_submission_chars:
                    raise GameError(f'提交过长，上限 {self.c.max_submission_chars} 字符；请精简后重发。')
                submission,fresh=await self.db.reserve(who,current,message_id,content)
                if not fresh:return Reply('这条消息已处理或正在判题，不会重复判题和计分。')
                p=Problem.load(current['problem_snapshot'])
                previous=await self.db.latest_judged(current['round_id'],who.user_id)
            # 不持有房间锁调用模型，允许其他用户提交与 giveup；提交时再原子核对 generation。
            model=(model or self.c.api_model)+'@'+getattr(self.judge,'fingerprint','default')
            if heavy:model+=':heavy'
            reusable=not heavy and self.c.reuse_identical_submissions and previous and previous['content'].strip()==content.strip() and previous['judge_model']==model and previous['verdict'] in ('AC','WA')
            if reusable:
                ac=previous['verdict']=='AC'
                j=Judgment(previous['verdict'],1,ac,ac,ac,feedback=previous['feedback'])
            elif heavy:j=await self.judge.judge(p,content,history,umo,heavy=True)
            else:j=await self.judge.judge(p,content,history,umo)
            result=await self.db.finish(who,current,submission['submission_id'],j,model,self.rating)
            if result.get('verdict')=='AC':
                scored='First Blood！' if result['scored'] else '练习 AC，本题不重复发放首杀积分。'
                return Reply(f"AC · {p.title} · 难度 {p.normalized_rating}\n{scored}\nRating {result['rating']}（+{result['delta']}）")
            return Reply(f"{result.get('verdict','UNCERTAIN')} · {result['feedback']}")

    async def late(self,who,cmd,message_id,umo='',model=''):
        content=cmd.argument.strip()
        if not content:raise GameError('late 后请写下做法。')
        if len(content)>self.c.max_submission_chars:raise GameError(f'补交过长，上限 {self.c.max_submission_chars} 字符；请精简后重发。')
        if not message_id:raise GameError('未获得稳定消息 ID，请重新发送补交消息。')
        async with self.user_locks.setdefault((who.room_id,who.user_id),asyncio.Lock()):
            # Wait for a concurrent new to commit before resolving “上一题”.
            async with self.room_locks.setdefault(who.room_id,asyncio.Lock()):
                target=await self.db.late_target(who,cmd.problem_id)
                sid=await self.db.reserve_late(who,target,message_id,content)
            if sid is None:return Reply('这条消息已处理或正在判题，不会重复补交。')
            history=await self.db.late_history(target['round_id'],who.user_id)
            p=Problem.load(target['problem_snapshot'])
            try:j=await self.judge.judge(p,content,history,umo)
            except asyncio.CancelledError:raise
            except Exception:j=Judgment(feedback='补交判题服务暂不可用，本次不计分，请用新消息重试。')
            model=(model or self.c.api_model)+'@'+getattr(self.judge,'fingerprint','default')
            if not await self.db.finish_late(who,target,sid,j,model):return Reply('这条补交已处理，不会重复记录。')
            if j.verdict=='AC':return Reply(f'补交 AC · {p.problem_id} · {p.title} · 难度 {p.normalized_rating}\n练习 AC，不补发首杀积分；当前题目保持不变。')
            return Reply(f'补交 {j.verdict} · {p.problem_id} · {j.feedback}')

    async def chat(self,who,text,umo=''):
        if not self.c.allowed(who):return Reply()
        if self.c.judge_only:
            return Reply('口胡模式不调用普通聊天 API。用 new 抽题、cur 看题、submit 做法判题、/kh help 查看帮助。')
        await self.db.touch(who,'chat')
        if len(text)>12000:return Reply('聊天内容过长，请精简。')
        async with self.chat_locks.setdefault((who.room_id,who.user_id),asyncio.Lock()):
            current=await self.db.current(who.room_id)
            history=await self.db.chat(who)
            p=Problem.load(current['problem_snapshot']) if current else None
            chat_round=current['round_id'] if current else None
            history=[m for m in history if m.get('round_id')==chat_round]
            # 活动题拒绝明显索要算法、答案或代码的请求；不向通用聊天管线泄漏。
            if p and re.search(r'答案|题解|思路|算法|提示|观察|怎么做|怎么解|代码|solution|hint|algorithm|ignore|忽略',text,re.I):
                return Reply('活动题期间只提供翻译和题意说明，请用 submit 提交做法。')
            if p and not re.search(r'翻译|英语|英文|日文|句子|题意|输入|输出|含义|意思|定义|meaning|translate|definition|what does',text,re.I):
                return Reply('活动题期间只提供翻译和题意说明，请说明需要解释的句子或题目定义。')
            try:
                response=await self.llm.call((PROMPTS/'chat.txt').read_text('utf-8'),
                    {'active':p is not None,'problem':p.public() if p else None,'history':history[-10:],'message':text},provider=self.c.chat_provider,umo=umo)
                if p and response.get('scope')!='statement':return Reply('活动题期间只提供翻译和题意说明，请用 submit 提交做法。')
                reply=response.get('reply')
                if not isinstance(reply,str) or not reply.strip():raise ValueError()
                # 持久化仅本用户普通聊天；不给 JudgeService。
                await self.db.chat(who,(history+[{'role':'user','content':text,'round_id':chat_round},{'role':'assistant','content':reply[:6000],'round_id':chat_round}])[-10:])
                return Reply(reply[:6000])
            except (LLMFailure,ValueError):return Reply('聊天服务暂不可用，请稍后重试。')
