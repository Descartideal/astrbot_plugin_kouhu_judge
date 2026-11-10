"""无模型的快速阅读视图：目标前置，仅移除明确且不含定义/规则的开场闲笔。"""
import re

FORMAL=re.compile(r'\d|\$|\\[([]|\b(?:given|array|sequence|string|integer|graph|tree|vertices|edges|define|represented|length|operation|move|game|player|playing|win|lose|board|grid|matrix|coin|stone|letter|substring|subsequence|sum|product|bit|segment|interval|position|element|distinct|if|must|can|should|need)\b',re.I)
FLAVOR=re.compile(r'^(?:Is there anything better|Once upon a time|After a tiresome|After a long week|It was a beautiful day|One sunny day)\b',re.I)
GOAL=re.compile(r'^(?:Find\b|Determine\b|Calculate\b|Your task\b|You (?:have to|need to|are asked to)\b|请(?:求|找|计算|确定)|求出|找出|计算|确定)',re.I)
FENCED_CODE=re.compile(r'(?<!`)(?P<fence>`{3,})(?:[\w+-]+)?[ \t]*\r?\n(?P<code>[\s\S]*?)\r?\n(?P=fence)(?!`)')


def statement_blocks(text):
    """Yield prose and literal code separately, preserving code indentation."""
    text=text or ''
    start=0
    for match in FENCED_CODE.finditer(text):
        if match.start()>start:
            yield False,text[start:match.start()]
        yield True,match['code']
        start=match.end()
    if start<len(text):
        yield False,text[start:]


def quick_statement(text):
    if FENCED_CODE.search(text or ''):
        return '',text
    parts=[p.strip() for p in re.split(r'\n\s*\n',text or '') if p.strip()]
    for _ in range(2):
        if len(parts)>1 and len(parts[0])<450 and FLAVOR.search(parts[0]) and not FORMAL.search(parts[0]):parts.pop(0)
        else:break
    objective=''
    # 仅搬移原文，不生成摘要、不删定义；优先最后一段明确任务句。
    for pos in range(len(parts)-1,-1,-1):
        if len(parts)>1 and len(parts[pos])<=600 and GOAL.search(parts[pos]):
            objective=parts.pop(pos);break
    return objective,'\n\n'.join(parts)
