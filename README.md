# Bale Group To-Do List Bot · ربات لیست کارهای گروهی بله

<div dir="rtl">

## فارسی

رباتی ساده برای پیام‌رسان **بله**: آن را به گروه اضافه کنید، لیست کارها را بسازید و هر کاری که انجام شد تیک بزنید. ربات **همان یک پیام سنجاق‌شده** را ویرایش می‌کند، پس لیست همیشه به‌روز و مرتب می‌ماند.

### امکانات
- لیست جداگانه برای هر گروه (و لیست شخصی در چت خصوصی)
- ارسال و **سنجاق** لیست؛ به‌روزرسانی با ویرایش همان پیام (اگر پیام پاک شده باشد، پیام تازه فرستاده و دوباره سنجاق می‌شود)
- دکمهٔ تیک برای هر کار (در لیست‌های بلند: دکمه‌های شماره‌دار فشرده در ردیف‌های ۵تایی) + دکمهٔ 🔄
- نوار پیشرفت، مثل «پیشرفت: ۳ از ۷ (۴۳٪)» و زمان آخرین به‌روزرسانی به وقت تهران (تاریخ شمسی)
- وارد کردن لیست آماده: روی پیام لیست ریپلای کنید و `/import` بزنید (شماره‌دار، بولت‌دار، با ✅/☑/▫ یا بدون علامت)
- اعداد فارسی و دستورهای فارسی بدون اسلش
- جشن 🎉 وقتی همه کارها انجام شد
- قفل: `/lock` تا فقط ادمین‌ها و سازندهٔ لیست بتوانند تغییر دهند

### دستورها
| دستور | معادل فارسی | کار |
|---|---|---|
| `/new [عنوان]` | لیست جدید | شروع لیست تازه |
| `/list` | لیست | نمایش دوباره و سنجاق |
| `/add متن` (چند خط = چند کار) | اضافه متن | افزودن |
| `/import` (ریپلای) | وارد کن | وارد کردن لیست آماده |
| `/done ۱ ۳ ۵` یا `۲-۴` | انجام شد ۳ | تیک |
| `/undo ۳` | برگشت ۳ | برداشتن تیک |
| `/del ۳` | حذف ۳ | حذف |
| `/edit ۳ متن` | ویرایش ۳ متن | ویرایش |
| `/title عنوان` | عنوان … | تغییر عنوان |
| `/clear_done` | پاکسازی | حذف انجام‌شده‌ها |
| `/reset` | ریست | پاک کردن همه (با تأیید) |
| `/lock` ، `/unlock` | قفل | محدود کردن تغییرات |

> برای سنجاق کردن و دیدن دستورهای فارسیِ بدون اسلش، ربات را در گروه **ادمین** کنید.

</div>

## English

A small Persian-first to-do list bot for the **Bale** messenger (Telegram-compatible Bot API at `https://tapi.bale.ai`). Add it to a group, build a list, tick tasks as they get done — the bot keeps editing **one pinned list message**.

**Features:** per-group lists (and a personal list in private chat) · send + pin, then `editMessageText` the same message (re-send and re-pin if the edit fails) · inline toggle buttons (compact numbered rows of 5 for long lists) + refresh · progress bar and Tehran-time (Jalali) "last updated" · import an existing list by replying with `/import` (numbered, bulleted, ✅/☑/▫ markers; ✅ lines import as done) · Persian digits and slash-less Persian aliases (`انجام شد 3`, `اضافه …`, `حذف 3`, `لیست`) · 🎉 when everything is done · `/lock` so only group admins or the list creator can modify.

**Stack:** Python 3, raw `requests`, long polling (`getUpdates`), SQLite.

### Run
```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
export TODO_BALE_BOT_TOKEN=...      # from @BotFather in Bale; never commit it
./run.sh                            # detached, flock-protected auto-restart supervisor; logs to bot.log
./stop.sh
venv/bin/python -m unittest discover -s tests   # tests (fake API, no network)
```
Optional `.env` (git-ignored): `TODO_STRIKE=unicode` draws done tasks with a Unicode strikethrough (Bale Markdown has no documented strikethrough, so the default is just ✅).

### Bale notes
- Bale always renders Markdown (no `parse_mode`); user text is escaped.
- Admin check uses `getChatMember`, falling back to `getChatAdministrators`.
- Pinning needs the bot to be a group admin; without it, the bot says so (at most every 6 h) and works unpinned.

### Files
`bot.py` polling loop · `app.py` logic · `parse.py` commands/aliases/import · `render.py` text + keyboard · `db.py` SQLite · `api.py` HTTP client · `tests/`.

License: MIT
