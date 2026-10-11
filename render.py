"""Render a list to Bale text (Bale always parses Markdown) + the button UI (inline keyboards)."""
import os, re, time
from parse import to_fa_digits as fa
import dates
from dates import gregorian_to_jalali, fa_stamp  # noqa: F401  (re-exported for callers/tests)

MAX_LEN = 4000
FEW = 8            # up to this many tasks: one wide button per task; more: compact numbered buttons
ROW = 5
MAX_TASK_BUTTONS = 80
UNDO_WINDOW = 120  # seconds the ↩️ button stays


def md_escape(t):
    """Neutralise Bale Markdown in user text (same idea as the fitness bot's balefmt)."""
    t = (t or "").replace("*", "∗").replace("`", "ˋ").replace("[", "［").replace("]", "］").replace("~", "∼")
    return re.sub(r"(?<!\S)_|_(?!\S)", "＿", t)


def progress(done, total, width=10):
    pct = round(100 * done / total) if total else 0
    filled = round(width * done / total) if total else 0
    bar = "▰" * filled + "▱" * (width - filled)
    return "پیشرفت: %s از %s (%s٪)\n%s" % (fa(done), fa(total), fa(pct), bar)


def strike(t, mode=None):
    """Bale Markdown has no documented strikethrough, so by default done items are plain text (✅ is the signal).
    TODO_STRIKE=unicode draws it with combining U+0336 (works everywhere, but looks rough with Persian script)."""
    mode = mode or os.environ.get("TODO_STRIKE", "none")
    if mode == "unicode":
        return "".join(c + "\u0336" if not c.isspace() else c for c in t)
    return t


def who(t):
    """Assignee label: @username if known (Bale turns it into a mention), else the name."""
    if t["assignee_username"]:
        return "@" + t["assignee_username"]
    return t["assignee_name"] or ""


def task_line(i, t, now):
    text = md_escape(t["text"])
    if t["done"]:
        row = "✅ %s. %s" % (fa(i), strike(text))
        if t["done_by"]:
            row += "  — %s" % md_escape(t["done_by"])[:30]
        return row
    row = "⬜ %s. %s" % (fa(i), text)
    meta = []
    if who(t):
        meta.append("👤 " + md_escape(who(t)))
    if t["due_at"]:
        over = dates.is_overdue(t["due_at"], t["due_has_time"], now)
        meta.append(("⚠️ " if over else "⏰ ") + dates.fmt_due(t["due_at"], t["due_has_time"], now))
    if meta:
        row += "  (" + " · ".join(meta) + ")"
    return row


REM_KIND = {"daily": "هر روز", "work": "شنبه تا چهارشنبه", "once": "یک‌بار"}


