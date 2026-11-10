import asyncio
import json


class LLMFailure(Exception):
    pass


class LocalOnlyClient:
    async def call(self,*args,**kwargs):
        raise LLMFailure('普通聊天与翻译不使用独立判题 API')


def json_object(text):
    s=text.strip()
    if s.startswith('```'):
        s=s[s.find('\n')+1:]
        if s.endswith('```'):s=s[:-3]
    value=json.loads(s)
    if not isinstance(value,dict):raise ValueError('expected JSON object')
    return value


class LLMClient:
    def __init__(self, generate, config):
        self.generate,self.c=generate,config
        self.limit=asyncio.Semaphore(config.max_parallel_judges)

    async def call(self, system, payload, *, provider='', umo='', structured=True, reasoning_effort=None):
        options = {'reasoning_effort': reasoning_effort} if reasoning_effort else {}
        async with self.limit:
            for attempt in range(self.c.retries+1):
                try:
                    raw=await asyncio.wait_for(self.generate(system,json.dumps(payload,ensure_ascii=False,separators=(',',':')),provider,umo,**options),timeout=self.c.timeout)
                    return json_object(raw) if structured else raw
                except asyncio.CancelledError:raise
                except Exception as e:
                    if attempt==self.c.retries:raise LLMFailure('模型请求失败或输出格式不正确') from e
                    await asyncio.sleep(0.5*(attempt+1))
