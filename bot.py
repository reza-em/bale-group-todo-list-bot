"""Bale group to-do list bot - long polling entry point. Token: env TODO_BALE_BOT_TOKEN (never logged)."""
import os, sys, time, fcntl, logging, threading
import db
from api import BaleApi, ApiError, NetError
from app import TodoBot

BASE = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger("bot")

COMMANDS = [
    ("list", "نمایش و سنجاق لیست"), ("new", "شروع لیست تازه"), ("add", "افزودن کار (چند خط = چند کار)"),
    ("done", "تیک زدن: /done 1 3"), ("undo", "برداشتن تیک"), ("del", "حذف کار"), ("edit", "ویرایش: /edit 2 متن"),
    ("import", "ریپلای روی یک لیست آماده"), ("title", "تغییر عنوان"), ("clear_done", "حذف انجام‌شده‌ها"),
    ("reset", "پاک کردن کل لیست"), ("lock", "فقط ادمین‌ها تغییر دهند"), ("unlock", "باز کردن قفل"),
    ("due", "موعد: /due 2 فردا 18"), ("remind", "تنظیم یادآور"), ("stats", "آمار هفته"),
    ("settings", "تنظیمات"), ("help", "راهنما"),
]


def single_instance():
    fd = open(os.path.join(BASE, "bot.lock"), "a+")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("another instance is running - exiting")
        sys.exit(0)
    fd.seek(0); fd.truncate(); fd.write(str(os.getpid())); fd.flush()
    return fd


TICK = int(os.environ.get("TODO_TICK_SECONDS", "20"))


def scheduler(bot):
    """Reminders, DM outbox, delayed deletes. State lives in SQLite (claims), so restarts never double-send."""
    db.connect()
    while True:
        try:
            bot.tick()
        except Exception:
            log.exception("scheduler tick failed")
        time.sleep(TICK)


UI_VERSION = "2"


def upgrade_ui(bot, api):
    """After an update that changes the keyboard, re-render existing list messages ONCE (edit only, never a new
    message or pin, so nobody gets a notification)."""
    if db.kv_get("ui_version") == UI_VERSION:
        return
    for lst in db.q("SELECT * FROM lists WHERE active=1 AND message_id IS NOT NULL"):
        text, tasks = bot._render(lst)
        try:
            api.call("editMessageText", chat_id=lst["chat_id"], message_id=lst["message_id"], text=text,
                     reply_markup=__import__("render").kb_main(lst, tasks))
            log.info("list %s re-rendered with the new UI", lst["id"])
        except (ApiError, NetError) as e:
            log.info("list %s not re-rendered: %s", lst["id"], e)
    db.kv_set("ui_version", UI_VERSION)


def main():
    token = os.environ.get("TODO_BALE_BOT_TOKEN", "").strip()
    if not token:
        print("TODO_BALE_BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(0)
    _lock = single_instance()
    api = BaleApi(token)
    db.connect()
    while True:
        try:
            me = api.call("getMe"); break
        except (ApiError, NetError) as e:
            log.warning("getMe failed: %s; retrying", e); time.sleep(10)
    bot = TodoBot(api, me.get("username"))
    try:
        api.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
        log.info("setMyCommands ok")
    except (ApiError, NetError) as e:
        log.info("setMyCommands not supported: %s", e)
    upgrade_ui(bot, api)
    threading.Thread(target=scheduler, args=(bot,), name="scheduler", daemon=True).start()
    log.info("todo bot started as @%s (pid %s), scheduler every %ss", me.get("username"), os.getpid(), TICK)
    offset = None
    while True:
        try:
            updates = api.get_updates(offset)
        except (ApiError, NetError) as e:
            log.warning("getUpdates: %s", e); time.sleep(5); continue
        for up in updates or []:
            offset = up["update_id"] + 1
            try:
                bot.handle_update(up)
            except Exception:
                log.exception("update %s failed", up.get("update_id"))


if __name__ == "__main__":
    main()