def reminders_summary(rems, now=None):
    out = []
    for r in rems:
        if r["kind"] == "once":
            out.append("یک‌بار %s" % dates.fmt_due(r["next_at"], True, now))
        else:
            out.append("%s %s" % (REM_KIND[r["kind"]], fa("%02d:%02d" % (r["minute"] // 60, r["minute"] % 60))))
    return " · ".join(out)


def render_text(lst, tasks, now=None, rems=()):
    now = time.time() if now is None else now
    lines = ["*%s*" % md_escape(lst["title"]).strip(), ""]
    if not tasks:
        lines.append("هنوز کاری نیست. دکمهٔ «➕ افزودن» را بزنید.")
    for i, t in enumerate(tasks, 1):
        lines.append(task_line(i, t, now))
    lines.append("")
    total, done = len(tasks), sum(1 for t in tasks if t["done"])
    if total:
        lines.append(progress(done, total))
    if rems:
        lines.append("⏰ یادآور: " + reminders_summary(rems, now))
    footer = "🕒 %s" % dates.fa_stamp(now)
    if lst["locked"]:
        footer += "  🔒"
    lines.append(footer)
    out = "\n".join(lines)
    if len(out) > MAX_LEN:
        out = _shrink(lines)
    return out


def _shrink(lines):
    lines = [l if len(l) < 80 else l[:77] + "…" for l in lines]
    out = "\n".join(lines)
    while len(out) > MAX_LEN and len(lines) > 6:
        del lines[len(lines) // 2]
        out = "\n".join(lines)
    return out[:MAX_LEN]


# ---------------------------------------------------------------- keyboards
def B(text, data):
    return {"text": text, "callback_data": data}


def _short(s, n=30):
    return s if len(s) <= n else s[: n - 1] + "…"


def task_buttons(tasks, action, list_id, wide_ok=True, marks=True):
    """Toggle (action 't') or pick (other actions) buttons for each task."""
    shown = tasks[:MAX_TASK_BUTTONS]
    rows = []
    if wide_ok and len(shown) <= FEW:
        for i, t in enumerate(shown, 1):
            mark = ("✅" if t["done"] else "⬜") + " " if marks else ""
            rows.append([B(_short("%s%s. %s" % (mark, fa(i), t["text"]), 32), "%s:%d:%d" % (action, list_id, t["id"]))])
        return rows
    row = []
    for i, t in enumerate(shown, 1):
        mark = ("✅" if t["done"] else "⬜") if marks else ""
        row.append(B(mark + fa(i), "%s:%d:%d" % (action, list_id, t["id"])))
        if len(row) == ROW:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return rows


def kb_main(lst, tasks, now=None):
    now = time.time() if now is None else now
    L = lst["id"]
    rows = task_buttons(tasks, "t", L)
    rows.append([B("➕ افزودن", "add:%d" % L), B("⏰", "rem:%d" % L), B("📚", "ls:%d" % L), B("⚙️", "more:%d" % L)])
    if lst["undo_at"] and now - lst["undo_at"] < UNDO_WINDOW:
        rows.append([B("↩️ واگرد: " + _short(lst["undo_label"] or "آخرین تغییر", 24), "undo:%d" % L)])
    return {"inline_keyboard": rows}


def kb_more(lst, chat_row):
    L = lst["id"]
    quiet = "روشن" if chat_row["quiet_on"] else "خاموش"
    return {"inline_keyboard": [
        [B("✏️ ویرایش", "pk:%d:edit" % L), B("🗑 حذف", "pk:%d:del" % L)],
        [B("👤 مسئول", "pk:%d:asg" % L), B("📅 موعد", "pk:%d:due" % L)],
        [B("🧹 پاک کردن انجام‌شده‌ها", "clr:%d" % L)],
        [B("🔓 باز کردن قفل" if lst["locked"] else "🔒 قفل", "lock:%d" % L), B("🏷 عنوان", "ttl:%d" % L)],
        [B("🆕 لیست جدید", "newl:%d" % L), B("🗄 بایگانی لیست", "arc:%d" % L)],
        [B("🗑 حذف این لیست", "dell:%d" % L)],
        [B("🌙 ساعت سکوت: %s" % quiet, "qt:%d" % L), B("🕰 ساعت‌های سکوت", "qh:%d" % L)],
        [B("🔄 به‌روزرسانی", "r:%d" % L), B("◀️ بازگشت", "bk:%d" % L)],
    ]}


PICK_TITLE = {"edit": "✏️ کدام کار ویرایش شود؟", "del": "🗑 کدام کار حذف شود؟",
              "asg": "👤 مسئول کدام کار؟", "due": "📅 موعد کدام کار؟"}


def kb_pick(lst, tasks, what):
    rows = task_buttons(tasks, "p" + what, lst["id"], marks=False)
    rows.append([B("◀️ بازگشت", "more:%d" % lst["id"])])
    return {"inline_keyboard": rows}


def kb_assign(lst, task, members):
    L, T = lst["id"], task["id"]
    rows, row = [], []
    for m in members[:12]:
        mark = "✓ " if task["assignee_id"] == m["user_id"] else ""
        row.append(B(_short(mark + (m["name"] or ("@" + (m["username"] or "?"))), 18), "as:%d:%d:%d" % (L, T, m["user_id"])))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([B("🚫 بدون مسئول", "as:%d:%d:0" % (L, T)), B("◀️ بازگشت", "more:%d" % L)])
    return {"inline_keyboard": rows}


DUE_PRESETS = [("امروز ۱۸", "امروز 18"), ("امشب ۲۱", "امروز 21"), ("فردا ۹", "فردا 9"),
               ("فردا ۱۸", "فردا 18"), ("پس‌فردا", "پسفردا"), ("شنبه", "شنبه")]


def kb_due(lst, task):
    L, T = lst["id"], task["id"]
    rows, row = [], []
    for i, (label, _v) in enumerate(DUE_PRESETS):
        row.append(B(label, "du:%d:%d:%d" % (L, T, i)))
        if len(row) == 3:
            rows.append(row); row = []
    rows.append([B("✍️ نوشتن تاریخ/ساعت", "du:%d:%d:w" % (L, T)), B("❌ حذف موعد", "du:%d:%d:x" % (L, T))])
    rows.append([B("◀️ بازگشت", "more:%d" % L)])
    return {"inline_keyboard": rows}


DUE_MODES = [("both", "۱ ساعت قبل + سر موعد"), ("due", "فقط سر موعد"), ("off", "خاموش")]
TIME_PRESETS = [9 * 60, 13 * 60, 18 * 60, 21 * 60]


def kb_rem(lst, rems, chat_row, bot_username, private=False):
    L = lst["id"]
    kinds = {r["kind"] for r in rems}
    mode = dict(DUE_MODES).get(lst["due_mode"] or "both", "خاموش")
    quiet = "روشن" if chat_row["quiet_on"] else "خاموش"
    rows = [
        [B(("✓ " if "daily" in kinds else "") + "📅 هر روز", "rk:%d:daily" % L),
         B(("✓ " if "work" in kinds else "") + "🗓 شنبه تا چهارشنبه", "rk:%d:work" % L)],
        [B(("✓ " if "once" in kinds else "") + "1️⃣ یک‌بار", "rk:%d:once" % L),
         B("⏳ موعد کارها: " + mode, "dm:%d" % L)],
        [B("🌙 ساعت سکوت: %s" % quiet, "qt:%d:r" % L), B("🕰 ساعت‌های سکوت", "qh:%d" % L)],
    ]
    if rems:
        rows.append([B("🔕 خاموش کردن همهٔ یادآورها", "roff:%d" % L)])
    if not private and bot_username:
        rows.append([{"text": "🔔 فعال‌سازی یادآور شخصی", "url": deep_link(bot_username, lst["chat_id"])}])
    rows.append([B("◀️ بازگشت", "bk:%d" % L)])
    return {"inline_keyboard": rows}


def kb_times(lst, kind, rems):
    L = lst["id"]
    have = {r["minute"] for r in rems if r["kind"] == kind}
    if kind == "once":
        rows = [[B("⏱ ۱ ساعت دیگر", "ro:%d:1h" % L), B("امروز ۱۸", "ro:%d:t18" % L)],
                [B("فردا ۹", "ro:%d:f9" % L), B("فردا ۱۸", "ro:%d:f18" % L)]]
    else:
        row = []
        for m in TIME_PRESETS:
            row.append(B(("✓ " if m in have else "") + fa("%02d:%02d" % (m // 60, m % 60)), "rt:%d:%s:%d" % (L, kind, m)))
        rows = [row[:2], row[2:]]
        extra = sorted(have - set(TIME_PRESETS))
        if extra:
            rows.append([B("✓ " + fa("%02d:%02d" % (m // 60, m % 60)), "rt:%d:%s:%d" % (L, kind, m)) for m in extra[:4]])
    rows.append([B("✍️ ساعت دلخواه", "rw:%d:%s" % (L, kind)), B("◀️ بازگشت", "rem:%d" % L)])
    return {"inline_keyboard": rows}


QUIET_PRESETS = [(22, 7), (23, 7), (0, 8), (23, 9)]


def quiet_label(s, e):
    return "%s تا %s" % (fa("%02d:00" % s), fa("%02d:00" % e))


def kb_quiet_hours(prefix, ident, cur, back):
    rows = [[B(("✓ " if (s, e) == cur else "") + quiet_label(s, e), "%s:%s:%d:%d" % (prefix, ident, s, e))]
            for s, e in QUIET_PRESETS]
    rows.append([B("◀️ بازگشت", back)])
    return {"inline_keyboard": rows}


def deep_link(bot_username, chat_id):
    return "https://ble.ir/%s?start=g%s" % (bot_username, str(chat_id).replace("-", "m"))


def kb_confirm(yes_data, no_data, yes="✅ بله", no="◀️ نه"):
    return {"inline_keyboard": [[B(yes, yes_data), B(no, no_data)]]}


# ---------------------------------------------------------------- several lists
def short_progress(tasks):
    total, done = len(tasks), sum(1 for t in tasks if t["done"])
    return done, total


def kb_lists(lst, lists_with_tasks):
    """📚 menu inside a list message: jump to another list, or create one."""
    rows = []
    for l, tasks in lists_with_tasks:
        d, n = short_progress(tasks)
        mark = "📍 " if l["id"] == lst["id"] else "📋 "
        rows.append([B(_short("%s%s · %s/%s" % (mark, l["title"], fa(d), fa(n)), 34), "go:%d" % l["id"])])
    rows.append([B("🆕 لیست جدید", "newl:%d" % lst["id"]), B("◀️ بازگشت", "bk:%d" % lst["id"])])
    return {"inline_keyboard": rows}


def index_text(lists_with_tasks, private=False, now=None):
    lines = ["📚 *%s*" % ("لیست‌های من" if private else "لیست‌های گروه"), ""]
    for i, (l, tasks) in enumerate(lists_with_tasks, 1):
        d, n = short_progress(tasks)
        pct = round(100 * d / n) if n else 0
        bar = "▰" * round(5 * d / n) + "▱" * (5 - round(5 * d / n)) if n else "▱" * 5
        lines.append("%s. %s — %s از %s (%s٪) %s%s" % (fa(i), md_escape(l["title"]), fa(d), fa(n), fa(pct), bar,
                                                    "  🔒" if l["locked"] else ""))
    lines += ["", "روی هر لیست بزنید تا پایین گفتگو بیاید.", "🕒 %s" % dates.fa_stamp(now if now is not None else time.time())]
    return "\n".join(lines)


def kb_index(lists_with_tasks):
    rows = []
    for l, tasks in lists_with_tasks:
        d, n = short_progress(tasks)
        rows.append([B(_short("📋 %s · %s/%s" % (l["title"], fa(d), fa(n)), 34), "go:%d" % l["id"])])
    rows.append([B("🆕 لیست جدید", "newl:0")])
    return {"inline_keyboard": rows}


def kb_pick_list(lists, data_fmt):
    rows = [[B(_short("📋 " + l["title"], 34), data_fmt % l["id"])] for l in lists]
    rows.append([B("✖️ لغو", data_fmt % 0)])
    return {"inline_keyboard": rows}
