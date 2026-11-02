"""任务状态存在本地 SQLite：每页一条记录，状态落盘，中断后接着跑。"""
import sqlite3, threading, time
from .conf import DATA

_lock = threading.Lock()
SCHEMA = """
create table if not exists profile(
  id integer primary key check(id=1), name text, photo text, voice_sample text,
  voice_id text, tts_model text, ref_audio text, consent_at text, consent_text text);
create table if not exists lessons(
  id integer primary key, title text, pptx text, created_at text, render text);
create table if not exists pages(
  id integer primary key, lesson_id int, idx int, title text, text text, notes text,
  script text default '', on_camera int default 1, status text default 'new', error text default '',
  audio text default '', audio_s real default 0, avatar text default '', clip text default '',
  face_sim real, voice_sim real, flag text default '', updated_at text,
  unique(lesson_id, idx));
create table if not exists lexicon(term text primary key, reading text);
create table if not exists ledger(
  id integer primary key, ts text, kind text, lesson_id int, page_id int, units real, cost real, note text);
create table if not exists kv(k text primary key, v text);
"""


def conn():
    c = sqlite3.connect(DATA / "mooc.db", timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


# 后加的列：旧数据库自动补上
COLUMNS = {"profile": {"base_video": "text", "base_loop": "text", "loop_sec": "real", "base_hash": "text", "ref_frame": "text"},
           "pages": {"timeline": "text default ''", "base_offset": "real", "gpu_s": "real default 0"},
           "lessons": {"start_cursor": "real"}}

with conn() as _c:
    _c.executescript(SCHEMA)
    for t, cols in COLUMNS.items():
        have = {r[1] for r in _c.execute(f"pragma table_info({t})")}
        for k, typ in cols.items():
            if k not in have: _c.execute(f"alter table {t} add column {k} {typ}")


def now(): return time.strftime("%Y-%m-%d %H:%M:%S")
def q(sql, *a):
    with conn() as c: return [dict(r) for r in c.execute(sql, a)]
def one(sql, *a): r = q(sql, *a); return r[0] if r else None
def run(sql, *a):
    with _lock, conn() as c: return c.execute(sql, a).lastrowid


def profile(): return one("select * from profile where id=1") or {}
def set_profile(**kw):
    run("insert or ignore into profile(id) values(1)")
    for k, v in kw.items(): run(f"update profile set {k}=? where id=1", v)


def page(pid): return one("select * from pages where id=?", pid)
def pages(lid): return q("select * from pages where lesson_id=? order by idx", lid)
def set_page(pid, **kw):
    kw["updated_at"] = now()
    run("update pages set " + ",".join(f"{k}=?" for k in kw) + " where id=?", *kw.values(), pid)


def lexicon(): return {r["term"]: r["reading"] for r in q("select * from lexicon order by length(term) desc")}
def kv(k, d=None): r = one("select v from kv where k=?", k); return r["v"] if r else d
def set_kv(k, v): run("insert into kv(k,v) values(?,?) on conflict(k) do update set v=excluded.v", k, str(v))


def charge(kind, units, cost, lesson_id=None, page_id=None, note=""):
    run("insert into ledger(ts,kind,lesson_id,page_id,units,cost,note) values(?,?,?,?,?,?,?)",
        now(), kind, lesson_id, page_id, units, cost, note)
def spent(): return (one("select coalesce(sum(cost),0) s from ledger")["s"]) or 0.0
