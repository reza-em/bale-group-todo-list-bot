"""SQLite storage: one active list per chat (older lists are archived by /new)."""
import os, sqlite3, time, threading

_PATH = os.environ.get("TODO_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "todo.db"))
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS lists(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '📋 لیست کارها',
  message_id INTEGER,
  creator_id INTEGER,
  locked INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  celebrated INTEGER NOT NULL DEFAULT 0,
  created_at REAL, updated_at REAL);
CREATE INDEX IF NOT EXISTS lists_chat ON lists(chat_id, active);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  pos INTEGER NOT NULL,
  text TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  done_by TEXT, done_at REAL,
  created_by INTEGER, created_at REAL);
CREATE INDEX IF NOT EXISTS tasks_list ON tasks(list_id, pos);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path=None):
    global _PATH
    if path:
        _PATH = path
        old = getattr(_local, "c", None)
        if old is not None:
            old.close()
        _local.c = None
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        if _PATH != ":memory:":
            c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        _local.c = c
    return c


def q(sql, args=()):
    return connect().execute(sql, args).fetchall()


def x(sql, args=()):
    c = connect()
    cur = c.execute(sql, args)
    c.commit()
    return cur


# --- lists ---
def active_list(chat_id):
    r = q("SELECT * FROM lists WHERE chat_id=? AND active=1 ORDER BY id DESC LIMIT 1", (chat_id,))
    return r[0] if r else None


def get_list(list_id):
    r = q("SELECT * FROM lists WHERE id=?", (list_id,))
    return r[0] if r else None


def create_list(chat_id, creator_id, title=None):
    now = time.time()
    x("UPDATE lists SET active=0 WHERE chat_id=? AND active=1", (chat_id,))
    cur = x("INSERT INTO lists(chat_id,title,creator_id,created_at,updated_at) VALUES(?,?,?,?,?)",
            (chat_id, title or "📋 لیست کارها", creator_id, now, now))
    return get_list(cur.lastrowid)


def ensure_list(chat_id, creator_id):
    return active_list(chat_id) or create_list(chat_id, creator_id)


def set_list(list_id, **kw):
    if not kw:
        return
    cols = ", ".join("%s=?" % k for k in kw)
    x("UPDATE lists SET %s WHERE id=?" % cols, (*kw.values(), list_id))


def touch(list_id):
    set_list(list_id, updated_at=time.time())


# --- tasks ---
def tasks(list_id):
    return q("SELECT * FROM tasks WHERE list_id=? ORDER BY pos, id", (list_id,))


def add_tasks(list_id, items, user_id=None, done_by=None):
    """items: list of str or (text, done)."""
    rows = tasks(list_id)
    pos = (rows[-1]["pos"] if rows else 0)
    now = time.time()
    c = connect()
    for it in items:
        text, done = (it, False) if isinstance(it, str) else it
        pos += 1
        c.execute("INSERT INTO tasks(list_id,pos,text,done,done_by,done_at,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (list_id, pos, text, 1 if done else 0, done_by if done else None, now if done else None, user_id, now))
    c.commit()
    touch(list_id)


def task_by_num(list_id, n):
    rows = tasks(list_id)
    return rows[n - 1] if 1 <= n <= len(rows) else None


def get_task(task_id):
    r = q("SELECT * FROM tasks WHERE id=?", (task_id,))
    return r[0] if r else None


def set_done(task_id, done, who=None):
    x("UPDATE tasks SET done=?, done_by=?, done_at=? WHERE id=?",
      (1 if done else 0, who if done else None, time.time() if done else None, task_id))


def edit_task(task_id, text):
    x("UPDATE tasks SET text=? WHERE id=?", (text, task_id))


def del_task(task_id):
    x("DELETE FROM tasks WHERE id=?", (task_id,))


def clear_done(list_id):
    return x("DELETE FROM tasks WHERE list_id=? AND done=1", (list_id,)).rowcount


def clear_all(list_id):
    return x("DELETE FROM tasks WHERE list_id=?", (list_id,)).rowcount


# --- kv ---
def kv_get(key, default=None):
    r = q("SELECT value FROM kv WHERE key=?", (key,))
    return r[0]["value"] if r else default


def kv_set(key, value):
    x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
