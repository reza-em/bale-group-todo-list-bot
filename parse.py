"""Pure text helpers: Persian digits, command/alias parsing, number lists, list import."""
import re

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_TO_EN = {ord(c): str(i) for i, c in enumerate(FA_DIGITS)}
_TO_EN.update({ord(c): str(i) for i, c in enumerate(AR_DIGITS)})
_TO_FA = {ord(str(i)): c for i, c in enumerate(FA_DIGITS)}
ZWNJ = "\u200c"


def to_en_digits(s):
    return s.translate(_TO_EN)


def to_fa_digits(s):
    return str(s).translate(_TO_FA)


def norm(s):
    """Normalise for alias matching: Arabic letters -> Persian, digits -> latin, ZWNJ -> space."""
    s = to_en_digits(s or "")
    s = s.replace("ي", "ی").replace("ك", "ک").replace("ى", "ی").replace(ZWNJ, " ")
    return s


# --- commands -----------------------------------------------------------------
# slash commands -> canonical name
SLASH = {
    "start": "help", "help": "help",
    "new": "new", "list": "list", "show": "list",
    "add": "add", "import": "import",
    "done": "done", "undo": "undo", "del": "del", "delete": "del", "rm": "del",
    "edit": "edit", "clear_done": "clear_done", "cleardone": "clear_done",
    "reset": "reset", "title": "title", "lock": "lock", "unlock": "unlock",
    "due": "due", "remind": "remind", "reminder": "remind", "settings": "settings",
}
# Persian aliases (no slash). (regex on normalised text, canonical, needs-argument)
# "needs" = "num": must be followed by numbers; "text": must be followed by text; None: must be the whole message.
FA_ALIASES = [
    (r"انجام ?شد|تیک|تمام شد", "done", "num"),
    (r"برگشت|لغو انجام|انجام نشد", "undo", "num"),
    (r"حذف|پاک کن", "del", "num"),
    (r"ویرایش", "edit", "text"),
    (r"اضافه کن|اضافه|افزودن", "add", "text_or_reply"),
    (r"وارد کن|وارد کردن|ایمپورت", "import", None),
    (r"لیست جدید|فهرست جدید", "new", "opt"),
    (r"لیست|فهرست|لیست کارها", "list", None),
    (r"عنوان", "title", "text"),
    (r"پاکسازی|پاک کردن انجام ?شده ?ها", "clear_done", None),
    (r"ریست|شروع دوباره", "reset", None),
    (r"قفل", "lock", None),
    (r"باز کردن قفل|آزاد", "unlock", None),
    (r"راهنما|کمک", "help", None),
    (r"موعد", "due", "text"),
    (r"یادآور|یادآوری", "remind", None),
    (r"تنظیمات", "settings", None),
]
_FA_RE = [(re.compile(r"^(?:%s)(?=$|[\s:،,0-9])[\s:،,]*" % pat), cmd, need) for pat, cmd, need in FA_ALIASES]
_SLASH_RE = re.compile(r"^/([A-Za-z_]+)(?:@([A-Za-z0-9_]+))?(?=$|\s)[ \t]*")


def parse_command(text, bot_username=None, is_reply=False):
    """Return (cmd, arg) or None. arg keeps line breaks (multi-line /add). Persian digits stay untouched in arg."""
    if not text:
        return None
    t = text.strip()
    m = _SLASH_RE.match(t)
    if m:
        name, target = m.group(1).lower(), m.group(2)
        if target and bot_username and target.lower() != bot_username.lower():
            return None  # command addressed to another bot
        cmd = SLASH.get(name)
        if not cmd:
            return None
        return cmd, t[m.end():].strip("\n").strip()
    first_line = norm(t.split("\n", 1)[0]).strip()
    for rx, cmd, need in _FA_RE:
        m = rx.match(first_line)
        if not m:
            continue
        # position of the argument in the ORIGINAL text: same length (norm keeps lengths)
        lines = t.split("\n", 1)
        rest = (lines[0].strip()[m.end():] + ("\n" + lines[1] if len(lines) > 1 else "")).strip()
        if need is None:
            if rest:
                continue  # chatter like "لیست خرید رو کی میاره؟" is not a command
        elif need == "num":
            if not parse_numbers(rest)[0] or parse_numbers(rest)[1]:
                continue
        elif need == "text":
            if not rest:
                continue
        elif need == "text_or_reply":
            if not rest and not is_reply:
                continue
        return cmd, rest
    return None


def parse_numbers(s):
    """'1 3 5', '۱،۳', '2-4', '#2' -> ([1,3,5], bad_tokens). Keeps order, de-duplicates."""
    s = to_en_digits(s or "")
    nums, bad = [], []
    for tok in re.split(r"[\s,،;و]+", s.strip()):
        tok = tok.strip().lstrip("#")
        if not tok:
            continue
        m = re.fullmatch(r"(\d{1,4})\s*[-–تا]+\s*(\d{1,4})", tok)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            if b - a > 200:
                bad.append(tok); continue
            rng = range(a, b + 1)
        elif tok.isdigit() and len(tok) <= 4:
            rng = [int(tok)]
        else:
            bad.append(tok); continue
        for n in rng:
            if n not in nums:
                nums.append(n)
    return nums, bad


