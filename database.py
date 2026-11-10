"""SQLite WAL。写入用 BEGIN IMMEDIATE，所有 I/O 在工作线程内串行执行。"""
import asyncio
import json
import sqlite3
import time
from pathlib import Path
from .models import Problem, GameError

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(
 platform_id TEXT NOT NULL,user_id TEXT NOT NULL,display_name TEXT NOT NULL,
 rating INTEGER NOT NULL DEFAULT 1200,scored_solve_count INTEGER NOT NULL DEFAULT 0,
 rating_version TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(platform_id,user_id));
CREATE TABLE IF NOT EXISTS problems(
 problem_id TEXT PRIMARY KEY,platform TEXT NOT NULL,title TEXT NOT NULL,
 original_rating REAL NOT NULL,normalized_rating INTEGER NOT NULL,statement TEXT NOT NULL,
 official_editorial TEXT NOT NULL,editorial_url TEXT NOT NULL,cache_status TEXT NOT NULL,
 updated_at REAL NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rooms(
 room_id TEXT PRIMARY KEY,room_type TEXT NOT NULL,platform_id TEXT NOT NULL,
 current_problem_id TEXT REFERENCES problems(problem_id),current_generation INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS room_rounds(
 round_id INTEGER PRIMARY KEY AUTOINCREMENT,room_id TEXT NOT NULL REFERENCES rooms(room_id),
 problem_id TEXT NOT NULL REFERENCES problems(problem_id),generation INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','solved','given_up','closed')),
 first_blood_user_id TEXT,started_at REAL NOT NULL,finished_at REAL,problem_snapshot TEXT NOT NULL,UNIQUE(room_id,generation));
CREATE TABLE IF NOT EXISTS submissions(
 submission_id INTEGER PRIMARY KEY AUTOINCREMENT,round_id INTEGER NOT NULL REFERENCES room_rounds(round_id),
 room_id TEXT NOT NULL REFERENCES rooms(room_id),platform_id TEXT NOT NULL,user_id TEXT NOT NULL,
 message_id TEXT NOT NULL,content TEXT NOT NULL,verdict TEXT NOT NULL DEFAULT 'PENDING',
 feedback TEXT NOT NULL DEFAULT '',judge_model TEXT NOT NULL DEFAULT '',submitted_at REAL NOT NULL,
 FOREIGN KEY(platform_id,user_id) REFERENCES users(platform_id,user_id),UNIQUE(room_id,message_id));
CREATE TABLE IF NOT EXISTS solves(
 solve_id INTEGER PRIMARY KEY AUTOINCREMENT,platform_id TEXT NOT NULL,user_id TEXT NOT NULL,
 problem_id TEXT NOT NULL REFERENCES problems(problem_id),room_id TEXT NOT NULL REFERENCES rooms(room_id),
 round_id INTEGER NOT NULL REFERENCES room_rounds(round_id),scored INTEGER NOT NULL,
 rating_before INTEGER NOT NULL,rating_after INTEGER NOT NULL,difficulty INTEGER NOT NULL,
 rating_version TEXT NOT NULL,created_at REAL NOT NULL,
 FOREIGN KEY(platform_id,user_id) REFERENCES users(platform_id,user_id),UNIQUE(round_id,user_id));
CREATE UNIQUE INDEX IF NOT EXISTS one_scored_problem ON solves(platform_id,user_id,problem_id) WHERE scored=1;
CREATE UNIQUE INDEX IF NOT EXISTS one_first_blood ON solves(round_id) WHERE scored=1;
CREATE TABLE IF NOT EXISTS activity_events(
 room_id TEXT NOT NULL REFERENCES rooms(room_id),platform_id TEXT NOT NULL,user_id TEXT NOT NULL,command TEXT NOT NULL,timestamp REAL NOT NULL,
 FOREIGN KEY(platform_id,user_id) REFERENCES users(platform_id,user_id));
CREATE INDEX IF NOT EXISTS activity_recent ON activity_events(room_id,timestamp);
CREATE TABLE IF NOT EXISTS chat_sessions(
 room_id TEXT NOT NULL REFERENCES rooms(room_id),platform_id TEXT NOT NULL,user_id TEXT NOT NULL,messages TEXT NOT NULL,updated_at REAL NOT NULL,
 FOREIGN KEY(platform_id,user_id) REFERENCES users(platform_id,user_id),PRIMARY KEY(room_id,user_id));
CREATE TABLE IF NOT EXISTS room_members(
 room_id TEXT NOT NULL REFERENCES rooms(room_id),platform_id TEXT NOT NULL,user_id TEXT NOT NULL,display_name TEXT NOT NULL,
 FOREIGN KEY(platform_id,user_id) REFERENCES users(platform_id,user_id),PRIMARY KEY(room_id,user_id));
CREATE TABLE IF NOT EXISTS room_defaults(
 room_id TEXT PRIMARY KEY REFERENCES rooms(room_id),platform TEXT NOT NULL,low INTEGER NOT NULL,high INTEGER NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sync_state(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS download_jobs(
 problem_id TEXT PRIMARY KEY,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,last_attempt REAL NOT NULL,
 next_retry REAL NOT NULL DEFAULT 0,error_kind TEXT NOT NULL DEFAULT '',source TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS late_submissions(
 late_id INTEGER PRIMARY KEY AUTOINCREMENT,round_id INTEGER NOT NULL REFERENCES room_rounds(round_id),
 room_id TEXT NOT NULL REFERENCES rooms(room_id),platform_id TEXT NOT NULL,user_id TEXT NOT NULL,
 message_id TEXT NOT NULL,content TEXT NOT NULL,verdict TEXT NOT NULL DEFAULT 'PENDING',
 feedback TEXT NOT NULL DEFAULT '',judge_model TEXT NOT NULL DEFAULT '',submitted_at REAL NOT NULL,
 FOREIGN KEY(platform_id,user_id) REFERENCES users(platform_id,user_id),UNIQUE(room_id,message_id));
CREATE INDEX IF NOT EXISTS late_history ON late_submissions(round_id,user_id,late_id);
CREATE TABLE IF NOT EXISTS prepared_problems(
 room_id TEXT PRIMARY KEY REFERENCES rooms(room_id),token TEXT NOT NULL,signature TEXT NOT NULL,
 problem_id TEXT NOT NULL REFERENCES problems(problem_id),snapshot TEXT NOT NULL,images TEXT NOT NULL);
PRAGMA user_version=5;
'''


class Database:
    def __init__(self, path, config):
        self.path = Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.config = config; self.lock = asyncio.Lock()

    def _connect(self):
        c = sqlite3.connect(self.path,timeout=30)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=30000')
        return c

    async def run(self, fn, write=False):
        def work():
            c=self._connect()
            try:
                if write: c.execute('BEGIN IMMEDIATE')
                result=fn(c)
                c.commit()
                return result
            except BaseException:
                c.rollback(); raise
            finally:
                c.close()
        async with self.lock:
            return await asyncio.to_thread(work)

    async def initialize(self):
        def init(c):
            c.execute('PRAGMA journal_mode=WAL')
            version=c.execute('PRAGMA user_version').fetchone()[0]
            if version>5: raise RuntimeError('数据库来自更新版本，拒绝降级写入')
            if version==1:
                c.execute("ALTER TABLE room_rounds ADD COLUMN problem_snapshot TEXT NOT NULL DEFAULT ''")
                c.execute('UPDATE room_rounds SET problem_snapshot=(SELECT payload FROM problems p WHERE p.problem_id=room_rounds.problem_id)')
            c.executescript(SCHEMA)
            c.execute("UPDATE submissions SET verdict='UNCERTAIN', feedback='判题被重载中断，请用新消息重新提交。' WHERE verdict='PENDING'")
            c.execute("UPDATE late_submissions SET verdict='UNCERTAIN',feedback='补交判题被重载中断，请用新消息重新 late。' WHERE verdict='PENDING'")
        await self.run(init)

    async def touch(self, who, command):
        def op(c):
            now=time.time()
            c.execute('INSERT INTO users VALUES(?,?,?,1200,0,?,?) ON CONFLICT(platform_id,user_id) DO UPDATE SET display_name=excluded.display_name',
                      (who.platform_id,who.user_id,who.display_name,self.config.rating_version,now))
            c.execute('INSERT OR IGNORE INTO rooms VALUES(?,?,?,NULL,0,?)',(who.room_id,who.room_type,who.platform_id,now))
            c.execute('INSERT INTO room_members VALUES(?,?,?,?) ON CONFLICT(room_id,user_id) DO UPDATE SET display_name=excluded.display_name',
                      (who.room_id,who.platform_id,who.user_id,who.display_name))
            if command in {'new','cur','submit','giveup'}:
                c.execute('INSERT INTO activity_events VALUES(?,?,?,?,?)',(who.room_id,who.platform_id,who.user_id,command,now))
                c.execute('DELETE FROM activity_events WHERE timestamp<?',(now-7*86400,))
        await self.run(op,True)

    async def add_problem(self, p):
        await self.run(lambda c:c.execute('''INSERT INTO problems VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(problem_id) DO UPDATE SET title=excluded.title,original_rating=excluded.original_rating,
          normalized_rating=excluded.normalized_rating,statement=excluded.statement,official_editorial=excluded.official_editorial,
          editorial_url=excluded.editorial_url,cache_status=excluded.cache_status,updated_at=excluded.updated_at,payload=excluded.payload''',
          (p.problem_id,p.platform,p.title,p.original_rating,p.normalized_rating,p.statement,p.official_editorial,p.editorial_url,
           'ready' if p.verified else 'unverified',p.updated_at,p.dump())),True)

    async def problem(self, problem_id):
        row=await self.run(lambda c:c.execute('SELECT payload FROM problems WHERE problem_id=?',(problem_id,)).fetchone())
        return Problem.load(row[0]) if row else None

    async def state(self,key,value=None):
        if value is None:
            row=await self.run(lambda c:c.execute('SELECT value FROM sync_state WHERE key=?',(key,)).fetchone())
            return json.loads(row[0]) if row else None
        await self.run(lambda c:c.execute('INSERT INTO sync_state VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(key,json.dumps(value),time.time())),True)

    async def download_ready(self,problem_id,source='official'):
        def op(c):
            if c.execute("SELECT 1 FROM problems WHERE problem_id=? AND cache_status IN ('ready','render_error')",(problem_id,)).fetchone():return False
            job=c.execute('SELECT next_retry,source FROM download_jobs WHERE problem_id=?',(problem_id,)).fetchone()
            return not job or job[1]!=source or job[0]<=time.time()
        return await self.run(op)

    async def record_download(self,problem_id,success,error_kind='',source=''):
        now=time.time();retry=0 if success else now+self.config.download_retry_hours*3600
        await self.run(lambda c:c.execute('''INSERT INTO download_jobs VALUES(?,?,1,?,?,?,?) ON CONFLICT(problem_id) DO UPDATE SET
          state=excluded.state,attempts=download_jobs.attempts+1,last_attempt=excluded.last_attempt,next_retry=excluded.next_retry,error_kind=excluded.error_kind,source=excluded.source''',
          (problem_id,'ready' if success else 'failed',now,retry,error_kind,source)),True)

    async def render_status(self,problem_id,ready):
        await self.run(lambda c:c.execute('UPDATE problems SET cache_status=? WHERE problem_id=?',('ready' if ready else 'render_error',problem_id)),True)

    async def set_default(self,who,platform,low,high):
        await self.run(lambda c:c.execute('INSERT INTO room_defaults VALUES(?,?,?,?,?) ON CONFLICT(room_id) DO UPDATE SET platform=excluded.platform,low=excluded.low,high=excluded.high,updated_at=excluded.updated_at',
                         (who.room_id,platform,low,high,time.time())),True)

    async def room_default(self,room_id):
        return await self.run(lambda c:_dict(c.execute('SELECT * FROM room_defaults WHERE room_id=?',(room_id,)).fetchone()))

    async def ensure_room(self,who):
        await self.run(lambda c:c.execute('INSERT OR IGNORE INTO rooms VALUES(?,?,?,NULL,0,?)',(who.room_id,who.room_type,who.platform_id,time.time())),True)

    async def prepared(self,room_id):
        return await self.run(lambda c:_dict(c.execute('SELECT * FROM prepared_problems WHERE room_id=?',(room_id,)).fetchone()))

    async def discard_prepared(self,room_id):
        await self.run(lambda c:c.execute('DELETE FROM prepared_problems WHERE room_id=?',(room_id,)),True)

    async def save_prepared(self,record):
        def op(c):
            latest=c.execute('SELECT problem_id FROM room_rounds WHERE room_id=? ORDER BY round_id DESC LIMIT 1',(record['room_id'],)).fetchone()
            if latest and latest[0]==record['problem_id']:return False
            c.execute('INSERT OR REPLACE INTO prepared_problems VALUES(?,?,?,?,?,?)',tuple(record[k] for k in ('room_id','token','signature','problem_id','snapshot','images')))
            return True
        return await self.run(op,True)

    async def current(self, room_id):
        return await self.run(lambda c: _dict(c.execute('''SELECT rr.* FROM room_rounds rr JOIN rooms r ON r.room_id=rr.room_id
          AND r.current_generation=rr.generation WHERE r.room_id=? AND r.current_problem_id IS NOT NULL''',(room_id,)).fetchone()))

    async def start_round(self, who, problem_id, prepared_token=None):
        def op(c):
            r=c.execute('SELECT * FROM rooms WHERE room_id=?',(who.room_id,)).fetchone()
            prev=c.execute('SELECT * FROM room_rounds WHERE room_id=? AND generation=?',(who.room_id,r['current_generation'])).fetchone()
            if prev and prev['status']=='active': raise GameError('已有活动题，请先完成或 giveup。使用cur可查看当前题目与样例')
            if prev and prev['status']=='solved': c.execute("UPDATE room_rounds SET status='closed' WHERE round_id=?",(prev['round_id'],))
            if prepared_token is not None:
                ready=c.execute('SELECT * FROM prepared_problems WHERE room_id=? AND token=? AND problem_id=?',(who.room_id,prepared_token,problem_id)).fetchone()
                if not ready:raise GameError('预备题已更新，请重试 new。')
            gen=r['current_generation']+1
            c.execute('UPDATE rooms SET current_problem_id=?,current_generation=?,updated_at=? WHERE room_id=?',(problem_id,gen,time.time(),who.room_id))
            snapshot=ready['snapshot'] if prepared_token is not None else c.execute('SELECT payload FROM problems WHERE problem_id=?',(problem_id,)).fetchone()[0]
            cursor=c.execute("INSERT INTO room_rounds(room_id,problem_id,generation,status,started_at,problem_snapshot) VALUES(?,?,?,'active',?,?)",(who.room_id,problem_id,gen,time.time(),snapshot))
            if prepared_token is not None:c.execute('DELETE FROM prepared_problems WHERE room_id=? AND token=?',(who.room_id,prepared_token))
            return cursor.lastrowid
        return await self.run(op,True)

    async def giveup(self, room_id):
        def op(c):
            r=c.execute('SELECT * FROM rooms WHERE room_id=?',(room_id,)).fetchone()
            if not r or not r['current_problem_id']: raise GameError('当前没有正在做的题。')
            c.execute("UPDATE room_rounds SET status=CASE WHEN status='solved' THEN 'closed' ELSE 'given_up' END,finished_at=? WHERE room_id=? AND generation=?",(time.time(),room_id,r['current_generation']))
            c.execute('UPDATE rooms SET current_problem_id=NULL,current_generation=current_generation+1,updated_at=? WHERE room_id=?',(time.time(),room_id))
        await self.run(op,True)

    async def reserve(self, who, round_, message_id, content):
        def op(c):
            old=c.execute('SELECT * FROM submissions WHERE room_id=? AND message_id=?',(who.room_id,message_id)).fetchone()
            if old: return dict(old),False
            late=c.execute('SELECT 1 FROM late_submissions WHERE room_id=? AND message_id=?',(who.room_id,message_id)).fetchone()
            if late:return {},False
            cur=c.execute('INSERT INTO submissions(round_id,room_id,platform_id,user_id,message_id,content,submitted_at) VALUES(?,?,?,?,?,?,?)',
                (round_['round_id'],who.room_id,who.platform_id,who.user_id,message_id,content,time.time()))
            return {'submission_id':cur.lastrowid},True
        return await self.run(op,True)

    async def history(self, round_id, user_id):
        return await self.run(lambda c:[dict(r) for r in c.execute("SELECT content,verdict FROM submissions WHERE round_id=? AND user_id=? AND verdict NOT IN ('PENDING','STALE') ORDER BY submission_id",(round_id,user_id))])

    async def latest_judged(self,round_id,user_id):
        return await self.run(lambda c:_dict(c.execute("SELECT content,verdict,feedback,judge_model FROM submissions WHERE round_id=? AND user_id=? AND verdict NOT IN ('PENDING','STALE') ORDER BY submission_id DESC LIMIT 1",(round_id,user_id)).fetchone()))

    async def finish(self, who, round_, sid, judgment, model, rating):
        def op(c):
            sub=c.execute('SELECT * FROM submissions WHERE submission_id=?',(sid,)).fetchone()
            if sub['verdict']!='PENDING': return {'duplicate':True,'feedback':sub['feedback']}
            r=c.execute('SELECT * FROM rooms WHERE room_id=?',(who.room_id,)).fetchone()
            rr=c.execute('SELECT * FROM room_rounds WHERE round_id=?',(round_['round_id'],)).fetchone()
            stale=r['current_generation']!=round_['generation'] or rr['status'] not in ('active','solved')
            verdict='STALE' if stale else judgment.verdict
            feedback='题目已放弃或切换，本次结果不计入。' if stale else judgment.feedback
            c.execute('UPDATE submissions SET verdict=?,feedback=?,judge_model=? WHERE submission_id=?',(verdict,feedback,model,sid))
            user=c.execute('SELECT * FROM users WHERE platform_id=? AND user_id=?',(who.platform_id,who.user_id)).fetchone()
            before=after=user['rating']; scored=False
            if verdict=='AC':
                previous=c.execute('SELECT 1 FROM solves WHERE platform_id=? AND user_id=? AND problem_id=? AND scored=1',(who.platform_id,who.user_id,rr['problem_id'])).fetchone()
                already=c.execute('SELECT 1 FROM solves WHERE round_id=? AND user_id=?',(rr['round_id'],who.user_id)).fetchone()
                # 首个 AC 占用首杀，即使该用户历史已做过该题，也不把首杀转给后来者。
                first=rr['first_blood_user_id'] is None
                if first:
                    c.execute("UPDATE room_rounds SET first_blood_user_id=?,status='solved',finished_at=? WHERE round_id=? AND first_blood_user_id IS NULL",(who.user_id,time.time(),rr['round_id']))
                scored=first and not previous and not already
                difficulty=Problem.load(rr['problem_snapshot']).normalized_rating
                if scored:
                    after=rating.next(before,user['scored_solve_count'],difficulty)
                    c.execute('UPDATE users SET rating=?,scored_solve_count=scored_solve_count+1,rating_version=? WHERE platform_id=? AND user_id=?',(after,self.config.rating_version,who.platform_id,who.user_id))
                if not already and (scored or self.config.record_practice):
                    c.execute('''INSERT INTO solves(platform_id,user_id,problem_id,room_id,round_id,scored,rating_before,rating_after,difficulty,rating_version,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(who.platform_id,who.user_id,rr['problem_id'],who.room_id,rr['round_id'],int(scored),before,after,difficulty,self.config.rating_version,time.time()))
            return {'verdict':verdict,'feedback':feedback,'scored':scored,'rating':after,'delta':after-before}
        return await self.run(op,True)

    async def late_target(self,who,problem_id=None):
        def op(c):
            current=c.execute('SELECT rr.* FROM room_rounds rr JOIN rooms r ON r.room_id=rr.room_id AND r.current_generation=rr.generation WHERE r.room_id=? AND r.current_problem_id IS NOT NULL',(who.room_id,)).fetchone()
            if problem_id:
                row=c.execute("SELECT * FROM room_rounds WHERE room_id=? AND lower(problem_id)=lower(?) AND status IN ('solved','given_up','closed') ORDER BY round_id DESC LIMIT 1",(who.room_id,problem_id)).fetchone()
            else:
                if not current or current['status']!='active':
                    raise GameError('当前没有新开的活动题，请用 late 题号 做法 指定补交题目。')
                row=c.execute("SELECT * FROM room_rounds WHERE room_id=? AND round_id<? AND status IN ('solved','given_up','closed') ORDER BY round_id DESC LIMIT 1",(who.room_id,current['round_id'])).fetchone()
            if not row:raise GameError('本房间没有对应的已通过或已放弃题目；当前活动题请用 submit。')
            return dict(row)
        return await self.run(op)

    async def reserve_late(self,who,target,message_id,content):
        def op(c):
            if c.execute('SELECT 1 FROM submissions WHERE room_id=? AND message_id=?',(who.room_id,message_id)).fetchone() or c.execute('SELECT 1 FROM late_submissions WHERE room_id=? AND message_id=?',(who.room_id,message_id)).fetchone():return None
            row=c.execute('SELECT * FROM room_rounds WHERE round_id=? AND room_id=?',(target['round_id'],who.room_id)).fetchone()
            if not row or row['status'] not in ('solved','given_up','closed'):raise GameError('该题尚未结束，请用 submit。')
            return c.execute('INSERT INTO late_submissions(round_id,room_id,platform_id,user_id,message_id,content,submitted_at) VALUES(?,?,?,?,?,?,?)',(target['round_id'],who.room_id,who.platform_id,who.user_id,message_id,content,time.time())).lastrowid
        return await self.run(op,True)

    async def late_history(self,round_id,user_id):
        return await self.run(lambda c:[dict(r) for r in c.execute("""SELECT content,verdict FROM (
          SELECT content,verdict,submitted_at,0 AS source,submission_id AS id FROM submissions WHERE round_id=? AND user_id=?
          UNION ALL SELECT content,verdict,submitted_at,1 AS source,late_id AS id FROM late_submissions WHERE round_id=? AND user_id=?)
          WHERE verdict NOT IN ('PENDING','STALE') ORDER BY submitted_at,source,id""",(round_id,user_id,round_id,user_id))])

    async def finish_late(self,who,target,sid,j,model):
        def op(c):
            sub=c.execute('SELECT * FROM late_submissions WHERE late_id=? AND room_id=? AND user_id=?',(sid,who.room_id,who.user_id)).fetchone()
            if not sub or sub['verdict']!='PENDING':return False
            c.execute('UPDATE late_submissions SET verdict=?,feedback=?,judge_model=? WHERE late_id=?',(j.verdict,j.feedback,model,sid))
            if j.verdict=='AC' and self.config.record_practice:
                rating=c.execute('SELECT rating FROM users WHERE platform_id=? AND user_id=?',(who.platform_id,who.user_id)).fetchone()[0]
                difficulty=Problem.load(target['problem_snapshot']).normalized_rating
                c.execute('INSERT OR IGNORE INTO solves(platform_id,user_id,problem_id,room_id,round_id,scored,rating_before,rating_after,difficulty,rating_version,created_at) VALUES(?,?,?,?,?,0,?,?,?,?,?)',(who.platform_id,who.user_id,target['problem_id'],who.room_id,target['round_id'],rating,rating,difficulty,self.config.rating_version,time.time()))
            return True
        return await self.run(op,True)

    async def chat(self, who, messages=None):
        if messages is None:
            row=await self.run(lambda c:c.execute('SELECT messages FROM chat_sessions WHERE room_id=? AND user_id=?',(who.room_id,who.user_id)).fetchone())
            return json.loads(row[0]) if row else []
        await self.run(lambda c:c.execute('INSERT INTO chat_sessions VALUES(?,?,?,?,?) ON CONFLICT(room_id,user_id) DO UPDATE SET messages=excluded.messages,updated_at=excluded.updated_at',
                      (who.room_id,who.platform_id,who.user_id,json.dumps(messages,ensure_ascii=False),time.time())),True)

    async def leaderboard(self, room_id):
        return await self.run(lambda c:[dict(r) for r in c.execute('''SELECT u.*,m.display_name AS room_name,
          (SELECT COUNT(*) FROM solves s WHERE s.room_id=m.room_id AND s.user_id=m.user_id AND s.scored=1) AS room_solves
          FROM room_members m JOIN users u ON u.platform_id=m.platform_id AND u.user_id=m.user_id
          WHERE m.room_id=? AND EXISTS(SELECT 1 FROM solves s WHERE s.room_id=m.room_id AND s.user_id=m.user_id AND s.scored=1)
          ORDER BY u.rating DESC,u.scored_solve_count DESC,u.user_id''',(room_id,))])

    async def refresh_member_names(self, room_id, members):
        def op(c):
            for user_id,name in members:
                if name:
                    c.execute('UPDATE room_members SET display_name=? WHERE room_id=? AND user_id=?',(str(name),room_id,str(user_id)))
        await self.run(op,True)

    async def recalculate(self, strategy):
        def op(c):
            c.execute('UPDATE users SET rating=1200,scored_solve_count=0,rating_version=?',(self.config.rating_version,))
            for s in c.execute('SELECT * FROM solves WHERE scored=1 ORDER BY solve_id').fetchall():
                u=c.execute('SELECT * FROM users WHERE platform_id=? AND user_id=?',(s['platform_id'],s['user_id'])).fetchone()
                after=strategy.next(u['rating'],u['scored_solve_count'],s['difficulty'])
                c.execute('UPDATE solves SET rating_before=?,rating_after=?,rating_version=? WHERE solve_id=?',(u['rating'],after,self.config.rating_version,s['solve_id']))
                c.execute('UPDATE users SET rating=?,scored_solve_count=scored_solve_count+1 WHERE platform_id=? AND user_id=?',(after,s['platform_id'],s['user_id']))
        await self.run(op,True)

    async def backup(self, destination):
        def op(c):
            with sqlite3.connect(destination) as target: c.backup(target)
        await self.run(op)


def _dict(row):
    return dict(row) if row else None
