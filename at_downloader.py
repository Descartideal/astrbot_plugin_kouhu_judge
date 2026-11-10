"""Persistent idle AtCoder scan; official editorials only, no model calls."""
import asyncio
import json
import time
from pathlib import Path
from .cache import atomic_write


class PacedFetcher:
    def __init__(self, fetcher, interval=1.0):
        self.fetcher, self.interval = fetcher, interval
        self.lock = asyncio.Lock()
        self.next_request = 0.0

    async def get(self, *args, **kwargs):
        async with self.lock:
            await asyncio.sleep(max(0, self.next_request-time.monotonic()))
            try:
                return await self.fetcher.get(*args, **kwargs)
            finally:
                self.next_request = time.monotonic()+self.interval


class AtCoderDownloader:
    CURSOR = 'at_scan_cursor:v1'

    def __init__(self, db, config, provider, cache_dir, idle=lambda: True, lock=None):
        self.db, self.c, self.provider, self.idle = db, config, provider, idle
        self.path = Path(cache_dir)/'at-metadata.json'
        self.lock = lock or asyncio.Lock()

    async def metadata(self):
        cached = None
        try:
            cached = json.loads(await asyncio.to_thread(self.path.read_text, encoding='utf-8'))
            if cached.get('schema') != 1 or (not isinstance(cached.get('problems'), list) or not cached['problems']):
                cached = None
        except (OSError, ValueError, TypeError):
            pass
        if cached and time.time()-cached['updated_at'] < self.c.metadata_ttl_hours*3600:
            return cached['problems']
        try:
            rows = await self.provider.metadata()
            if not rows:
                raise ValueError('empty AtCoder metadata')
            rows = sorted(rows, key=lambda x: (x['contest_id'].startswith('abc'), x['contest_id'], x['id']), reverse=True)
            await asyncio.to_thread(atomic_write, self.path, json.dumps({'schema':1, 'updated_at':time.time(), 'problems':rows}, ensure_ascii=False))
            return rows
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if getattr(e, 'status_code', None) == 429 or not cached:
                raise
            return cached['problems']

    async def harvest_page(self, force=False):
        async with self.lock:
            if (not force and not self.idle()) or time.time() < (await self.db.state('at_download_backoff_until') or 0):
                return 0, 0
            rows = await self.metadata()
            cursor = await self.db.state(self.CURSOR) or {}
            positions = {m['id']:i for i,m in enumerate(rows)}
            index = positions.get(cursor.get('next_id'), 0)
            loaded = failed = attempts = scanned = 0
            cycles = int(cursor.get('cycles', 0))
            while scanned < min(200, len(rows)) and attempts < self.c.at_download_batch_size:
                if not force and not self.idle():
                    break
                m = rows[index]
                pid = 'at:'+m['id']
                if self.c.download_low <= m['rating'] <= self.c.download_high and await self.db.download_ready(pid):
                    attempts += 1
                    try:
                        p = await self.provider.fetch_problem(m)
                        p.public()
                        if not p.verified:
                            raise ValueError('unverified editorial')
                        await self.db.add_problem(p)
                        await self.db.record_download(pid, True, source='official')
                        loaded += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await self.db.record_download(pid, False, type(e).__name__, 'official')
                        failed += 1
                        await self.db.state('at_download_last_error', {'kind':type(e).__name__, 'at':time.time(), 'problem_id':pid})
                        if getattr(e, 'status_code', None) == 429:
                            await self.db.state('at_download_backoff_until', time.time()+max(60, getattr(e, 'retry_after', 60)))
                            break
                scanned += 1
                index += 1
                if index == len(rows):
                    index = 0
                    cycles += 1
                await self.db.state(self.CURSOR, {'next_id':rows[index]['id'], 'last_id':m['id'], 'scanned':int(cursor.get('scanned',0))+scanned, 'cycles':cycles, 'metadata_count':len(rows)})
            await self.db.state('at_download_last_success', {'at':time.time(), 'loaded':loaded, 'skipped':failed, 'scanned':scanned})
            return loaded, failed
