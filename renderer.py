import asyncio
import base64
import io
import json
import math
import os
import time
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageStat
from jinja2 import Environment,FileSystemLoader,select_autoescape
from .cache import digest,atomic_write
from .models import GameError,RenderContentError
from .statement_layout import quick_statement,statement_blocks,FENCED_CODE

ROOT=Path(__file__).parent
STATEMENT_RENDER_SCHEMA='literal-code-v1'


def valid_image(path):
    try:
        with Image.open(path) as im:
            return im.width==980 and im.height>=150 and max(ImageStat.Stat(im.convert('RGB')).stddev)>5
    except (OSError,ValueError):return False


class Renderer:
    def __init__(self, config, cache_dir, translation):
        self.c,self.cache_dir,self.translation=config,Path(cache_dir),translation
        self.cache_dir.mkdir(parents=True,exist_ok=True)
        self.lock=asyncio.Lock();self.render_lock=asyncio.Lock();self.browser=None;self.pw=None;self.closed=False
        self.env=Environment(loader=FileSystemLoader(ROOT/'templates'),autoescape=select_autoescape(['html']))
        self.env.globals['statement_blocks']=statement_blocks

    async def _browser(self):
        async with self.lock:
            if self.closed:raise GameError('图片服务正在重载，请稍后重试。')
            if self.browser and self.browser.is_connected():return self.browser
            if self.pw:
                try:await self.pw.stop()
                except Exception:pass
                self.pw=None;self.browser=None
            from playwright.async_api import async_playwright
            self.pw=await async_playwright().start()
            options={'headless':True}
            if self.c.browser_path:options['executable_path']=self.c.browser_path
            elif self.c.browser_channel:options['channel']=self.c.browser_channel
            try:self.browser=await self.pw.chromium.launch(**options)
            except Exception:
                await self.pw.stop();self.pw=None
                raise GameError('图片浏览器未就绪，请管理员安装 Chromium 或配置 browser_path。')
            return self.browser

    def _assets(self):
        base=ROOT/'static'/'katex'
        try:
            css=(base/'katex.min.css').read_text('utf-8')
            import re
            def embed(m):
                f=base/m[1].strip('"\'')
                return 'url(data:font/woff2;base64,'+base64.b64encode(f.read_bytes()).decode()+')'
            css=re.sub(r'url\(([^)]+)\)',embed,css)
            return css,(base/'katex.min.js').read_text('utf-8'),(base/'auto-render.min.js').read_text('utf-8')
        except OSError as e:raise GameError('缺少本地 KaTeX 资源，请按安装说明准备 static/katex。') from e

    def html(self,p,samples_page):
        katex_css,katex_js,auto=self._assets()
        css=(ROOT/'templates'/('compact.css' if self.c.compact_layout else 'paper.css')).read_text('utf-8')
        if self.c.font_path:
            data=base64.b64encode(Path(self.c.font_path).read_bytes()).decode()
            css+="@font-face{font-family:KouhuCJK;src:url(data:font/ttf;base64,"+data+")}body{font-family:KouhuCJK,'Microsoft YaHei',sans-serif}"
        # CF 的 $$$ 是行内公式分隔符，统一为 KaTeX auto-render 格式。
        p=json.loads(json.dumps(p,ensure_ascii=False))
        if p.get('platform')=='cf':
            for field in ('title','statement','constraints','input_spec','output_spec'):
                p[field]=self._normalize_cf_math(p.get(field))
            for sample in p['samples']:
                sample['explanation']=self._normalize_cf_math(sample.get('explanation'))
        if self.c.compact_layout and not p.get('_original_view'):
            p['_objective'],p['statement']=quick_statement(p['statement'])
        return self.env.get_template('paper.html').render(p=p,compact=self.c.compact_layout,samples_page=samples_page,css=css,katex_css=katex_css,katex_js=katex_js,auto_render_js=auto,math_js=(ROOT/'templates'/'math.js').read_text('utf-8'))

    @staticmethod
    def _normalize_cf_math(value):
        value=value or ''
        parts=[]
        start=0
        for match in FENCED_CODE.finditer(value):
            parts.extend((value[start:match.start()].replace('$$$','$'),match[0]))
            start=match.end()
        parts.append(value[start:].replace('$$$','$'))
        return ''.join(parts)

    def _manifest(self,problem):
        public=problem.public()
        fields=[public[key] for key in ('title','statement','constraints','input_spec','output_spec')]
        fields.extend(s['explanation'] for s in public['samples'])
        key=[problem.problem_id,public,self.c.translation_version,self.c.render_version,self.c.translate,self.c.judge_only,self.c.compact_layout,self.c.font_path,self.c.image_page_height]
        if any(FENCED_CODE.search(value or '') for value in fields):
            key.append(STATEMENT_RENDER_SCHEMA)
        return self.cache_dir/(digest(key)+'.manifest.json')

    def _cached_images(self,problem):
        manifest=self._manifest(problem)
        try:
            files=json.loads(manifest.read_text('utf-8'))
            if files and all(valid_image(f) for f in files):return files
        except (ValueError,OSError):pass
        return None

    async def render(self,problem,umo='',refresh=False):
        # A cached cur must not wait behind another group's background LLM call.
        if not refresh:
            cached=self._cached_images(problem)
            if cached:return cached
        async with self.render_lock:
            for attempt in range(2):
                if self.closed:raise GameError('图片服务正在重载，请稍后重试。')
                try:return await self._render_once(problem,umo,refresh)
                except Exception as e:
                    if type(e).__name__!='TargetClosedError':raise
                    await self._reset_browser()
                    if attempt:raise GameError('图片浏览器意外关闭，已尝试恢复，请稍后重试。') from None

    async def _render_once(self,problem,umo='',refresh=False):
        manifest=self._manifest(problem)
        if manifest.exists() and not refresh:
            try:
                files=json.loads(manifest.read_text('utf-8'))
                if files and all(valid_image(f) for f in files):return files
            except (ValueError,OSError):pass
        p=await self.translation.translate(problem,umo,refresh)
        if isinstance(p,tuple):p=p[0]
        key=digest([p,self.c.render_version,self.c.compact_layout,self.c.font_path,self.c.image_page_height])
        browser=await self._browser()
        page=await browser.new_page(viewport={'width':980,'height':1},device_scale_factor=1)
        # 页面完全自包含，题面内任何远程资源均不可加载。
        await page.route('**/*',lambda route:route.abort())
        files=[]
        try:
            for samples_page in (False,True):
                html=self.html(p,samples_page)
                await page.set_content(html,wait_until='load')
                await page.wait_for_function('window.__mathReady === true')
                await page.evaluate('document.fonts.ready')
                error=await page.evaluate('window.__mathError')
                if error or await page.locator('.katex-error').count():raise RenderContentError('公式渲染失败，拒绝发送不完整题面。')
                height=await page.evaluate('document.documentElement.scrollHeight')
                if height>100000:raise RenderContentError('题面过长，需管理员拆分后导入。')
                if await page.evaluate('document.documentElement.scrollWidth')>980:raise RenderContentError('题面存在超宽公式，需维护者调整换行。')
                blob=await page.screenshot(full_page=True,type='png',animations='disabled')
                stem=f'{key}-'+('samples' if samples_page else 'statement')
                await asyncio.to_thread(atomic_write,self.cache_dir/(stem+'.html'),html)
                out=await asyncio.to_thread(self._save_pages,blob,stem)
                files.extend(out)
        finally:
            try:await page.close()
            except Exception as e:
                if type(e).__name__!='TargetClosedError':raise
        if not all(valid_image(f) for f in files):raise GameError('图片校验失败，请管理员检查字体与浏览器。')
        await asyncio.to_thread(atomic_write,manifest,json.dumps(files))
        return files

    def _save_pages(self,blob,stem):
        im=Image.open(io.BytesIO(blob)).convert('RGB');files=[];top=0;limit=max(1000,self.c.image_page_height)
        while top<im.height:
            bottom=min(im.height,top+limit)
            if bottom<im.height:
                # 在真实空白扫描线分页；忽略左侧装饰条，绝不切断文字/公式/代码行。
                for y in range(bottom,max(top+200,bottom-1000),-1):
                    if im.height-y<150:continue
                    row=im.crop((32,y,960,y+1))
                    # 代码框内有两条浅色竖边；允许背景/边框颜色，仍拒绝任何文字笔画。
                    allowed={(255,255,255),(229,231,235),(246,243,236),(253,252,248),(217,220,215),(214,221,213),(220,224,216)}
                    pixels=getattr(row,'get_flattened_data',row.getdata)()
                    if all(pixel in allowed for pixel in pixels):bottom=y;break
                else:raise RenderContentError('未找到安全分页位置，请调整 image_page_height。')
            path=self.cache_dir/f'{stem}-{len(files)+1}.png'
            tmp=path.with_suffix('.tmp.png');im.crop((0,top,980,bottom)).save(tmp);os.replace(tmp,path)
            files.append(str(path.resolve()));top=bottom
        return files

    async def _reset_browser(self):
        async with self.lock:
            browser,pw=self.browser,self.pw
            self.browser=None;self.pw=None
            if browser:
                try:await browser.close()
                except Exception:pass
            if pw:
                try:await pw.stop()
                except Exception:pass

    async def close(self):
        self.closed=True
        await self._reset_browser()



