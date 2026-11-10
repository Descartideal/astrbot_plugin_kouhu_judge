"""Short, answer-free feedback; deterministic sample checks never execute user code."""
import ast
import math
import re


def verified_formula_example(problem,content):
    # Scalar input and answer only. Never execute the submitted program.
    if re.search(r'mod|取模',problem.statement+' '+problem.output_spec,re.I):return ''
    batch = problem.problem_id == 'cf:818F'  # Official input: q followed by q scalar queries.
    if not batch and not re.search(r'\bn\b',problem.input_spec,re.I):return ''
    text=content.strip().replace('（','(').replace('）',')').replace('，',',').replace('×','*')
    m=re.fullmatch(r'(?:(?:取)?x\s*=\s*([^,]+),\s*)?(?:输出|答案为|答案是)\s*([^。]+)[。]?',text)
    if not m or len(text)>150:return ''
    try:
        def parse_expression(expr):
            if not re.fullmatch(r'[A-Za-z0-9 \t()+*,.%/\-]+',expr.strip()):
                raise ValueError()
            expr=re.sub(r'(?<=\d)(?=sqrt\(|[nx]\b)', '*', expr.strip().rstrip('.'))
            tree=ast.parse(expr,mode='eval')
            if len(list(ast.walk(tree)))>40:raise ValueError()
            return tree.body

        assignment=parse_expression(m[1]) if m[1] else None
        expression=parse_expression(m[2])

        def calc(node,n,x=None):
            if isinstance(node,ast.Constant) and type(node.value) is int:v=node.value
            elif isinstance(node,ast.Constant) and node.value==0.5 and type(node.value) is float:v=0.5
            elif isinstance(node,ast.Name) and node.id.lower()=='n':v=n
            elif isinstance(node,ast.Name) and node.id=='x' and x is not None:v=x
            elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
                v=calc(node.operand,n,x)*(-1 if isinstance(node.op,ast.USub) else 1)
            elif isinstance(node,ast.BinOp):
                a,b=calc(node.left,n,x),calc(node.right,n,x)
                if isinstance(node.op,ast.Add):v=a+b
                elif isinstance(node.op,ast.Sub):v=a-b
                elif isinstance(node.op,ast.Mult):v=a*b
                elif isinstance(node.op,ast.FloorDiv) and type(a) is type(b) is int:v=a//b
                elif isinstance(node.op,ast.Mod) and type(a) is type(b) is int:v=a%b
                else:raise ValueError()
            elif isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id.upper() in ('C','A') and len(node.args)==2 and not node.keywords:
                a,b=(calc(arg,n,x) for arg in node.args)
                if type(a) is not int or type(b) is not int or not 0<=b<=min(a,20) or a>2000:raise ValueError()
                v=(math.comb if node.func.id.upper()=='C' else math.perm)(a,b)
            elif isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and len(node.args)==1 and not node.keywords:
                arg=calc(node.args[0],n,x)
                if node.func.id=='sqrt' and type(arg) is int and 0<=arg<=10**6:
                    root=math.isqrt(arg)
                    v=root if root*root==arg else math.sqrt(arg)
                elif node.func.id in ('floor','ceil'):
                    # Skip ambiguous floating-point boundaries.
                    if type(arg) is float and abs(arg-round(arg))<1e-3:raise ValueError()
                    v=(math.floor if node.func.id=='floor' else math.ceil)(arg)
                else:raise ValueError()
            else:raise ValueError()
            if abs(v)>(10**6 if type(v) is float else 10**12):raise ValueError()
            return v
        for i,s in enumerate(problem.samples):
            if not re.fullmatch(r'\s*\d+(?:\s+\d+)*\s*',s.input) or not re.fullmatch(r'\s*-?\d+(?:\s+-?\d+)*\s*',s.output):continue
            inputs=list(map(int,s.input.split()))
            outputs=list(map(int,s.output.split()))
            if batch:
                if inputs[0]!=len(outputs) or len(inputs)!=len(outputs)+1:continue
                inputs=inputs[1:]
            elif len(inputs)!=1 or len(outputs)!=1:continue
            for n,expected in zip(inputs,outputs):
                if n>2000:continue
                try:
                    actual=calc(expression,n,calc(assignment,n) if assignment else None)
                except (ValueError, ArithmeticError):
                    continue
                if type(actual) is int and actual!=expected:
                    return f'样例{i+1}：n={n}，该式得{actual}，正确输出{expected}。'
    except (ValueError,SyntaxError,TypeError,ArithmeticError,RecursionError):pass
    return ''


def _complete_reason(candidates, limit):
    for value in candidates:
        text=re.sub(r'\s+',' ',value).strip().strip('`*# ')
        if re.search(r'正确(?:做法|算法|解法)|官方题解|改用|应(?:该)?使用|反例|https?://',text):continue
        parts=re.split(r'例如|比如|样例|示例',text,maxsplit=1)
        prefix=parts[0]
        if len(parts)>1:
            # Remove the entire example clause, including prefixes such as “但题面”.
            prefix=re.sub(r'[，,；;。！？：:]?\s*(?:但|而|与)?\s*(?:在|按|由)?\s*(?:题面|题目|官方)?\s*(?:第[一二三四五六七八九十0-9]+个?)?\s*$', '',prefix)
        text=prefix.rstrip('，,；;：: ')
        if re.search(r'(?:但|而|与|例如|因为|根据|题面|题目)$',text):continue
        if text and len(text)<=limit:return text
        sentences=re.findall(r'[^。！？]+[。！？]',text)
        if sentences and len(sentences[0])<=limit:return sentences[0]
    return ''


def concise_wa(j,limit,example=''):
    # Verified examples are complete reasons in their own right.
    if example and len(example)<=limit:return example
    return _complete_reason(j.logical_errors or j.missing_key_points,limit) or '请说明你这一步如何满足题目条件，并补充必要的边界或复杂度依据。'


def concise_uncertain(j,limit):
    return _complete_reason(j.missing_key_points,limit) or '暂时无法确定，请补充关键步骤、适用条件和复杂度。'
