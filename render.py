"""Render a list to Bale text (Bale always parses Markdown) + inline keyboard."""
import os, re, time, datetime
from zoneinfo import ZoneInfo
from parse import to_fa_digits

TEHRAN = ZoneInfo("Asia/Tehran")
MAX_LEN = 4000
FEW = 6            # up to this many tasks: one wide button per task; more: compact numbered buttons
ROW = 5
MAX_BUTTONS = 95   # keep under Telegram-style 100 button limit (incl. refresh)
FA_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def md_escape(t):
    """Neutralise Bale Markdown in user text (same idea as the fitness bot's balefmt)."""
    t = t.replace("*", "∗").replace("`", "ˋ").replace("[", "［").replace("]", "］").replace("~", "∼")
    return re.sub(r"(?<!\S)_|_(?!\S)", "＿", t)


def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def fa_time(ts=None, with_date=True):
    d = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), TEHRAN)
    hm = to_fa_digits("%02d:%02d" % (d.hour, d.minute))
    if not with_date:
        return hm
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return "%s %s، ساعت %s" % (to_fa_digits(jd), FA_MONTHS[jm - 1], hm)


def progress(done, total, width=10):
    pct = round(100 * done / total) if total else 0
    filled = round(width * done / total) if total else 0
    bar = "▰" * filled + "▱" * (width - filled)
    return "پیشرفت: %s از %s (%s٪)\n%s" % (to_fa_digits(done), to_fa_digits(total), to_fa_digits(pct), bar)


def strike(t, mode=None):
    """Bale Markdown has no documented strikethrough, so by default done items are plain text (✅ is the signal).
    TODO_STRIKE=unicode draws it with combining U+0336 (works everywhere, but looks rough with Persian script)."""
    mode = mode or os.environ.get("TODO_STRIKE", "none")
    if mode == "unicode":
        return "".join(c + "\u0336" if not c.isspace() else c for c in t)
    return t


def render_text(lst, tasks, now=None, show_who=True):
    lines = ["*%s*" % md_escape(lst["title"]).strip(), ""]
    if not tasks:
        lines.append("هنوز کاری اضافه نشده. با  /add متن کار  اضافه کنید.")
    for i, t in enumerate(tasks, 1):
        text = md_escape(t["text"])
        if t["done"]:
            row = "✅ %s. %s" % (to_fa_digits(i), strike(text))
            if show_who and t["done_by"]:
                row += "  — %s" % md_escape(t["done_by"])[:30]
        else:
            row = "⬜ %s. %s" % (to_fa_digits(i), text)
        lines.append(row)
    lines.append("")
    total, done = len(tasks), sum(1 for t in tasks if t["done"])
    if total:
        lines.append(progress(done, total))
    footer = "🕒 آخرین به‌روزرسانی: %s" % fa_time(now)
    if lst["locked"]:
        footer += "  🔒"
    lines.append(footer)
    out = "\n".join(lines)
    if len(out) > MAX_LEN:  # trim long task texts first, then drop the middle
        out = _shrink(lines)
    return out


def _shrink(lines):
    lines = [l if len(l) < 80 else l[:77] + "…" for l in lines]
    out = "\n".join(lines)
    while len(out) > MAX_LEN and len(lines) > 6:
        del lines[len(lines) // 2]
        out = "\n".join(lines)
    return out[:MAX_LEN]


def keyboard(lst, tasks):
    rows = []
    shown = tasks[:MAX_BUTTONS]
    if len(shown) <= FEW:
        for i, t in enumerate(shown, 1):
            label = "%s %s. %s" % ("✅" if t["done"] else "⬜", to_fa_digits(i), t["text"])
            if len(label) > 32:
                label = label[:31] + "…"
            rows.append([{"text": label, "callback_data": "t:%d" % t["id"]}])
    else:
        row = []
        for i, t in enumerate(shown, 1):
            row.append({"text": ("✅" if t["done"] else "⬜") + to_fa_digits(i), "callback_data": "t:%d" % t["id"]})
            if len(row) == ROW:
                rows.append(row); row = []
        if row:
            rows.append(row)
    rows.append([{"text": "🔄 به‌روزرسانی", "callback_data": "r:%d" % lst["id"]}])
    return {"inline_keyboard": rows}