class RankRenderer:
    def __init__(self, config, cache_dir, fetcher=None):
        self.c,self.dir,self.fetcher=config,Path(cache_dir),fetcher
        self.dir.mkdir(parents=True,exist_ok=True);self.lock=asyncio.Lock();self.avatar_slots=asyncio.Semaphore(8)

    def font(self,size):
        candidates=[self.c.font_path,'C:/Windows/Fonts/msyh.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
        for p in candidates:
            if p and Path(p).exists():return ImageFont.truetype(p,size)
        raise GameError('排行榜缺少中文字体，请配置 font_path。')

    async def _avatar(self,uid):
        # QQ 数字 ID 限定，URL 不能由玩家任意指定。
        path=self.dir/('avatar-'+digest(uid)+'.png')
        checked=self.dir/('avatar-'+digest(uid)+'.checked')
        fresh=checked.exists() and time.time()-checked.stat().st_mtime<self.c.avatar_ttl_hours*3600
        if self.fetcher and uid.isdigit() and not fresh:
            try:
                async with self.avatar_slots:
                    r=await self.fetcher.client.get(f'https://q1.qlogo.cn/g?b=qq&nk={uid}&s=100',timeout=3)
                r.raise_for_status()
                if len(r.content)>2_000_000:raise ValueError()
                with Image.open(io.BytesIO(r.content)) as im:
                    im.convert('RGB').resize((72,72)).save(path)
            except Exception:pass
            finally:checked.touch()  # 包括失败也限频，避免榜单每次等待失效头像接口。
        return path if path.exists() else None

    async def render(self,room_id,rows):
        async with self.lock:
            avatars=await asyncio.gather(*(self._avatar(r['user_id']) for r in rows))
            import hashlib
            key=digest([room_id,rows,[(str(p),hashlib.sha256(p.read_bytes()).hexdigest()) if p else None for p in avatars],self.c.render_version,self.c.font_path])
            manifests=self.dir/(key+'.json')
            if manifests.exists():
                paths=json.loads(manifests.read_text('utf-8'))
                if all(valid_image(p) for p in paths):return paths
            paths=await asyncio.to_thread(self._draw,key,rows,avatars)
            await asyncio.to_thread(atomic_write,manifests,json.dumps(paths))
            return paths

    def _draw(self,key,rows,avatars):
        paths=[]
        for start in range(0,max(1,len(rows)),50):
            part=rows[start:start+50];im=Image.new('RGB',(980,200+110*len(part)), '#F6F3EC');d=ImageDraw.Draw(im)
            d.rectangle((0,0,8,im.height),fill='#2F5D50');d.text((38,24),'口胡 Rating · 群排行榜',font=self.font(32),fill='#2F5D50')
            d.text((140,87),'玩家',font=self.font(20),fill='#68756b');d.text((570,87),'计分通过',font=self.font(20),fill='#68756b');d.text((770,87),'Rating',font=self.font(20),fill='#68756b')
            for i,r in enumerate(part):
                y=130+i*110;d.text((30,y+18),str(start+i+1),font=self.font(25),fill='#2F5D50')
                p=avatars[start+i]
                if p:
                    with Image.open(p) as a:im.paste(a.resize((72,72)),(85,y))
                else:d.ellipse((85,y,157,y+72),fill='#dce5dd')
                name=r['room_name'] or r['display_name'] or r['user_id']
                font=self.font(25)
                while len(name)>1 and d.textlength(name,font=font)>350:name=name[:-2]+'…'
                d.text((180,y+18),name,font=font,fill='#29382f');d.text((620,y+18),str(r['scored_solve_count']),font=font,fill='#29382f')
                d.text((775,y+18),str(r['rating']),font=self.font(29),fill='#2F5D50')
            if not rows:d.text((40,132),'暂无计分通过的玩家',font=self.font(23),fill='#667368')
            path=self.dir/f'rank-{key}-{start//50}.png';im.save(path);paths.append(str(path.resolve()))
        return paths
