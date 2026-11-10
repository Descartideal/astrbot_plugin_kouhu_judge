"""OpenAI Chat Completions compatible endpoints; no request bodies or secrets in logs."""
import logging
from urllib.parse import urlsplit
import httpx
from .llm import LLMFailure

log=logging.getLogger(__name__)

class DedicatedJudgeAPI:
    def __init__(self,config,client=None):
        self.c=config
        self.client=client or httpx.AsyncClient(timeout=config.timeout,proxy=config.api_proxy or None,follow_redirects=False,trust_env=False)
        self.legacy=False

    async def generate(self,system,prompt,provider='',umo='',*,max_output_tokens=None,reasoning_effort=None):
        if not self.c.api_key.strip():raise LLMFailure('未配置专用 API')
        base=self.c.api_base_url.strip().rstrip('/')
        url=base if base.endswith('/chat/completions') else base+'/chat/completions'
        # OpenRouter and generic proxies do not universally accept OpenAI reasoning options.
        host=urlsplit(base).hostname
        native=host=='api.openai.com'
        body={'model':self.c.api_model,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}], 'stream':False}
        if native and not self.legacy:
            body.update(max_completion_tokens=int(max_output_tokens or self.c.api_max_output_tokens),response_format={'type':'json_object'},reasoning_effort=self.c.api_reasoning_effort)
        else:body['max_tokens']=int(max_output_tokens or self.c.api_max_output_tokens)
        if reasoning_effort:
            if host=='openrouter.ai':
                body['reasoning']={'effort':reasoning_effort,'exclude':True}
                body['provider']={'require_parameters':True}
            else:
                body['reasoning_effort']=reasoning_effort
        try:
            for attempt in range(2):
                r=await self.client.post(url,headers={'Authorization':'Bearer '+self.c.api_key,'Content-Type':'application/json'},json=body)
                if r.status_code in (400,422) and native and not self.legacy and attempt==0 and not reasoning_effort:
                    # Only negotiate request parameters on a rejected request, never on auth/rate errors.
                    self.legacy=True
                    for key in ('response_format','reasoning_effort','max_completion_tokens'):body.pop(key,None)
                    body['max_tokens']=int(max_output_tokens or self.c.api_max_output_tokens)
                    continue
                if r.is_error:
                    log.warning('Kouhu judge API HTTP %s',r.status_code)
                r.raise_for_status()
                value=r.json();choice=value['choices'][0]
                if choice.get('finish_reason') in ('length','content_filter'):raise LLMFailure('判题输出未完成')
                text=choice['message']['content']
                if isinstance(text,list):text=''.join(x.get('text','') for x in text if isinstance(x,dict))
                if not isinstance(text,str) or not text.strip():raise LLMFailure('空判题输出')
                return text
        except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError) as e:
            log.warning('Kouhu judge API failure type=%s',type(e).__name__)
            raise LLMFailure('判题 API 请求失败，请检查地址、模型、密钥和网络。') from None

    async def close(self):await self.client.aclose()
