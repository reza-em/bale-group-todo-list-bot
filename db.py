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
CREATE TABLE IF NOT EXISTS chats(
  chat_id INTEGER PRIMARY KEY, type TEXT, title TEXT,
  quiet_on INTEGER NOT NULL DEFAULT 1, quiet_start INTEGER NOT NULL DEFAULT 23, quiet_end INTEGER NOT NULL DEFAULT 7,
  due_mode TEXT NOT NULL DEFAULT 'both',          -- off | due | both (1h before + at due)
  nostart_day TEXT);
CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY, name TEXT, username TEXT,
  dm_ok INTEGER NOT NULL DEFAULT 0,               -- has /started the bot in private (DMs possible)
  muted INTEGER NOT NULL DEFAULT 0,
  quiet_on INTEGER NOT NULL DEFAULT 1, quiet_start INTEGER NOT NULL DEFAULT 23, quiet_end INTEGER NOT NULL DEFAULT 7);
CREATE TABLE IF NOT EXISTS members(
  chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, last_seen REAL,
  PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS reminders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                             -- daily | work | once
  minute INTEGER,                                 -- minute of day (daily/work)
  next_at REAL,                                   -- claim column: a fire = CAS on next_at
  active INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER, created_at REAL);
CREATE INDEX IF NOT EXISTS rem_next ON reminders(active, next_at);
CREATE TABLE IF NOT EXISTS due_sent(task_id INTEGER NOT NULL, kind TEXT NOT NULL, due_at REAL NOT NULL,
  PRIMARY KEY(task_id, kind, due_at));
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL, group_id INTEGER, text TEXT NOT NULL, markup TEXT,
  not_before REAL NOT NULL, claimed_at REAL, sent_at REAL, error TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox(claimed_at, not_before);
CREATE TABLE IF NOT EXISTS prompts(
  chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
  kind TEXT NOT NULL, list_id INTEGER, task_id INTEGER, user_id INTEGER, payload TEXT, created_at REAL,
  PRIMARY KEY(chat_id, message_id));
CREATE TABLE IF NOT EXISTS deletes(chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, at REAL NOT NULL,
  PRIMARY KEY(chat_id, message_id));
