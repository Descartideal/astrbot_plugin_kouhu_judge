import re
import ast
from dataclasses import dataclass
from .models import GameError


@dataclass
class Command:
    name: str
    argument: str = ''
    platform: str | None = None
    low: int | None = None
    high: int | None = None
    problem_id: str | None = None
    heavy: bool = False


def clean_message(text):
    """移除 QQTools 附加的消息编号，不将其送给裁判。"""
    return re.sub(r"\[MSG_ID:[^\]]+\]\s*", "", text).strip()


def looks_like_code(text):
    s = clean_message(text)
    if s.startswith('```') and s.endswith('```'):
        s = re.sub(r'^```(?:cpp|c\+\+|python|py)?\s*', '', s, flags=re.I)[:-3]
    if re.search(r'#include\s*[<"]',s) and re.search(r'\bint\s+main\s*\(',s):return True
    if '\n' not in s:return False
    try:tree=ast.parse(s)
    except (SyntaxError,ValueError,RecursionError):return False
    calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call)]
    names=[ast.unparse(node.func) for node in calls]
    reads=any(name=='input' or name.endswith(('stdin.read','stdin.readline','stdin.readlines')) for name in names)
    writes=any(name=='print' or name.endswith('stdout.write') for name in names)
    return reads and writes


def canonical_problem_id(token):
    value=token.strip('[]')
    cf=re.fullmatch(r'(?:cf:?)?(\d+)([a-z]\d*)',value,re.I)
    if cf:return 'cf:'+cf[1]+cf[2].upper()
    at=re.fullmatch(r'(?:at:?)?((?:abc|arc|agc|ahc)\d+_[a-z0-9_]+)',value,re.I)
    if at:return 'at:'+at[1].lower()
    return None


def parse(text, *, bare=False, generic=False, direct_code=False):
    s = clean_message(text)
    if direct_code and not re.match(r'^/?(?:kh\s+)?(?:late|submit)\b',s,re.I) and looks_like_code(s):
        return Command('submit', s)
    slash = s.startswith('/')
    if slash:
        s = s[1:].lstrip()
    if not slash and not bare:
        return None
    if s.lower().startswith('kh '):
        s = s[3:].lstrip()
        generic = True
    m = re.match(r'^(\w+)(?:\s+([\s\S]*))?$', s)
    if not m:
        return None
    name, arg = m[1].lower(), (m[2] or '').strip()
    if name not in {'new','cur','cue','submit','late','clear','giveup','ping','ranklist','help','recalculate','refresh','importbank','sync','setdefault','bankstatus'}:
        return None
    if name in {'ping','help','clear'} and not generic:
        return None
    if name == 'submit':
        parts = arg.split(None, 1)
        if parts and parts[0].lower() == 'heavy':
            return Command(name, parts[1] if len(parts) == 2 else '', heavy=True)
    if name=='late':
        if not arg:raise GameError('用法：late [题号] 做法；省略题号需已开新题。')
        # Accept any whitespace between the optional ID and a multiline solution.
        pieces=arg.split(None,1);target=canonical_problem_id(pieces[0])
        if target:
            if len(pieces)<2 or not pieces[1].strip():raise GameError('late 题号后请写下做法。')
            return Command(name,pieces[1].strip(),problem_id=target)
        return Command(name,arg)
    if name == 'new':
        n = re.fullmatch(r'(?:(cf|at)(?:\s+|$))?(?:(\d+)(?:\s*-\s*(\d+))?)?', arg, re.I)
        if not n:
            raise GameError('用法：new [cf|at] [难度或下限-上限]，单个难度按 ±50 匹配。')
        if n[2] and not n[3]:
            rating=int(n[2]);lo,hi=max(0,rating-50),rating+50
        else:
            lo,hi=(int(n[2]),int(n[3])) if n[2] else (None,None)
        if lo is not None and hi < lo:
            raise GameError('难度下限至少 0，且上限不能小于下限。')
        return Command(name,arg,n[1].lower() if n[1] else None,lo,hi)
    if name not in {'submit','refresh','importbank','sync','setdefault'} and arg:
        raise GameError(f'{name} 不接受参数。')
    return Command(name,arg)
