import json
import pytest
import httpx
from astrbot_plugin_kouhu_judge.commands import parse
from astrbot_plugin_kouhu_judge.dedicated_api import DedicatedJudgeAPI

@pytest.mark.parametrize('text',['new','/new','new cf 1200-2400','/NEW cf 1200-2400'])
def test_msg_id_new(text):
    assert parse(text+' [MSG_ID:1604415504]',bare=True).name=='new'

def test_submit_msg_id():
    assert parse('submit 123 [MSG_ID:42]',bare=True).argument=='123'

@pytest.mark.asyncio
async def test_openrouter_minimal(config):
    config.api_key='secret';config.api_base_url='https://openrouter.ai/api/v1';config.api_model='openai/gpt-6.1-sol'
    def handler(req):
        body=json.loads(req.content)
        assert body['model']=='openai/gpt-6.1-sol'
        assert 'response_format' not in body and 'reasoning_effort' not in body
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
    api=DedicatedJudgeAPI(config,httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:assert await api.generate('JSON','{}')=='{}'
    finally:await api.close()

@pytest.mark.asyncio
async def test_native_parameter_negotiation(config):
    config.api_key='secret';calls=[]
    def handler(req):
        body=json.loads(req.content);calls.append(body)
        if 'response_format' in body:return httpx.Response(400)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    api=DedicatedJudgeAPI(config,httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        assert await api.generate('JSON','{}')=='{}'
        assert len(calls)==2 and 'max_tokens' in calls[-1]
    finally:await api.close()
