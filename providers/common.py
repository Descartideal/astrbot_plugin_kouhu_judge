import asyncio
import re
import httpx
from bs4 import BeautifulSoup


class FetchError(Exception):
    def __init__(self,message,status_code=None,retry_after=60):
        super().__init__(message);self.status_code=status_code;self.retry_after=retry_after


class Fetcher:
    def __init__(self, client=None,proxy=''):
        self.client=client or httpx.AsyncClient(timeout=25,proxy=proxy or None,trust_env=False,follow_redirects=False,headers={'User-Agent':'KouhuJudge/1.1 (official statement cache)'})
        self.lock=asyncio.Lock()

    async def get(self, url, json_data=False):
        # 全局串行和间隔，避免同步题库时突发请求；禁止自动跟随到未知主机。
        async with self.lock:
            for attempt in range(2):
                try:
                    r=await self.client.get(url)
                    if r.status_code==429:
                        try:delay=max(60,min(86400,int(r.headers.get('retry-after','60'))))
                        except ValueError:delay=60
                        raise FetchError('数据源限流，暂停下载',429,delay)
                    r.raise_for_status()
                    if len(r.content)>15_000_000: raise FetchError('页面过大')
                    return r.json() if json_data else r.text
                except (httpx.HTTPError,ValueError) as e:
                    if attempt: raise FetchError('数据源暂时不可用') from e
                    await asyncio.sleep(1)
            await asyncio.sleep(0.3)

    async def close(self):
        await self.client.aclose()


def text(node):
    if node is None: return ''
    node=BeautifulSoup(str(node),'html.parser')
    only_pre=bool(node.find('pre') and len(node.find_all(recursive=False))==1 and node.find(recursive=False).name=='pre')
    for br in node.find_all('br'): br.replace_with('\n')
    for script in node.find_all('script'):
        if 'math/tex' in script.get('type',''):
            display='mode=display' in script.get('type','')
            script.replace_with(('\\[' if display else '\\(')+script.get_text()+('\\]' if display else '\\)'))
        else: script.decompose()
    for rendered in node.select('.katex'):
        annotation=rendered.select_one('annotation[encoding="application/x-tex"]')
        if annotation:rendered.replace_with('\\('+annotation.get_text()+'\\)')
    for tag in node.select('.MathJax, .MathJax_Preview, .katex, style'): tag.decompose()
    # Preserve the original DOM's scripts before get_text flattens them.
    for formula in node.select('.tex-span, var'):
        if formula.parent is None:continue
        for sub in formula.find_all(['sub','sup']):
            sub.replace_with(('_' if sub.name=='sub' else '^')+'{'+sub.get_text()+'}')
        value=formula.get_text()
        formula.replace_with(value if value.startswith(('\\(','$')) else '\\('+value+'\\)')
    for script in node.find_all(['sub','sup']):
        from bs4 import NavigableString
        previous=script.previous_sibling
        suffix=('_' if script.name=='sub' else '^')+'{'+script.get_text()+'}'
        if isinstance(previous,NavigableString):
            m=re.search(r'([A-Za-z]+|\d+)$',str(previous))
            if m:
                previous.replace_with(str(previous)[:m.start()])
                script.replace_with('\\('+m[0]+suffix+'\\)');continue
        script.replace_with('\\({}'+suffix+'\\)')
    for var in node.find_all('var'):
        value=var.get_text()
        var.replace_with(value if value.startswith(('\\(','$')) else '\\('+value+'\\)')
    # CF 每行样例用 div 包裹，不能丢失换行。
    for pre in node.find_all('pre'):
        divs=pre.find_all('div',recursive=False)
        for div in divs[:-1]:div.insert_after('\n')
    result=node.get_text(separator='',strip=False).replace('\xa0',' ')
    return result if only_pre else result.strip()


def section_text(node):
    if node is None:return ''
    copy=BeautifulSoup(str(node),'html.parser')
    for title in copy.select('.section-title'):title.decompose()
    for pre in copy.find_all('pre'):
        code=text(pre).strip('\r\n')
        # Some CF statements wrap the entire HTML pre in literal backticks.
        if code.startswith('`') and not code.startswith('``') and code.rstrip().endswith('`'):
            code=code.rstrip()[1:-1].strip('\r\n')
        fence='`'*max(3,1+max(map(len,re.findall(r'`+',code)),default=0))
        pre.replace_with(f'\n\n{fence}\n{code}\n{fence}\n\n' if code.strip() else '\n\n')
    for block in copy.select('p, li'):block.insert_after('\n\n')
    return text(copy)
