from pathlib import Path
import asyncio
from dataclasses import replace
from astrbot.api import AstrBotConfig,logger
from astrbot.api.event import filter,AstrMessageEvent
from astrbot.api.star import Context,Star,StarTools
from astrbot.api.message_components import At
from .config import Config
from .models import Identity,GameError
from .commands import parse,clean_message
from .database import Database
from .providers.common import Fetcher
from .providers.codeforces import CodeforcesProvider
from .providers.atcoder import AtCoderProvider
from .editorial_service import EditorialService
from .problem_service import ProblemService
from .translation_service import TranslationService
from .original_service import OriginalStatementService
from .renderer import Renderer,RankRenderer
from .llm import LLMClient,LLMFailure,LocalOnlyClient
from .judge_service import JudgeService
from .room_service import RoomService
from .dedicated_api import DedicatedJudgeAPI
from .providers.cf_archive import CFArchiveProvider
from .offline_downloader import OfflineDownloader
from .at_downloader import AtCoderDownloader,PacedFetcher
from .preparation_service import PreparationService


class KouhuScopeFilter(filter.CustomFilter):
    owner=None

    def filter(self,event,cfg):
        return self.owner is not None and self.owner.accepts_event(event)


class KouhuJudgePlugin(Star):
    def __init__(self,context:Context,config:AstrBotConfig):
        super().__init__(context)
        self.c=Config.from_mapping(config)
        self.data=Path(StarTools.get_data_dir('astrbot_plugin_kouhu_judge'))
        self.db=Database(self.data/'kouhu.sqlite3',self.c)
        self.fetcher=Fetcher(proxy=self.c.download_proxy);editorials=EditorialService(self.fetcher)
        self.api=DedicatedJudgeAPI(self.c)
        self.llm=LLMClient(self.generate,self.c)
        self.aux_llm=LocalOnlyClient()
        self.translation_llm=LLMClient(self.generate_translation,replace(self.c,retries=0))
        self.renderer=Renderer(self.c,self.data/'images',TranslationService(self.translation_llm,self.c,self.data/'translations'))
        original_config=replace(self.c,translate=False,translation_version='original-en-v1')
        self.original_renderer=Renderer(original_config,self.data/'images-original',OriginalStatementService())
        cf=CodeforcesProvider(self.fetcher,editorials,self.data/'http-cache',self.c)
        at_fetcher=PacedFetcher(self.fetcher)
        at=AtCoderProvider(at_fetcher,EditorialService(at_fetcher),self.c)
        self.problems=ProblemService(self.db,self.c,{'cf':cf,'at':at})
        self.downloader=OfflineDownloader(self.db,self.c,cf,CFArchiveProvider(self.fetcher,self.data/'http-cache'/'cf-archive'),logger)
        self.downloader.at_downloader=AtCoderDownloader(self.db,self.c,at,self.data/'http-cache',self.downloader.idle,self.problems.sync_lock)
        self.problems.downloader=self.downloader
        self.download_task=None
        self.game=RoomService(self.db,self.c,self.problems,self.renderer,JudgeService(self.llm,self.c),RankRenderer(self.c,self.data/'rank',self.fetcher),self.aux_llm)
        self.preparation=PreparationService(self.db,self.c,self.problems,self.renderer)
        self.game.preparation=self.preparation
        self.game.original_renderer=self.original_renderer
        self.active_rooms=set()
        KouhuScopeFilter.owner=self

    async def initialize(self):
        errors=await self.game.initialize()
        self.active_rooms=set(await self.db.run(lambda c:[r[0] for r in c.execute('SELECT room_id FROM rooms WHERE current_problem_id IS NOT NULL')]))
        if errors:logger.warning('口胡判题：部分本地题库未通过校验，请检查题库与官方题解核验记录。')
        self.preparation.start()
        if self.c.background_download and self.download_task is None:
            self.download_task=asyncio.create_task(self.downloader.run())

    async def generate(self,system,prompt,provider,umo,*,reasoning_effort=None):
        if self.c.api_key.strip():
            if reasoning_effort:
                return await self.api.generate(system,prompt,reasoning_effort=reasoning_effort)
            return await self.api.generate(system,prompt)
        if reasoning_effort:
            raise LLMFailure('轻度思考复判需要配置支持思考参数的独立判题 API')
        selected=provider or await self.context.get_current_chat_provider_id(umo)
        result=await self.context.llm_generate(chat_provider_id=selected,prompt=prompt,system_prompt=system)
        return result.completion_text

    async def generate_translation(self,system,prompt,provider,umo):
        if not provider and self.c.translation_use_dedicated and self.c.api_key.strip():
            return await self.api.generate(system,prompt,max_output_tokens=self.c.translation_max_output_tokens)
        selected=provider or await self.context.get_current_chat_provider_id(umo)
        result=await self.context.llm_generate(chat_provider_id=selected,prompt=prompt,system_prompt=system)
        return result.completion_text

    @staticmethod
    def original_text(event):
        # AstrBot 4.28.1 唤醒阶段会移除 event.message_str 的 /，原始消息仍保留。
        return clean_message(getattr(event.message_obj,'message_str',None) or event.message_str)

    def accepts_event(self,event):
        who=Identity(str(event.get_platform_id()),str(event.get_sender_id()),str(event.get_group_id() or ''),event.get_sender_name())
        if not self.c.allowed(who):return False
        if any(isinstance(x,At) and str(x.qq)==str(event.get_self_id()) for x in event.get_messages()):
            self.downloader.mark_activity();return True
        try:
            accepted=parse(self.original_text(event),bare=self.c.bare_commands,
                         generic=bool(who.group_id) or self.c.private_generic_commands or who.room_id in self.active_rooms,
                         direct_code=self.c.direct_code and who.room_id in self.active_rooms) is not None
            if accepted:self.downloader.mark_activity()
            return accepted
        except GameError:return True  # 仅将格式错误的本插件指令送入路由返回用法。

    @filter.custom_filter(KouhuScopeFilter,False,priority=100)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE,priority=100)
    async def route(self,event:AstrMessageEvent):
        who=Identity(str(event.get_platform_id()),str(event.get_sender_id()),str(event.get_group_id() or ''),event.get_sender_name())
        if not self.c.allowed(who):return
        try:
            current=await self.db.current(who.room_id)
            generic=bool(who.group_id) or self.c.private_generic_commands or current is not None
            text=self.original_text(event)
            cmd=parse(text,bare=self.c.bare_commands,generic=generic,
                      direct_code=self.c.direct_code and current is not None)
            mentioned=any(isinstance(x,At) and str(x.qq)==str(event.get_self_id()) for x in event.get_messages())
            if cmd is None:
                if not mentioned:return
                event.should_call_llm(False);event.stop_event()
                response=await self.game.chat(who,text,event.unified_msg_origin)
            else:
                event.should_call_llm(False);event.stop_event()
                if cmd.name=='new':await event.send(event.plain_result('正在抽题，题面马上发出。'))
                if cmd.name=='ranklist' and who.group_id:
                    try:
                        group=await asyncio.wait_for(event.get_group(),timeout=8)
                        if group and group.members:
                            await self.db.refresh_member_names(who.room_id,[(m.user_id,m.nickname) for m in group.members])
                    except Exception:pass  # 名片接口失败时仍可用最近缓存生成榜单。
                model=''
                if cmd.name=='late' or cmd.name=='submit' and current:
                    model=self.c.api_model if self.c.api_key.strip() else (self.c.judge_provider or await self.context.get_current_chat_provider_id(event.unified_msg_origin))
                response=await self.game.execute(who,cmd,str(event.message_obj.message_id or ''),event.unified_msg_origin,
                         admin=event.is_admin(),model=model)
                if await self.db.current(who.room_id):self.active_rooms.add(who.room_id)
                else:self.active_rooms.discard(who.room_id)
            if response.images:
                chain=[]
                for path in response.images:chain.extend(event.image_result(path).chain)
                if response.text:chain.extend(event.plain_result(response.text).chain)
                await event.send(event.chain_result(chain))
            elif response.text:await event.send(event.plain_result(response.text))
        except GameError as e:
            event.should_call_llm(False);event.stop_event()
            await event.send(event.plain_result(str(e)))
        except Exception as e:
            # 不记录异常正文（提供商异常可能带请求/密钥）；故障局限于本次命令。
            logger.error('口胡判题操作失败，错误类型：'+type(e).__name__)
            event.should_call_llm(False);event.stop_event()
            await event.send(event.plain_result('口胡判题服务暂时不可用，本次操作未完成，请稍后重试或联系管理员。'))

    async def terminate(self):
        if KouhuScopeFilter.owner is self:KouhuScopeFilter.owner=None
        if self.download_task:
            self.download_task.cancel()
            try:await self.download_task
            except asyncio.CancelledError:pass
            self.download_task=None
        await self.preparation.close()
        await self.original_renderer.close()
        await self.renderer.close();await self.fetcher.close();await self.api.close()
