import json

import httpx
import pytest

from astrbot_plugin_kouhu_judge.commands import parse
from astrbot_plugin_kouhu_judge.dedicated_api import DedicatedJudgeAPI
from astrbot_plugin_kouhu_judge.llm import LLMFailure
from astrbot_plugin_kouhu_judge.models import GameError


@pytest.mark.parametrize('first_verdict', ['WA', 'UNCERTAIN'])
async def test_heavy_requires_own_failure_and_rejudges(setup_game, first_verdict):
    game, db, mock, player, other = setup_game
    game.c.api_key = 'mock'
    await game.execute(player, parse('/new'))
    heavy = parse('submit HEAVY [MSG_ID:123]', bare=True)
    with pytest.raises(GameError, match='先在本轮'):
        await game.execute(player, heavy, 'too-early')
    assert not mock.calls

    mock.error = first_verdict == 'UNCERTAIN'
    result = await game.execute(player, parse('/submit guess'), 'first')
    assert result.text.startswith(first_verdict)
    mock.error = False
    with pytest.raises(GameError, match='先在本轮'):
        await game.execute(other, heavy, 'other-player')

    result = await game.execute(player, heavy, 'review')
    assert result.text.startswith('WA')
    assert mock.calls[-1][1]['current_submission'] == 'guess'
    assert mock.options[-1] == {'reasoning_effort': 'low'}
    assert mock.options[-2] == {}  # Extraction is still ordinary mode.
    calls = len(mock.calls)
    await game.execute(player, heavy, 'review')
    assert len(mock.calls) == calls

    result = await game.execute(player, parse('/submit heavy sum each a_i in O(n)'), 'corrected')
    assert result.text.startswith('AC')
    current = await db.current(player.room_id)
    assert (await db.latest_judged(current['round_id'], player.user_id))['judge_model'].endswith(':heavy')

    await game.execute(player, parse('/giveup'))
    game.c.dedup = 'none'
    await game.execute(player, parse('/new'))
    with pytest.raises(GameError, match='先在本轮'):
        await game.execute(player, heavy, 'different-round')


@pytest.mark.parametrize('base', ['https://openrouter.ai/api/v1', 'https://api.openai.com/v1', 'https://example.com/v1'])
async def test_heavy_http_request_keeps_effort_on_rejection(config, base):
    config.api_base_url = base
    config.api_key = 'mock'
    bodies = []

    def reply(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(400)

    api = DedicatedJudgeAPI(config, httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    try:
        with pytest.raises(LLMFailure):
            await api.generate('system', 'prompt', reasoning_effort='low')
        assert len(bodies) == 1 and not api.legacy
        if 'openrouter.ai' in base:
            assert bodies[0]['reasoning'] == {'effort': 'low', 'exclude': True}
            assert bodies[0]['provider']['require_parameters']
        else:
            assert bodies[0]['reasoning_effort'] == 'low'
    finally:
        await api.close()