"""

MIGRATIONS = [  # (table, column, ddl) added to databases created by v1
    ("tasks", "assignee_id", "INTEGER"), ("tasks", "assignee_username", "TEXT"), ("tasks", "assignee_name", "TEXT"),
    ("tasks", "due_at", "REAL"), ("tasks", "due_has_time", "INTEGER NOT NULL DEFAULT 0"),
    ("tasks", "done_by_id", "INTEGER"),
    ("lists", "undo_json", "TEXT"), ("lists", "undo_at", "REAL"), ("lists", "undo_label", "TEXT"),
    ("lists", "undo_shown", "INTEGER NOT NULL DEFAULT 0"), ("lists", "locked_by", "INTEGER"),
]


def _migrate(c):
    for table, col, ddl in MIGRATIONS:
        cols = {r[1] for r in c.execute("PRAGMA table_info(%s)" % table)}
        if col not in cols:
            c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, ddl))
    c.commit()


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
        _migrate(c)
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
    ids = []
    for it in items:
        text, done = (it, False) if isinstance(it, str) else it
        pos += 1
        cur = c.execute("INSERT INTO tasks(list_id,pos,text,done,done_by,done_at,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (list_id, pos, text, 1 if done else 0, done_by if done else None, now if done else None, user_id, now))
        ids.append(cur.lastrowid)
    c.commit()
    touch(list_id)
    return ids


def task_by_num(list_id, n):
    rows = tasks(list_id)
    return rows[n - 1] if 1 <= n <= len(rows) else None


def get_task(task_id):
    r = q("SELECT * FROM tasks WHERE id=?", (task_id,))
    return r[0] if r else None


def set_done(task_id, done, who=None, who_id=None, now=None):
    x("UPDATE tasks SET done=?, done_by=?, done_by_id=?, done_at=? WHERE id=?",
      (1 if done else 0, who if done else None, who_id if done else None,
       (now or time.time()) if done else None, task_id))


def set_task(task_id, **kw):
    cols = ", ".join("%s=?" % k for k in kw)
    x("UPDATE tasks SET %s WHERE id=?" % cols, (*kw.values(), task_id))


TASK_COLS = ("id", "list_id", "pos", "text", "done", "done_by", "done_at", "created_by", "created_at",
             "assignee_id", "assignee_username", "assignee_name", "due_at", "due_has_time", "done_by_id")


def snapshot(list_id):
    lst = get_list(list_id)
    return {"title": lst["title"], "locked": lst["locked"],
            "tasks": [{k: t[k] for k in TASK_COLS} for t in tasks(list_id)]}


def restore(list_id, snap):
    c = connect()
    c.execute("DELETE FROM tasks WHERE list_id=?", (list_id,))
    for t in snap["tasks"]:
        c.execute("INSERT INTO tasks(%s) VALUES(%s)" % (",".join(TASK_COLS), ",".join("?" * len(TASK_COLS))),
                  tuple(t[k] for k in TASK_COLS))
    c.execute("UPDATE lists SET title=?, locked=? WHERE id=?", (snap["title"], snap["locked"], list_id))
    c.commit()


# --- chats / users / members ---
def upsert_chat(chat_id, ctype, title):
    x("INSERT INTO chats(chat_id,type,title) VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET "
      "type=excluded.type, title=COALESCE(excluded.title, chats.title)", (chat_id, ctype, title))


def chat(chat_id):
    r = q("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
    if r:
        return r[0]
    upsert_chat(chat_id, None, None)
    return q("SELECT * FROM chats WHERE chat_id=?", (chat_id,))[0]


def set_chat(chat_id, **kw):
    chat(chat_id)
    cols = ", ".join("%s=?" % k for k in kw)
    x("UPDATE chats SET %s WHERE chat_id=?" % cols, (*kw.values(), chat_id))


def upsert_user(user_id, name, username):
    x("INSERT INTO users(user_id,name,username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
      "name=excluded.name, username=COALESCE(excluded.username, users.username)", (user_id, name, username))


def user(user_id):
    r = q("SELECT * FROM users WHERE user_id=?", (user_id,))
    return r[0] if r else None


def user_by_username(username):
    r = q("SELECT * FROM users WHERE lower(username)=lower(?)", (username,))
    return r[0] if r else None


def set_user(user_id, **kw):
    cols = ", ".join("%s=?" % k for k in kw)
    x("UPDATE users SET %s WHERE user_id=?" % cols, (*kw.values(), user_id))


def touch_member(chat_id, user_id, now=None):
    x("INSERT INTO members(chat_id,user_id,last_seen) VALUES(?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET "
      "last_seen=excluded.last_seen", (chat_id, user_id, now or time.time()))


def members(chat_id):
    return q("SELECT u.* FROM members m JOIN users u ON u.user_id=m.user_id WHERE m.chat_id=? "
             "ORDER BY m.last_seen DESC", (chat_id,))


def groups_of(user_id):
    return q("SELECT c.* FROM members m JOIN chats c ON c.chat_id=m.chat_id WHERE m.user_id=? "
             "AND c.type IN ('group','supergroup')", (user_id,))


# --- reminders ---
def reminders(chat_id, active_only=True):
    return q("SELECT * FROM reminders WHERE chat_id=?%s ORDER BY kind, minute" % (" AND active=1" if active_only else ""),
             (chat_id,))


def add_reminder(chat_id, kind, minute, next_at, user_id=None):
    return x("INSERT INTO reminders(chat_id,kind,minute,next_at,created_by,created_at) VALUES(?,?,?,?,?,?)",
             (chat_id, kind, minute, next_at, user_id, time.time())).lastrowid


def due_reminders(now):
    return q("SELECT * FROM reminders WHERE active=1 AND next_at IS NOT NULL AND next_at<=?", (now,))


def claim_reminder(rid, old_next, new_next):
    """Compare-and-set: only one worker (and only once) moves next_at forward -> no duplicate sends."""
    cur = x("UPDATE reminders SET next_at=?, active=CASE WHEN ? IS NULL THEN 0 ELSE active END "
            "WHERE id=? AND next_at=? AND active=1", (new_next, new_next, rid, old_next))
    return cur.rowcount == 1


def claim_due(task_id, kind, due_at):
    return x("INSERT OR IGNORE INTO due_sent(task_id,kind,due_at) VALUES(?,?,?)", (task_id, kind, due_at)).rowcount == 1


# --- outbox (DMs) ---
def queue_dm(user_id, group_id, text, markup, not_before, now=None):
    import json
    return x("INSERT INTO outbox(user_id,group_id,text,markup,not_before,created_at) VALUES(?,?,?,?,?,?)",
             (user_id, group_id, text, json.dumps(markup, ensure_ascii=False) if markup else None, not_before,
              now or time.time())).lastrowid


def outbox_due(now):
    return q("SELECT * FROM outbox WHERE claimed_at IS NULL AND not_before<=? ORDER BY id LIMIT 50", (now,))


def claim_outbox(oid, now):
    return x("UPDATE outbox SET claimed_at=? WHERE id=? AND claimed_at IS NULL", (now, oid)).rowcount == 1


def finish_outbox(oid, now, error=None):
    x("UPDATE outbox SET sent_at=?, error=? WHERE id=?", (None if error else now, error, oid))


# --- prompts / delayed deletes ---
def add_prompt(chat_id, message_id, kind, list_id=None, task_id=None, user_id=None, payload=None, now=None):
    x("INSERT OR REPLACE INTO prompts VALUES(?,?,?,?,?,?,?,?)",
      (chat_id, message_id, kind, list_id, task_id, user_id, payload, now or time.time()))


def prompt(chat_id, message_id):
    r = q("SELECT * FROM prompts WHERE chat_id=? AND message_id=?", (chat_id, message_id))
    return r[0] if r else None


def latest_prompt_for(chat_id, user_id, since):
    r = q("SELECT * FROM prompts WHERE chat_id=? AND user_id=? AND created_at>=? AND kind!='offer' "
          "ORDER BY created_at DESC LIMIT 1", (chat_id, user_id, since))
    return r[0] if r else None


def drop_prompt(chat_id, message_id):
    x("DELETE FROM prompts WHERE chat_id=? AND message_id=?", (chat_id, message_id))


def schedule_delete(chat_id, message_id, at_ts):
    x("INSERT OR REPLACE INTO deletes VALUES(?,?,?)", (chat_id, message_id, at_ts))


def deletes_due(now):
    return q("SELECT * FROM deletes WHERE at<=? LIMIT 50", (now,))


def drop_delete(chat_id, message_id):
    x("DELETE FROM deletes WHERE chat_id=? AND message_id=?", (chat_id, message_id))


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