def split_num_text(s):
    """'3 new text' -> (3, 'new text') or (None, s)."""
    m = re.match(r"^\s*#?([0-9۰-۹٠-٩]{1,4})[\s.)\-:،]+(.+)$", s or "", re.S)
    if not m:
        return None, s
    return int(to_en_digits(m.group(1))), m.group(2).strip()


# --- list import ----------------------------------------------------------------
DONE_MARKS = ("✅", "☑️", "☑", "✔️", "✔", "[x]", "[X]", "✓", "🟢")
TODO_MARKS = ("⬜️", "⬜", "▫️", "▫", "◻️", "◻", "☐", "□", "▪️", "▪", "◽", "🔲", "🔳", "[ ]", "[]", "○", "◯",
              "•", "●", "◾", "-", "–", "—", "*", "+", "·", "▶️", "▶", "🔸", "🔹", "🔘", "⚪", "⚫")
_NUM_PREFIX = re.compile(r"^\s*(?:\(?[0-9۰-۹٠-٩]{1,3}\s*[.)\-:،ـ]|[0-9۰-۹٠-٩]{1,3}\s+(?=\S)|[0-9]\ufe0f?\u20e3)\s*")
_SKIP_LINE = re.compile(r"^(?:پیشرفت|آخرین به ?روز ?رسانی|progress|📊|🕒|⏱)", re.I)


def _strip_marks(line):
    """Strip leading bullets/checkboxes/numbers (any order, repeated). Returns (text, done, had_marker)."""
    s, done, marked = line.strip(), False, False
    for _ in range(6):
        changed = False
        m = _NUM_PREFIX.match(s)
        if m and m.end() < len(s):
            s, marked, changed = s[m.end():].lstrip(), True, True
        for mk in DONE_MARKS:
            if s.startswith(mk):
                s, done, marked, changed = s[len(mk):].lstrip(), True, True, True
                break
        for mk in TODO_MARKS:
            if s.startswith(mk) and (len(mk) > 1 or not mk.isascii() or s[len(mk):len(mk) + 1] in (" ", "\t")):
                s, marked, changed = s[len(mk):].lstrip(), True, True
                break
        if not changed:
            break
    # trailing done marks ("buy milk ✅") and our own "— name" suffix are not part of the task
    for mk in DONE_MARKS:
        if s.endswith(mk):
            s, done, marked = s[: -len(mk)].rstrip(), True, True
    s = s.strip("~").strip()
    return s, done, marked


def parse_import(text, max_items=100):
    """Parse a pasted/replied list into [(text, done)]. If some lines carry list markers, unmarked lines
    (titles, progress lines) are ignored; otherwise every non-empty line is a task."""
    rows = []
    for raw in (text or "").splitlines():
        if not raw.strip() or _SKIP_LINE.match(raw.strip()):
            continue
        t, done, marked = _strip_marks(raw)
        if t and re.search(r"\w", t):
            rows.append((t, done, marked))
    if any(r[2] for r in rows) and not all(r[2] for r in rows):
        rows = [r for r in rows if r[2]]
    out = []
    for t, done, _ in rows[:max_items]:
        out.append((t[:300], done))
    return out


def parse_add_lines(arg):
    """'/add a' -> ['a']; multi-line /add -> one task per line, bullets/numbers stripped."""
    if not arg:
        return []
    lines = [l for l in arg.splitlines() if l.strip()]
    if len(lines) == 1:
        t, _d, _m = _strip_marks(lines[0]) if _NUM_PREFIX.match(lines[0]) is None else (lines[0].strip(), False, False)
        return [t[:300]] if t else []
    out = []
    for l in lines:
        t = _strip_marks(l)[0]
        if t:
            out.append(t[:300])
    return out


# --- task meta: "@user" assignee and "| due" suffix --------------------------------------
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{2,31})\b")


def extract_meta(line):
    """'خرید نان @ali | فردا ۱۸' -> ('خرید نان', 'ali', 'فردا ۱۸'). Due needs an explicit '|' or '⏰' separator."""
    text, due = line, None
    for sep in ("|", "⏰", "｜"):
        if sep in text:
            text, due = text.split(sep, 1)
            due = due.strip() or None
            break
    username = None
    m = _MENTION.search(text)
    if m:
        username = m.group(1)
        text = (text[:m.start()] + text[m.end():])
    text = re.sub(r"\s{2,}", " ", text).strip(" -،,")
    return text, username, due


def looks_like_list(text):
    """≥2 lines that carry list markers (numbers, bullets, checkboxes)."""
    marked = 0
    for raw in (text or "").splitlines():
        if raw.strip() and _strip_marks(raw)[2]:
            marked += 1
    return marked >= 2


def mentions(text, username):
    return bool(username) and ("@" + username.lower()) in (text or "").lower()
