"""Unabridged source view. Never translate Chinese back into a supposed original."""
import re
from .models import GameError
from .translation_service import TOKEN


class OriginalStatementService:
    async def translate(self,problem,umo='',refresh=False):
        p=problem.public()
        language=problem.verification.get('statement_language','')
        prose=TOKEN.sub('',p['statement'])
        non_latin=len(re.findall(r'[\u3040-\u30ff\u4e00-\u9fff]',prose))
        words=len(re.findall(r'[A-Za-z]{2,}',prose))
        if language in ('ja','zh') or not language and non_latin>=10 and words<10:
            raise GameError('本题缓存暂无英文原文，不会反译冒充原版；可打开原题查看：'+p['url'])
        p['_original_view']=True
        p['_translation']='original'
        return p
