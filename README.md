# Bale Group To-Do List Bot · ربات لیست کارهای گروهی بله

<div dir="rtl">

## فارسی

ربات **لیست کارها** برای پیام‌رسان **بله**. یک پیام سنجاق‌شده کل رابط کاربری است: کار اضافه کنید، تیک بزنید، مسئول و موعد بگذارید و یادآور بگیرید — بدون شلوغ کردن گروه.

### شروع سریع
۱. ربات را به گروه اضافه کنید و **ادمین** کنید (برای سنجاق و پاک کردن پیام‌های کمکی).
۲. «📋 ساخت لیست» را بزنید.
۳. زیر لیست: ⬜/✅ تیک · ➕ افزودن · ⏰ یادآور · ⚙️ بیشتر

### امکانات
- **دکمه‌محور:** ➕ افزودن یک پیام «ریپلای کن» می‌فرستد؛ هر خط یک کار. پیام راهنما و جواب شما بعدش پاک می‌شوند.
- **⚙️ بیشتر** (در همان پیام): ✏️ ویرایش، 🗑 حذف، 👤 مسئول، 📅 موعد، 🧹 پاک کردن انجام‌شده‌ها، 🔒 قفل، 🏷 عنوان، 🆕 لیست جدید، 🌙 ساعت سکوت
- **↩️ واگرد** تا ۲ دقیقه بعد از هر تغییر
- **مسئول و موعد در متن:** «خرید نان @ali | فردا ۱۸» — تاریخ فارسی/شمسی: «فردا ۱۸»، «شنبه»، «۲۵ مهر ۱۰:۳۰»، «۱۴۰۵/۸/۱»، «۲ ساعت دیگه»؛ کارهای عقب‌افتاده ⚠️
- **تبدیل پیام به لیست:** لیستی را بفرستید و ربات را منشن کنید (یا روی پیام ربات ریپلای کنید) → دکمهٔ «📋 تبدیل به لیست»
- **یادآور (⏰):** هر روز / شنبه تا چهارشنبه (۰۹، ۱۳، ۱۸، ۲۱ یا ساعت دلخواه)، یک‌باره، یادآور موعد هر کار (۱ ساعت قبل + سر موعد)، خاموش کردن همه — به وقت تهران
- **یادآور شخصی:** «🔔 فعال‌سازی یادآور شخصی» → ربات را استارت کنید؛ هنگام یادآوری کارهای شما در پیوی می‌آید و همان‌جا تیک می‌زنید (لیست گروه هم به‌روز می‌شود). 🔕 قطع یادآور در پیوی.
- **🌙 ساعت سکوت** (پیش‌فرض روشن، ۲۳ تا ۷): هم برای گروه (⚙️ یا ⏰) و هم برای هر نفر در پیوی، قابل خاموش/روشن و تغییر ساعت (۲۲–۷، ۲۳–۷، ۰۰–۸، ۲۳–۹). یادآورهای داخل این بازه به پایان آن منتقل می‌شوند.
- لیست شخصی در چت خصوصی؛ اعداد فارسی؛ دستورهای متنی برای حرفه‌ای‌ها (پاسخ‌شان بعد از ۱۰ ثانیه پاک می‌شود)

### دستورهای متنی (اختیاری)
`/list` `/new` `/add` `/import` `/done ۱ ۳` `/undo` `/del` `/edit ۳ متن` `/due ۳ فردا ۱۸` `/title` `/clear_done` `/reset` `/lock` `/unlock` `/remind` `/settings`
فارسی: «اضافه متن»، «انجام شد ۳»، «برگشت ۳»، «حذف ۳»، «ویرایش ۳ متن»، «موعد ۳ فردا»، «لیست»، «یادآور»

</div>

## English

A Persian-first, button-driven to-do list bot for the **Bale** messenger (Telegram-compatible Bot API at `https://tapi.bale.ai`). One pinned message is the whole UI; menus are swapped in place with `editMessageText`.

**Features**
- Toggle buttons per task (short text for ≤ 8 tasks, compact numbered rows of 5 otherwise) + `➕ Add | ⏰ Reminders | ⚙️ More`
- ➕ Add uses a ForceReply prompt (one task per line); prompt and reply are deleted afterwards when the bot is admin
- ⚙️ More: edit, delete, assign (👤 members who interacted, or `@username` in the text), due date (📅 presets or Persian natural input, Jalali), clear done, lock, title, new list, quiet hours
- Short-lived ↩️ undo (whole-list snapshot)
- "📋 Convert to list" offer when a list-looking message mentions/replies to the bot
- Reminders: daily / Sat–Wed at preset or custom times, one-time, per-task due reminders (1 h before + at due), turn all off; Tehran time
- Each reminder posts a summary in the group (assignees mentioned, members without personal reminders listed at most once a day) and DMs every member who started the bot (deep link `https://ble.ir/<bot>?start=g<chat_id>`) with their tasks and ✅ buttons that update the group list
- 🌙 Quiet hours, toggleable per group and per user (default on, 23:00–07:00, presets). Group quiet postpones reminders to the window's end; user quiet postpones that user's DMs
- Scheduler thread inside the polling process; state in SQLite with claim rows (compare-and-set on `next_at`, `due_sent` keys, outbox claims) → survives restarts, no duplicate sends; reminders more than 1 h late after downtime are skipped
- Personal list in private chat; Persian digits; slash commands kept as power-user shortcuts (their acks auto-delete after 10 s in groups)

**Stack:** Python 3, raw `requests`, long polling, SQLite.

### Run
```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
export TODO_BALE_BOT_TOKEN=...      # from @BotFather in Bale; never commit it
./run.sh                            # detached, flock-protected auto-restart supervisor; logs to bot.log
./stop.sh
venv/bin/python -m unittest discover -s tests   # tests (fake API, no network)
```
Optional `.env` (git-ignored): `TODO_STRIKE=unicode` (Unicode strikethrough for done tasks; Bale Markdown has none), `TODO_TICK_SECONDS=20`.

### Bale notes (observed)
- Always Markdown (no `parse_mode`); user text is escaped. Group chat ids are positive.
- `editMessageText`, `pinChatMessage`, `setMyCommands`, `getChat` work. `getChatMember`/`getChatAdministrators` returned `403 permission_denied` for this bot, so locked lists fall back to "list creator + whoever locked it".
- Bots receive normal (non-command) group messages when they are admin.

### Files
`bot.py` polling + scheduler thread · `app.py` logic · `render.py` text + keyboards · `dates.py` Jalali / natural dates / quiet hours · `parse.py` commands, aliases, import · `db.py` SQLite + migrations · `api.py` HTTP client · `tests/`

License: MIT
