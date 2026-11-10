"""只接收竞赛官方目录明确链接的题解，或维护者签署的本地已核验快照。"""
import hashlib
import re
import time
from urllib.parse import urljoin,urlparse
from bs4 import BeautifulSoup
from .providers.common import FetchError, section_text


def official_url(platform, url):
    u=urlparse(url)
    if u.scheme!='https' or u.username or u.password or u.port not in (None,443):return False
    if platform=='cf':return u.hostname=='codeforces.com' and bool(re.fullmatch(r'/blog/entry/\d+/?',u.path))
    return platform=='at' and u.hostname=='atcoder.jp' and bool(re.fullmatch(r'/contests/[\w-]+/editorial/\d+/?',u.path))


class EditorialService:
    def __init__(self, fetcher):self.fetcher=fetcher

    async def fetch(self, platform, directory_url, task_id, directory_html=None):
        page=BeautifulSoup(directory_html or await self.fetcher.get(directory_url),'html.parser')
        links=[]
        for a in page.find_all('a',href=True):
            url=urljoin(directory_url,a['href'])
            if not official_url(platform,url):continue
            label=a.get_text(' ',strip=True)
            if platform=='cf' and re.search(r'tutorial|editorial|题解',label,re.I): links.append(url)
            elif platform=='at':
                block=a.find_parent('li') or a.parent
                # AtCoder 官方题解列表按任务分段；仅接受同一块明确指向当前任务的链接。
                task_links=block.select(f'a[href$="/tasks/{task_id}"]')
                if not task_links:
                    section=a.find_parent(class_='editorial-section')
                    heading=section.find_previous_sibling('h3') if section else None
                    task_links=heading.select(f'a[href$="/tasks/{task_id}"]') if heading else []
                if task_links and ('Official' in block.get_text() or '公式' in block.get_text()): links.append(url)
        # 英文正文优先；同一列表仍可选择官方日文版作为后备。
        links.sort(key=lambda u: next((a.get_text(strip=True)!='Editorial' for a in page.find_all('a',href=True) if urljoin(directory_url,a['href'])==u),True))
        for url in dict.fromkeys(links):
            raw=await self.fetcher.get(url)
            doc=BeautifulSoup(raw,'html.parser')
            body=doc.select_one('.ttypography' if platform=='cf' else '#main-container .lang-en')
            if body is None and platform=='at':body=doc.select_one('#main-container .panel-body')
            if body is None and platform=='at':
                heading=doc.select_one('#main-container .col-sm-12 > h2')
                if heading:
                    for sibling in heading.find_next_siblings():
                        if sibling.name=='div' and sibling.find('p') and not sibling.get('class'):
                            body=sibling;break
            content=section_text(body)
            if len(content)<80:continue
            # 不支持图解/视频占主体的题解，宁可跳过，避免不完整内容进入判题。
            if body.find(['img','iframe']):continue
            return content,url,{'method':'official-directory','source':directory_url,'task_id':task_id,
                    'sha256':hashlib.sha256(content.encode()).hexdigest(),'checked_at':time.time()}
        raise FetchError('未找到可确认来源且正文完整的官方题解')

    @staticmethod
    def validate_local(problem):
        v=problem.verification
        expected=hashlib.sha256(problem.official_editorial.encode()).hexdigest()
        if not official_url(problem.platform,problem.editorial_url):raise ValueError('题解 URL 不是支持的官方来源')
        if len(problem.official_editorial.strip())<80:raise ValueError('题解正文为空或过短')
        if v.get('method')!='maintainer-reviewed' or not v.get('reviewer') or not v.get('checked_at') or not v.get('source'):
            raise ValueError('本地题库须附维护者、核验时间和官方关联证据 source')
        if v.get('sha256')!=expected:raise ValueError('题解正文校验值不匹配')
        if not problem.statement.strip() or not problem.samples or not problem.input_spec or not problem.output_spec:
            raise ValueError('缺少完整题面、输入输出或样例')
        u=urlparse(problem.url)
        host='codeforces.com' if problem.platform=='cf' else 'atcoder.jp'
        if u.scheme!='https' or u.hostname!=host:raise ValueError('题目来源非法')
        problem.verified=True
        return problem
