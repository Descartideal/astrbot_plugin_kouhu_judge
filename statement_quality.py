"""Evidence-based repairs; never infer a missing exponent from a decimal string."""
import re
from .models import RenderContentError
from .statement_layout import FENCED_CODE,statement_blocks

PSEUDOCODE_REFERENCE=re.compile(
    r'\b(?:following\s+pseudo[ -]?code|pseudo[ -]?code\s+(?:below|as follows))[^\n:.]{0,100}[:.]'
    r'(?=[ \t]*(?:\n|$))',re.I)
LEGACY_CODE=re.compile(r'`[^\n`]+\n|[ \t]{4}\S|(?:let\s+)?\w+\s*(?::?=|\+=)|(?:function|def)\s+\w+\s*\(')
CF1373C_INTRO='This process can be described by the following pseudocode:'
CF1373C_CODE="""```
res = 0
for init = 0 to inf
    cur = init
    ok = true
    for i = 1 to |s|
        res = res + 1
        if s[i] == '+'
            cur = cur + 1
        else
            cur = cur - 1
        if cur < 0
            ok = false
            break
    if ok
        break
```"""


def checked_public(d):
    # The Open R1 archive omitted this pre block. Checked against
    # https://codeforces.com/problemset/problem/1373/C on 2026-11-10.
    if d['problem_id']=='cf:1373C':
        missing=CF1373C_INTRO+'\n\nNote that the $$$inf$$$'
        d['statement']=d['statement'].replace(
            missing,CF1373C_INTRO+'\n\n'+CF1373C_CODE+'\n\nNote that the $$$inf$$$',1)
    # CF818F official input bound checked on 2026-11-09.
    if d['problem_id']=='cf:818F':
        d['input_spec']=d['input_spec'].replace(
            '1 ≤ ni ≤ 2·109',r'\(1\le n_i\le 2\cdot 10^{9}\)')
    # Checked against https://codeforces.com/problemset/problem/870/C on
    # 2026-10-30. Apply only to the exact known flattened input text.
    if d['problem_id']=='cf:870C':
        old='The first line contains single integer q (1 ≤ q ≤ 105)\xa0— the number of queries.\n\nq lines follow. The (i + 1)-th line contains single integer ni (1 ≤ ni ≤ 109)\xa0— the i-th query.'
        if d['input_spec']==old:
            d['input_spec']=r'The first line contains integer \(q\) (\(1\le q\le10^{5}\)), the number of queries.'+'\n\n'+r'Each of the next \(q\) lines contains integer \(n_i\) (\(1\le n_i\le10^{9}\)).'
            d['statement']=d['statement'].replace('integer ni.',r'integer \(n_i\).').replace('represent ni as',r'represent \(n_i\) as')
    # This signature is ambiguous: skip, rather than turning a genuine 105 into
    # 10^5. Original database rows are retained for maintainer review.
    for key in ('statement','constraints','input_spec','output_spec'):
        value=d.get(key) or ''
        if d['problem_id'].startswith('cf:'):
            # Mask block contents so comments inside code cannot trigger the guard.
            prose=FENCED_CODE.sub(lambda m:'\0'+' '*(len(m[0])-1),value)
            for reference in PSEUDOCODE_REFERENCE.finditer(prose):
                remainder=re.sub(r'^(?:[ \t]*\r?\n)+','',value[reference.end():])
                block=FENCED_CODE.match(remainder.lstrip())
                if not (block and block['code'].strip()) and not LEGACY_CODE.match(remainder):
                    raise RenderContentError('题面引用的伪代码疑似缺失，需核对原题。')
        if any(re.search(r'(?:≤|<=|\\leq?\s*)\s*10[3-9](?!\d)',part)
               for is_code,part in statement_blocks(value) if not is_code):
            raise RenderContentError('题面范围疑似丢失上标，已隔离；需核对原题后修复。')
    return d
