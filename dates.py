"""Tehran time helpers: Jalali calendar, Persian natural due-date parsing, compact due display, quiet hours."""
import re, time, datetime
from zoneinfo import ZoneInfo
from parse import to_en_digits, to_fa_digits, norm

TEHRAN = ZoneInfo("Asia/Tehran")
FA_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
# python weekday(): Mon=0 ... Sun=6
FA_WEEKDAYS = {"شنبه": 5, "یکشنبه": 6, "یک شنبه": 6, "دوشنبه": 0, "دو شنبه": 0, "سه شنبه": 1, "سهشنبه": 1,
               "چهارشنبه": 2, "چهار شنبه": 2, "پنجشنبه": 3, "پنج شنبه": 3, "جمعه": 4}
WEEKDAY_NAME = {5: "شنبه", 6: "یکشنبه", 0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه"}
DATE_ONLY_REMIND_HOUR = 9   # reminders for a due date without a time fire at 09:00 that day


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


def jalali_to_gregorian(jy, jm, jd):
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal_a = [0, 31, 29 if (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def now_dt(ts=None):
    return datetime.datetime.fromtimestamp(time.time() if ts is None else ts, TEHRAN)


def at(d, hour=0, minute=0):
    """Tehran wall-clock time on date d -> timestamp."""
    return datetime.datetime(d.year, d.month, d.day, hour, minute, tzinfo=TEHRAN).timestamp()


def hm(ts):
    d = now_dt(ts)
    return to_fa_digits("%02d:%02d" % (d.hour, d.minute))


def fa_date(ts):
    d = now_dt(ts)
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return "%s %s" % (to_fa_digits(jd), FA_MONTHS[jm - 1])


def fa_stamp(ts):
    return "%s، ساعت %s" % (fa_date(ts), hm(ts))


# --- natural due-date parsing ---------------------------------------------------------
_TIME = re.compile(r"(?:ساعت\s*)?(\d{1,2})(?:[:٫.](\d{2}))?\s*(صبح|ظهر|بعد ?از ?ظهر|عصر|شب|am|pm)?", re.I)


def _apply_ampm(h, mark):
    if not mark:
        return h
    mark = mark.replace(" ", "").lower()
    if mark in ("بعدازظهر", "عصر", "شب", "pm") and h < 12:
        return h + 12 if not (mark == "شب" and h == 12) else 0
    if mark in ("صبح", "am") and h == 12:
        return 0
    return h


def parse_due(text, now=None):
    """Persian/English natural input -> (timestamp, has_time) or None.
    Accepts: امروز/فردا/پس‌فردا, weekday names, «۲۵ مهر», «۱۴۰۵/۷/۲۵», «۷/۲۵», «۱۸», «۱۸:۳۰», «۵ عصر»,
    «۲ ساعت دیگه», «۳۰ دقیقه دیگر», «۳ روز دیگه». A date without time gives has_time=False (ts = 00:00)."""
    s = norm(text).strip().lower()
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None
    now = time.time() if now is None else now
    today = now_dt(now).date()
    m = re.fullmatch(r"(\d{1,3}) ?(دقیقه|ساعت|روز|min|h|d)(?: ?(?:دیگه|دیگر|بعد))?", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit in ("روز", "d"):
            return at(today + datetime.timedelta(days=n)), False
        return now + n * (60 if unit in ("دقیقه", "min") else 3600), True
    day, rest = None, s
    for word, delta in (("پس فردا", 2), ("پسفردا", 2), ("امروز", 0), ("فردا", 1), ("امشب", 0)):
        if rest.startswith(word):
            day, rest = today + datetime.timedelta(days=delta), rest[len(word):].strip()
            if word == "امشب" and not rest:
                rest = "21"
            break
    if day is None:
        for word in sorted(FA_WEEKDAYS, key=len, reverse=True):
            if rest.startswith(word):
                wd = FA_WEEKDAYS[word]
                ahead = (wd - today.weekday()) % 7
                day, rest = today + datetime.timedelta(days=ahead), rest[len(word):].strip()
                break
    if day is None:
        m = re.match(r"^(?:(\d{4})[/\-.])?(\d{1,2})[/\-.](\d{1,2})\b", rest)
        mm = re.match(r"^(\d{1,2}) ?(%s)(?: (\d{4}))?" % "|".join(FA_MONTHS), rest)
        if m or mm:
            if m:
                jy, jm, jd = (int(m.group(1)) if m.group(1) else None), int(m.group(2)), int(m.group(3))
                rest = rest[m.end():].strip()
            else:
                jd, jm = int(mm.group(1)), FA_MONTHS.index(mm.group(2)) + 1
                jy = int(mm.group(3)) if mm.group(3) else None
                rest = rest[mm.end():].strip()
            if not (1 <= jm <= 12 and 1 <= jd <= 31):
                return None
            cy = gregorian_to_jalali(today.year, today.month, today.day)[0]
            y = jy or cy
            g = datetime.date(*jalali_to_gregorian(y, jm, jd))
            if jy is None and g < today:
                g = datetime.date(*jalali_to_gregorian(y + 1, jm, jd))
            day = g
    rest = rest.strip(" ،,")
    if not rest:
        if day is None:
            return None
        if day == today and s.startswith(("امروز",)):
            return at(day), False
        return at(day), False
    m = _TIME.fullmatch(rest)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    h = _apply_ampm(h, m.group(3))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    if day is None:
        day = today
        if at(day, h, mi) <= now:
            # bare «۵» at 14:00 means 17:00 (afternoon hour); «9:00» with minutes already past -> tomorrow
            if h < 12 and not m.group(3) and not m.group(2) and at(day, h + 12, mi) > now:
                h += 12
            else:
                day = today + datetime.timedelta(days=1)
    return at(day, h, mi), True


def due_effective(due_at, has_time):
    """Moment a task is 'due' for reminders; date-only -> 09:00 that day."""
    if has_time:
        return due_at
    return due_at + DATE_ONLY_REMIND_HOUR * 3600


def is_overdue(due_at, has_time, now):
    if due_at is None:
        return False
    return now > (due_at if has_time else due_at + 86400)


def fmt_due(due_at, has_time, now=None):
    """Compact: «امروز ۱۸:۰۰», «فردا», «شنبه ۹:۰۰», «۲۵ مهر»."""
    now = time.time() if now is None else now
    d, today = now_dt(due_at).date(), now_dt(now).date()
    delta = (d - today).days
    if delta == 0:
        label = "امروز"
    elif delta == 1:
        label = "فردا"
    elif delta == -1:
        label = "دیروز"
    elif 1 < delta < 7:
        label = WEEKDAY_NAME[d.weekday()]
    else:
        label = fa_date(due_at)
    return label + (" " + hm(due_at) if has_time else "")


# --- quiet hours ------------------------------------------------------------------------
def in_quiet(ts, start_h, end_h):
    """Window [start_h, end_h) in Tehran hours; wraps midnight (23 -> 7)."""
    if start_h == end_h:
        return False
    h = now_dt(ts)
    x = h.hour + h.minute / 60
    return (start_h <= x < end_h) if start_h < end_h else (x >= start_h or x < end_h)


def quiet_end(ts, start_h, end_h):
    """If ts is inside the quiet window, the timestamp when it ends; else ts."""
    if not in_quiet(ts, start_h, end_h):
        return ts
    d = now_dt(ts)
    end = datetime.datetime(d.year, d.month, d.day, end_h, 0, tzinfo=TEHRAN)
    if end.timestamp() <= ts:
        end += datetime.timedelta(days=1)
    return end.timestamp()


def next_daily(now, minute_of_day, days=None):
    """Next Tehran time at minute_of_day strictly after now; days = set of python weekdays allowed (None = every day)."""
    d = now_dt(now).date()
    for i in range(0, 9):
        day = d + datetime.timedelta(days=i)
        if days is not None and day.weekday() not in days:
            continue
        ts = at(day, minute_of_day // 60, minute_of_day % 60)
        if ts > now:
            return ts
    return None


WORK_DAYS = {5, 6, 0, 1, 2}  # شنبه تا چهارشنبه
