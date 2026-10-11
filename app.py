"""Group to-do list logic: one pinned list message is the whole UI (buttons + menus edited in place),
text commands stay as power-user shortcuts, reminders post in the group and DM members.
Transport-agnostic: give it any object with call(method, **params)."""
import json, re, time, logging, threading
import db, parse, render, dates
from api import ApiError, NetError
from parse import to_fa_digits as fa

log = logging.getLogger("app")
ADMIN_TTL = 300
PIN_WARN_EVERY = 6 * 3600
ACK_TTL = 10            # seconds before a text-command ack is auto-deleted in groups
PROMPT_TTL = 180        # a pending prompt also accepts the user's next plain message for this long
STALE = 3600            # a scheduled reminder more than this late (bot was down) is skipped, not sent
DUE_GRACE = 6 * 3600

WELCOME = """👋 سلام! من لیست کارهای گروه را نگه می‌دارم.
۱. «📋 ساخت لیست» را بزنید؛ لیست ارسال و سنجاق می‌شود.
۲. با دکمه‌های زیر لیست کار اضافه کنید و تیک بزنید.
۳. من را *ادمین* کنید تا لیست را سنجاق کنم و گروه را تمیز نگه دارم."""

HELP = """📋 *راهنمای لیست کارها*

همه‌چیز با دکمه‌های زیر هر لیست انجام می‌شود:
⬜/✅ تیک زدن · ➕ افزودن (هر خط یک کار) · ⏰ یادآور · 📚 لیست‌ها · ⚙️ بیشتر (ویرایش، حذف، مسئول، موعد، قفل، عنوان، لیست جدید، بایگانی/حذف لیست، ساعت سکوت)
↩️ واگرد تا ۲ دقیقه بعد از هر تغییر.

*چند لیست:* ⚙️ ← «🆕 لیست جدید» (تا ۱۰ لیست). وقتی بیش از یک لیست دارید، پیام «📚 لیست‌ها» سنجاق می‌شود؛ روی هر لیست بزنید تا پایین گفتگو بیاید. هر لیست یادآور خودش را دارد.
دستورهای عددی روی آخرین لیستی که با آن کار کرده‌اید اجرا می‌شوند؛ برای لیست دیگر اول عنوانش را بنویسید: «انجام شد خرید ۳».

در متن کار:  «خرید نان @ali | فردا ۱۸»  ← مسئول و موعد

*یادآور شخصی:* در منوی ⏰ دکمهٔ «🔔 فعال‌سازی یادآور شخصی» را بزنید و ربات را استارت کنید؛ کارهایتان در پیوی هم یادآوری می‌شود و همان‌جا تیک می‌زنید.

*دستورهای متنی (اختیاری):*
/list · /lists · /new عنوان · /add متن · /done ۱ ۳ · /undo ۲ · /del ۳ · /edit ۳ متن · /due ۳ فردا ۱۸ · /title عنوان · /import (ریپلای روی یک لیست) · /clear_done · /reset · /lock · /unlock · /remind
فارسی: «اضافه متن»، «انجام شد ۳»، «برگشت ۳»، «حذف ۳»، «لیست»"""

HELP_PV = """👋 سلام! اینجا هم یک *لیست شخصی* دارید: هر متنی بفرستید (هر خط یک کار) به لیست اضافه می‌شود؛ با دکمه‌ها تیک بزنید.
چند لیست هم می‌توانید بسازید: /new عنوان
برای لیست گروهی، من را به گروه اضافه کنید.
تنظیمات یادآور شخصی: /settings"""

PIN_HINT = "📌 برای سنجاق کردن لیست، لطفاً من را در گروه *ادمین* کنید. لیست بدون سنجاق هم کار می‌کند 🙂"
LOCKED_MSG = "🔒 لیست قفل است؛ فقط سازندهٔ لیست، کسی که قفلش کرده و ادمین‌ها می‌توانند تغییرش دهند."
LIMIT_MSG = "حداکثر %s لیست فعال در هر گفتگو ممکن است؛ یکی را از ⚙️ بایگانی یا حذف کنید." % fa(db.MAX_ACTIVE)
NEWLIST_PROMPT = "🆕 %s عنوان لیست تازه را بنویسید (مثلاً «🛒 خرید»):"
ADD_PROMPT = "➕ %s کار(ها) رو بنویس، هر خط یک کار.\n(مسئول و موعد اختیاری: «خرید نان @ali | فردا ۱۸»)"


def display_name(u):
    if not u:
        return "؟"
    n = ((u.get("first_name") or "") + " " + (u.get("last_name") or "")).strip()
    return n or (u.get("username") or str(u.get("id", "")))


def fa_list(nums):
    return "، ".join(fa(n) for n in nums)


def is_group(chat):
    return (chat or {}).get("type") in ("group", "supergroup")


class TodoBot:
    def __init__(self, api, username=None, clock=time.time):
        self.api, self.username, self.clock = api, username, clock
        self._admin_cache = {}
        self.lock = threading.RLock()   # updates (main thread) and scheduler ticks never interleave
        self._forced = None             # list chosen by title/target for the command being run

    # ================================================================== which list?
    def cur(self, chat):
        """List a text command applies to: an explicitly targeted one, else the most recently used."""
        return self._forced or db.active_list(chat["id"])

    def cur_or_new(self, chat, user):
        return self._forced or db.ensure_list(chat["id"], user.get("id"))

    def label(self, lst):
        """«title»: prefix for acks, only when the chat has several lists."""
        return "«%s» " % lst["title"] if len(db.active_lists(lst["chat_id"])) > 1 else ""

    # ================================================================== transport helpers
    def call(self, method, **p):
        return self.api.call(method, **p)

    def send(self, chat_id, text, reply_to=None, markup=None):
        try:
            return self.call("sendMessage", chat_id=chat_id, text=text, reply_to_message_id=reply_to, reply_markup=markup)
        except ApiError as e:
            if reply_to and e.code in (400, 404):
                return self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=markup)
            raise

    def safe_send(self, *a, **k):
        try:
            return self.send(*a, **k)
        except (ApiError, NetError) as e:
            log.warning("send failed: %s", e)

    def delete(self, chat_id, mid):
        if not mid:
            return False
        try:
            self.call("deleteMessage", chat_id=chat_id, message_id=mid)
            db.kv_set("cap.deleteMessage", "ok")
            return True
        except (ApiError, NetError) as e:
            db.kv_set("cap.deleteMessage", "fail: %s" % e)
            return False

    def ack(self, msg, text, ttl=ACK_TTL):
        """Short reply to a text command; in groups it disappears after a few seconds."""
        chat = msg["chat"]
        res = self.safe_send(chat["id"], text, reply_to=msg.get("message_id"))
        if res and is_group(chat) and ttl:
            db.schedule_delete(chat["id"], res.get("message_id"), self.clock() + ttl)
        return res

    def answer(self, cb, text=None, alert=False):
        try:
            self.call("answerCallbackQuery", callback_query_id=cb["id"], text=text, show_alert=True if alert else None)
        except (ApiError, NetError) as e:
            log.info("answerCallbackQuery failed: %s", e)

    # ================================================================== bookkeeping
    def record(self, chat, user):
        if chat.get("id") is not None:
            db.upsert_chat(chat["id"], chat.get("type"), chat.get("title"))
        if user and user.get("id") and not user.get("is_bot"):
            db.upsert_user(user["id"], display_name(user), user.get("username"))
            if is_group(chat):
                db.touch_member(chat["id"], user["id"], self.clock())
            elif chat.get("type") == "private":
                db.set_user(user["id"], dm_ok=1)

    # ================================================================== permissions
    def is_admin(self, chat_id, user_id):
        key = (chat_id, user_id)
        hit = self._admin_cache.get(key)
        if hit and self.clock() - hit[1] < ADMIN_TTL:
            return hit[0]
        res = False
        try:
            m = self.call("getChatMember", chat_id=chat_id, user_id=user_id)
            res = (m or {}).get("status") in ("creator", "administrator", "owner")
            db.kv_set("cap.getChatMember", "ok")
        except (ApiError, NetError) as e:
            db.kv_set("cap.getChatMember", "fail: %s" % e)
            try:
                admins = self.call("getChatAdministrators", chat_id=chat_id)
                if not isinstance(admins, list):
                    raise ApiError("getChatAdministrators", 0, "unexpected result")
                res = any((a.get("user") or {}).get("id") == user_id for a in admins)
                db.kv_set("cap.getChatAdministrators", "ok")
            except (ApiError, NetError) as e2:
                log.info("admin check unavailable: %s", e2)
                db.kv_set("cap.getChatAdministrators", "fail: %s" % e2)
        self._admin_cache[key] = (res, self.clock())
        return res

    def can_admin(self, lst, chat, user_id):
        if not is_group(chat):
            return True
        return user_id in (lst["creator_id"], lst["locked_by"]) or self.is_admin(chat["id"], user_id)

    def can_modify(self, lst, chat, user_id):
        return not lst["locked"] or self.can_admin(lst, chat, user_id)

    # ================================================================== list message
    def _render(self, lst):
        tasks = db.tasks(lst["id"])
        return render.render_text(lst, tasks, now=self.clock(), rems=db.list_reminders(lst["id"])), tasks

    def refresh(self, lst, repost=False, kb=None):
        """Edit the list message in place (optionally with a menu keyboard); send + pin a new one if needed."""
        lst = db.get_list(lst["id"])
        text, tasks = self._render(lst)
        kb = kb or render.kb_main(lst, tasks, self.clock())
        mid = lst["message_id"]
        if mid and not repost:
            try:
                self.call("editMessageText", chat_id=lst["chat_id"], message_id=mid, text=text, reply_markup=kb)
                db.kv_set("cap.editMessageText", "ok")
                return mid
            except NetError as e:
                log.warning("edit failed (network), not reposting: %s", e)
                return mid
            except ApiError as e:
                if "not modified" in e.desc.lower():
                    return mid
                log.info("edit failed (%s) -> sending a new list message", e)
        try:
            res = self.call("sendMessage", chat_id=lst["chat_id"], text=text, reply_markup=kb)
        except (ApiError, NetError) as e:
            log.warning("could not send list message: %s", e)
            return None
        new_mid = (res or {}).get("message_id")
        if new_mid:
            db.set_list(lst["id"], message_id=new_mid)
            if repost and mid and mid != new_mid:
                self.delete(lst["chat_id"], mid)
            self.pin_for(lst, new_mid)
        return new_mid

    # ================================================================== several lists: pin + index
    def lists_with_tasks(self, chat_id):
        return [(l, db.tasks(l["id"])) for l in db.active_lists(chat_id)]

    def pin_for(self, lst, mid):
        """One list -> pin the list itself. Several -> one pinned «📚» index (works whether or not Bale keeps
        several pins, and does not re-pin on every tap)."""
        if len(db.active_lists(lst["chat_id"])) >= 2:
            self.ensure_index(lst["chat_id"])
        else:
            self.pin(lst["chat_id"], mid)

    def ensure_index(self, chat_id, repost=False):
        lw = self.lists_with_tasks(chat_id)
        c = db.chat(chat_id)
        text = render.index_text(lw, private=c["type"] == "private", now=self.clock())
        kb = render.kb_index(lw)
        mid = c["index_mid"]
        if mid and not repost:
            try:
                self.call("editMessageText", chat_id=chat_id, message_id=mid, text=text, reply_markup=kb)
                return mid
            except NetError:
                return mid
            except ApiError as e:
                if "not modified" in e.desc.lower():
                    return mid
        res = self.safe_send(chat_id, text, markup=kb)
        new_mid = (res or {}).get("message_id")
        if not new_mid:
            return mid
        db.set_chat(chat_id, index_mid=new_mid)
        if mid and mid != new_mid:
            self.delete(chat_id, mid)
        if len(lw) >= 2:
            for l, _t in lw:      # the index replaces the single-list pin (ignored if Bale keeps one pin only)
                if l["message_id"]:
                    try:
                        self.call("unpinChatMessage", chat_id=chat_id, message_id=l["message_id"])
                        db.kv_set("cap.unpinChatMessage", "ok")
                    except (ApiError, NetError) as e:
                        db.kv_set("cap.unpinChatMessage", "fail: %s" % e)
            self.pin(chat_id, new_mid)
        return new_mid

    def update_index(self, chat_id):
        lists = db.active_lists(chat_id)
        c = db.chat(chat_id)
        if len(lists) >= 2:
            self.ensure_index(chat_id)
        elif c["index_mid"]:          # back to a single list: drop the index, pin the list again
            if not self.delete(chat_id, c["index_mid"]):
                try:
                    self.call("unpinChatMessage", chat_id=chat_id, message_id=c["index_mid"])
                except (ApiError, NetError):
                    pass
            db.set_chat(chat_id, index_mid=None)
            if lists and lists[0]["message_id"]:
                self.pin(chat_id, lists[0]["message_id"])

    def new_list(self, chat, user, title=None):
        if len(db.active_lists(chat["id"])) >= db.MAX_ACTIVE:
            return None
        lst = db.create_list(chat["id"], user.get("id"), title, now=self.clock())
        self.refresh(lst)
        return lst

    def close_list(self, lst, delete=False):
        chat_id = lst["chat_id"]
        if delete:
            if not self.delete(chat_id, lst["message_id"]):
                self._edit_plain(chat_id, {"message_id": lst["message_id"]}, "🗑 این لیست حذف شد.")
            db.delete_list(lst["id"])
        else:
            text, _ = self._render(lst)
            self._edit_plain(chat_id, {"message_id": lst["message_id"]}, text + "\n📦 بایگانی شد")
            db.archive_list(lst["id"])
        self.update_index(chat_id)

    def pin(self, chat_id, mid):
        try:
            self.call("pinChatMessage", chat_id=chat_id, message_id=mid, disable_notification=True)
            db.kv_set("cap.pinChatMessage", "ok")
            return True
        except (ApiError, NetError) as e:
            log.info("pin failed in %s: %s", chat_id, e)
            db.kv_set("cap.pinChatMessage", "fail: %s" % e)
            if db.chat(chat_id)["type"] in ("group", "supergroup"):
                key = "pinwarn.%s" % chat_id
                if self.clock() - float(db.kv_get(key, 0)) > PIN_WARN_EVERY:
                    db.kv_set(key, self.clock())
                    res = self.safe_send(chat_id, PIN_HINT)
                    if res:
                        db.schedule_delete(chat_id, res.get("message_id"), self.clock() + 120)
            return False

    def mutate(self, lst, label, fn):
        """Run a change with a whole-list snapshot so «↩️ واگرد» can restore it."""
        snap = db.snapshot(lst["id"])
        db.use_list(lst["id"], self.clock())
        result = fn()
        db.set_list(lst["id"], undo_json=json.dumps(snap, ensure_ascii=False), undo_at=self.clock(),
                    undo_label=label, undo_shown=1, updated_at=self.clock())
        self.after_change(lst)
        return result

    def after_change(self, lst):
        self.refresh(lst)
        tasks = db.tasks(lst["id"])
        all_done = bool(tasks) and all(t["done"] for t in tasks)
        lst = db.get_list(lst["id"])
        if all_done and not lst["celebrated"]:
            db.set_list(lst["id"], celebrated=1)
            self.safe_send(lst["chat_id"], "🎉 آفرین! همهٔ %s کار انجام شد. خسته نباشید! 👏" % fa(len(tasks)))
        elif not all_done and lst["celebrated"]:
            db.set_list(lst["id"], celebrated=0)
        self.update_index(lst["chat_id"])

    # ================================================================== task helpers
    def add_items(self, lst, lines, user, done_flags=None):
        """lines: task strings (may carry '@user' and '| due'); returns number added."""
        items, metas = [], []
        for i, line in enumerate(lines):
            text, uname, due_s = parse.extract_meta(line)
            due = dates.parse_due(due_s, self.clock()) if due_s else None
            if due_s and not due:      # not a date after all: keep the text as written
                text = parse.extract_meta(line.replace("|", "/").replace("⏰", ""))[0]
            if not text:
                continue
            items.append((text[:300], bool(done_flags and done_flags[i])))
            metas.append((uname, due))
        if not items:
            return 0

        def go():
            ids = db.add_tasks(lst["id"], items, user.get("id"), done_by=display_name(user))
            for tid, (uname, due) in zip(ids, metas):
                kw = {}
                if uname:
                    kw.update(self._assignee_by_username(uname))
                if due:
                    kw.update(due_at=due[0], due_has_time=1 if due[1] else 0)
                if kw:
                    db.set_task(tid, **kw)
        self.mutate(lst, "افزودن %s کار" % fa(len(items)), go)
        return len(items)

    def _assignee_by_username(self, uname):
        u = db.user_by_username(uname)
        if u:
            return {"assignee_id": u["user_id"], "assignee_username": u["username"], "assignee_name": u["name"]}
        return {"assignee_id": None, "assignee_username": uname, "assignee_name": None}

    def task_in(self, lst, task_id):
        t = db.get_task(task_id)
        return t if t and t["list_id"] == lst["id"] else None

    def num_of(self, lst, task_id):
        for i, t in enumerate(db.tasks(lst["id"]), 1):
            if t["id"] == task_id:
                return i
        return 0

    def toggle(self, lst, t, user):
        done = not t["done"]
        self.mutate(lst, ("تیک " if done else "برداشتن تیک ") + fa(self.num_of(lst, t["id"])),
                    lambda: db.set_done(t["id"], done, display_name(user), user.get("id"), self.clock()))
        return done

    # ================================================================== prompts (ForceReply)
    def ask(self, chat, user, kind, lst, task_id=None, text=None, payload=None):
        who = ("@" + user["username"]) if user.get("username") else display_name(user)
        tpl = text or ADD_PROMPT
        body = (tpl % who if "%s" in tpl else tpl) + "\n↩️ روی همین پیام ریپلای کنید."
        res = None
        try:
            res = self.call("sendMessage", chat_id=chat["id"], text=body,
                            reply_markup={"force_reply": True, "selective": True})
            db.kv_set("cap.force_reply", "accepted")
        except ApiError as e:
            db.kv_set("cap.force_reply", "rejected: %s" % e)
            res = self.safe_send(chat["id"], body)
        except NetError as e:
            log.warning("prompt failed: %s", e)
        if res and res.get("message_id"):
            db.add_prompt(chat["id"], res["message_id"], kind, lst["id"] if lst else None, task_id, user.get("id"),
                          payload, self.clock())
            db.schedule_delete(chat["id"], res["message_id"], self.clock() + 600)
        return res

    def find_prompt(self, msg):
        chat, user = msg["chat"], msg.get("from") or {}
        rep = msg.get("reply_to_message")
        if rep:
            p = db.prompt(chat["id"], rep.get("message_id"))
            return p if p and p["kind"] != "offer" else None
        text = msg.get("text") or ""
        if text and not text.startswith("/"):
            return db.latest_prompt_for(chat["id"], user.get("id"), self.clock() - PROMPT_TTL)
        return None

    def handle_prompt(self, p, msg):
        chat, user, text = msg["chat"], msg.get("from") or {}, (msg.get("text") or "").strip()
        if p["kind"] == "newlist":
            if not text:
                return
            if self.new_list(chat, user, text.replace("\n", " ")[:100]) is None:
                self.ack(msg, LIMIT_MSG)
            db.drop_prompt(chat["id"], p["message_id"])
            self.delete(chat["id"], p["message_id"])
            if is_group(chat):
                self.delete(chat["id"], msg.get("message_id"))
            return
        lst = db.get_list(p["list_id"]) if p["list_id"] else None
        if lst and not lst["active"]:
            lst = db.active_list(chat["id"])
        if not lst:
            db.drop_prompt(chat["id"], p["message_id"])
            return
        if not self.can_modify(lst, chat, user.get("id")):
            self.ack(msg, LOCKED_MSG)
            return
        ok, kind = True, p["kind"]
        if kind == "add":
            ok = self.add_items(lst, parse.parse_add_lines(text), user) > 0
        elif kind == "edit":
            t = self.task_in(lst, p["task_id"])
            if t and text:
                self.mutate(lst, "ویرایش " + fa(self.num_of(lst, t["id"])),
                            lambda: db.edit_task(t["id"], text.replace("\n", " ")[:300]))
        elif kind == "title":
            if text:
                self.mutate(lst, "تغییر عنوان", lambda: db.set_list(lst["id"], title=text.replace("\n", " ")[:100]))
        elif kind == "due":
            t = self.task_in(lst, p["task_id"])
            due = dates.parse_due(text, self.clock())
            if not due:
                ok = False
                self.ack(msg, "🤔 تاریخ را نفهمیدم. مثال: «فردا ۱۸»، «شنبه»، «۲۵ مهر ۱۰:۳۰»، «۲ ساعت دیگه»", ttl=20)
            elif t:
                self.mutate(lst, "موعد " + fa(self.num_of(lst, t["id"])),
                            lambda: db.set_task(t["id"], due_at=due[0], due_has_time=1 if due[1] else 0))
        elif kind == "remtime":
            ok = self.add_reminder_from_text(lst, p["payload"], text, user, msg)
        if ok:
            db.drop_prompt(chat["id"], p["message_id"])
            self.delete(chat["id"], p["message_id"])
            if is_group(chat):
                self.delete(chat["id"], msg.get("message_id"))

    # ================================================================== updates
    def handle_update(self, up):
        with self.lock:
            kind = next((k for k in up if k != "update_id"), "?")
            m = up.get(kind) if isinstance(up.get(kind), dict) else {}
            ctype = ((m.get("chat") or (m.get("message") or {}).get("chat") or {}).get("type"))
            log.info("update %s: %s in %s chat%s", up.get("update_id"), kind, ctype,
                     " (text starts with /)" if (m.get("text") or "").startswith("/") else "")
            if "callback_query" in up:
                return self.on_callback(up["callback_query"])
            if "message" in up:
                return self.on_message(up["message"])

    def on_message(self, msg):
        chat, user = msg.get("chat") or {}, msg.get("from") or {}
        if chat.get("id") is None:
            return
        self.record(chat, user)
        for m in msg.get("new_chat_members") or []:
            if not m.get("is_bot"):
                self.record(chat, m)
            elif (m.get("username") or "").lower() == (self.username or "").lower():
                return self.welcome(chat)
        text = msg.get("text") or msg.get("caption") or ""
        if not text:
            return
        p = self.find_prompt(msg)
        if p:
            return self.handle_prompt(p, msg)
        reply = msg.get("reply_to_message")
        cmd = parse.parse_command(text, self.username, is_reply=bool(reply))
        if not cmd:
            if chat.get("type") == "private" and not text.startswith("/"):
                cmd = ("add", text)
            elif is_group(chat) and parse.looks_like_list(text) and (
                    parse.mentions(text, self.username) or
                    (((reply or {}).get("from") or {}).get("username") or "").lower() == (self.username or "").lower()):
                return self.offer_import(msg)
            else:
                return
        name, arg = cmd
        log.info("cmd %s in %s chat", name, chat.get("type"))
        slash = text.lstrip().startswith("/")
        target, arg = self.resolve_target(msg, chat, user, name, arg, slash)
        if target is False:
            return
        return self.run_cmd(name, msg, chat, user, arg, reply, target)

    def run_cmd(self, name, msg, chat, user, arg, reply, target=None):
        self._forced = target
        try:
            return getattr(self, "cmd_" + name)(msg, chat, user, arg, reply)
        finally:
            self._forced = None

    TARGETABLE = ("done", "undo", "del", "edit", "due")

    @staticmethod
    def title_key(s):
        return re.sub(r"^[\W_]+", "", parse.norm(s or "").lower()).strip()

    def resolve_target(self, msg, chat, user, name, arg, slash):
        """«انجام شد خرید 3»: a title prefix before the numbers picks the list. Returns (list|None, arg) or
        (False, None) when the bot asked which list (or the message was just chatter)."""
        lists = db.active_lists(chat["id"])
        if name not in self.TARGETABLE or len(lists) < 2:
            return None, arg
        m = re.match(r"^([^\d۰-۹٠-٩#]+?)\s+([#\d۰-۹٠-٩].*)$", (arg or "").strip(), re.S)
        if not m:
            return None, arg                       # numbers only -> most recently used list
        key = self.title_key(m.group(1))
        hits = [l for l in lists if key and self.title_key(l["title"]).startswith(key)]
        if len(hits) == 1:
            return hits[0], m.group(2)
        if not hits and not slash:
            return False, None                     # «انجام شد دیگه 2 تا» etc. is chatter, not a command
        res = self.safe_send(chat["id"], "📚 کدام لیست؟", reply_to=msg.get("message_id"),
                             markup=render.kb_pick_list(hits or lists, "tg:%d"))
        if res and res.get("message_id"):
            db.add_prompt(chat["id"], res["message_id"], "target", None, None, user.get("id"),
                          json.dumps({"name": name, "arg": m.group(2)}, ensure_ascii=False), self.clock())
            db.schedule_delete(chat["id"], res["message_id"], self.clock() + 300)
        return False, None

    def welcome(self, chat):
        if chat.get("type") == "private":
            return self.safe_send(chat["id"], HELP_PV)
        if db.active_list(chat["id"]):
            kb = {"inline_keyboard": [[render.B("📋 نمایش لیست", "show0")]]}
        else:
            kb = {"inline_keyboard": [[render.B("📋 ساخت لیست", "new0")]]}
        self.safe_send(chat["id"], WELCOME, markup=kb)

    def offer_import(self, msg):
        chat = msg["chat"]
        n = len(parse.parse_import(msg.get("text") or ""))
        res = self.safe_send(chat["id"], "📋 %s خط شبیه لیست است. به لیست کارها اضافه شود؟" % fa(n),
                             reply_to=msg.get("message_id"),
                             markup={"inline_keyboard": [[render.B("📋 تبدیل به لیست", "cv"), render.B("✖️", "cvx")]]})
        if res and res.get("message_id"):
            db.add_prompt(chat["id"], res["message_id"], "offer", None, None, (msg.get("from") or {}).get("id"),
                          msg.get("text"), self.clock())
            db.schedule_delete(chat["id"], res["message_id"], self.clock() + 900)

    def _guard(self, lst, chat, user, msg):
        if self.can_modify(lst, chat, user.get("id")):
            return True
        self.ack(msg, LOCKED_MSG)
        return False

    # ================================================================== text commands (power users)
    def cmd_help(self, msg, chat, user, arg, reply):
        if (msg.get("text") or "").startswith("/start"):
            return self.cmd_start(msg, chat, user, arg)
        self.safe_send(chat["id"], HELP if is_group(chat) else HELP_PV + "\n\n" + HELP)

    def cmd_start(self, msg, chat, user, arg):
        if chat.get("type") != "private":
            return self.welcome(chat)
        db.set_user(user["id"], dm_ok=1)
        arg = (arg or "").strip()
        if arg.startswith("g"):
            try:
                gid = int(arg[1:].replace("m", "-"))
            except ValueError:
                gid = None
            g = db.q("SELECT * FROM chats WHERE chat_id=?", (gid,)) if gid is not None else []
            if g:
                db.touch_member(gid, user["id"], self.clock())
                return self.safe_send(chat["id"], "✅ یادآور شخصی برای گروه «%s» فعال شد.\nموقع یادآوری، کارهایتان را "
                                      "همین‌جا می‌فرستم و از همین‌جا تیک می‌زنید." %
                                      render.md_escape(g[0]["title"] or "گروه"), markup=self.kb_user(user["id"]))
        self.safe_send(chat["id"], HELP_PV)

    def cmd_settings(self, msg, chat, user, arg, reply):
        if chat.get("type") != "private":
            lst = self.cur_or_new(chat, user)
            return self.refresh(lst, kb=render.kb_more(lst, db.chat(chat["id"])))
        self.safe_send(chat["id"], self.user_settings_text(user["id"]), markup=self.kb_user(user["id"]))

    def cmd_remind(self, msg, chat, user, arg, reply):
        lst = self.cur_or_new(chat, user)
        self.refresh(lst, kb=self.kb_rem(lst))

    def cmd_list(self, msg, chat, user, arg, reply):
        self.refresh(self.cur_or_new(chat, user), repost=True)

    def cmd_lists(self, msg, chat, user, arg, reply):
        if not db.active_lists(chat["id"]):
            db.ensure_list(chat["id"], user.get("id"))
        self.ensure_index(chat["id"], repost=True)

    def cmd_new(self, msg, chat, user, arg, reply):
        if len(db.active_lists(chat["id"])) >= db.MAX_ACTIVE:
            return self.ack(msg, LIMIT_MSG)
        title = arg.strip().replace("\n", " ")[:100] if arg and arg.strip() else None
        if not title:
            return self.ask(chat, user, "newlist", None, text=NEWLIST_PROMPT)
        self.new_list(chat, user, title)

    def cmd_add(self, msg, chat, user, arg, reply):
        lines = parse.parse_add_lines(arg)
        if not lines and reply:
            return self.cmd_import(msg, chat, user, arg, reply)
        lst = self.cur_or_new(chat, user)
        if not lines:
            return self.ask(chat, user, "add", lst)
        if not self._guard(lst, chat, user, msg):
            return
        start = len(db.tasks(lst["id"])) + 1
        n = self.add_items(lst, lines, user)
        if chat.get("type") != "private":
            self.ack(msg, "➕ %s کار اضافه شد (%s%s)." % (fa(n), fa(start), "" if n == 1 else " تا " + fa(start + n - 1)))

    def cmd_import(self, msg, chat, user, arg, reply):
        src = (reply or {}).get("text") or (reply or {}).get("caption") or arg or ""
        items = parse.parse_import(src)
        if not items:
            return self.ack(msg, "📥 روی پیامی که لیست کارها در آن است ریپلای کنید و /import بفرستید (هر خط یک کار).")
        lst = self.cur_or_new(chat, user)
        if not self._guard(lst, chat, user, msg):
            return
        self.add_items(lst, [t for t, _d in items], user, [d for _t, d in items])
        nd = sum(1 for _t, d in items if d)
        self.ack(msg, "📥 %s کار وارد لیست شد%s." % (fa(len(items)), " (%s انجام‌شده)" % fa(nd) if nd else ""))

    def _nums(self, msg, arg, lst, usage):
        nums, bad = parse.parse_numbers(arg)
        if not nums or bad:
            self.ack(msg, usage)
            return None
        total = len(db.tasks(lst["id"]))
        wrong = [n for n in nums if not 1 <= n <= total]
        if wrong:
            self.ack(msg, "⚠️ شمارهٔ %s در لیست نیست (لیست %s کار دارد)." % (fa_list(wrong), fa(total)))
            return None
        return nums

    def _active_nonempty(self, msg, chat):
        lst = self.cur(chat)
        if not lst or not db.tasks(lst["id"]):
            self.ack(msg, "📋 لیست خالی است. «➕ افزودن» را بزنید یا /add بنویسید.")
            return None
        return lst

    def _set_done(self, msg, chat, user, arg, done):
        lst = self._active_nonempty(msg, chat)
        if not lst or not self._guard(lst, chat, user, msg):
            return
        cmd = "done" if done else "undo"
        nums = self._nums(msg, arg, lst, "مثال: /%s ۳  یا  /%s ۱ ۳ ۵" % (cmd, cmd))
        if not nums:
            return

        def go():
            for n in nums:
                t = db.task_by_num(lst["id"], n)
                if bool(t["done"]) != done:
                    db.set_done(t["id"], done, display_name(user), user.get("id"), self.clock())
        self.mutate(lst, ("تیک " if done else "برداشتن تیک ") + fa_list(nums), go)
        tasks = db.tasks(lst["id"])
        if done:
            self.ack(msg, "✅ %s%s (%s از %s)" % (self.label(lst), fa_list(nums), fa(sum(t["done"] for t in tasks)),
                                                fa(len(tasks))))
        else:
            self.ack(msg, "↩️ %sتیک %s برداشته شد." % (self.label(lst), fa_list(nums)))

    def cmd_done(self, msg, chat, user, arg, reply):
        self._set_done(msg, chat, user, arg, True)

    def cmd_undo(self, msg, chat, user, arg, reply):
        self._set_done(msg, chat, user, arg, False)

    def cmd_del(self, msg, chat, user, arg, reply):
        lst = self._active_nonempty(msg, chat)
        if not lst or not self._guard(lst, chat, user, msg):
            return
        nums = self._nums(msg, arg, lst, "مثال: /del ۳  یا  /del ۲ ۴")
        if not nums:
            return
        ids = [db.task_by_num(lst["id"], n)["id"] for n in nums]
        self.mutate(lst, "حذف " + fa_list(nums), lambda: [db.del_task(i) for i in ids])
        self.ack(msg, "🗑 %s%s حذف شد." % (self.label(lst), fa_list(nums)))

    def cmd_edit(self, msg, chat, user, arg, reply):
        n, text = parse.split_num_text(arg)
        if not n or not text:
            return self.ack(msg, "مثال: /edit ۳ متن جدید")
        lst = self.cur(chat)
        t = db.task_by_num(lst["id"], n) if lst else None
        if not t:
            return self.ack(msg, "⚠️ کار شمارهٔ %s پیدا نشد." % fa(n))
        if not self._guard(lst, chat, user, msg):
            return
        self.mutate(lst, "ویرایش " + fa(n), lambda: db.edit_task(t["id"], text[:300]))
        self.ack(msg, "✏️ کار %s ویرایش شد." % fa(n))

    def cmd_due(self, msg, chat, user, arg, reply):
        n, text = parse.split_num_text(arg)
        lst = self.cur(chat)
        t = db.task_by_num(lst["id"], n) if (lst and n) else None
        if not t:
            return self.ack(msg, "مثال: /due ۳ فردا ۱۸  (یا «/due ۳ حذف» برای برداشتن موعد)")
        if not self._guard(lst, chat, user, msg):
            return
        if parse.norm(text).strip() in ("حذف", "-", "x", "off"):
            self.mutate(lst, "حذف موعد " + fa(n), lambda: db.set_task(t["id"], due_at=None, due_has_time=0))
            return self.ack(msg, "⏰ موعد کار %s برداشته شد." % fa(n))
        due = dates.parse_due(text, self.clock())
        if not due:
            return self.ack(msg, "🤔 تاریخ را نفهمیدم. مثال: «فردا ۱۸»، «شنبه»، «۲۵ مهر ۱۰:۳۰»")
        self.mutate(lst, "موعد " + fa(n), lambda: db.set_task(t["id"], due_at=due[0], due_has_time=1 if due[1] else 0))
        self.ack(msg, "⏰ موعد کار %s: %s" % (fa(n), dates.fmt_due(due[0], due[1], self.clock())))

    def cmd_title(self, msg, chat, user, arg, reply):
        lst = self.cur_or_new(chat, user)
        if not arg.strip():
            return self.ask(chat, user, "title", lst, text="🏷 %s عنوان تازهٔ لیست را بنویسید.")
        if not self._guard(lst, chat, user, msg):
            return
        self.mutate(lst, "تغییر عنوان", lambda: db.set_list(lst["id"], title=arg.strip().replace("\n", " ")[:100]))
        self.ack(msg, "🏷 عنوان لیست عوض شد.")

    def cmd_clear_done(self, msg, chat, user, arg, reply):
        lst = self.cur(chat)
        if not lst:
            return self.ack(msg, "📋 لیستی وجود ندارد.")
        if not self._guard(lst, chat, user, msg):
            return
        n = self.mutate(lst, "پاک کردن انجام‌شده‌ها", lambda: db.clear_done(lst["id"]))
        self.ack(msg, "🧹 %s کار انجام‌شده پاک شد." % fa(n) if n else "کار انجام‌شده‌ای نبود.")

    def cmd_reset(self, msg, chat, user, arg, reply):
        lst = self._active_nonempty(msg, chat)
        if not lst or not self._guard(lst, chat, user, msg):
            return
        self.safe_send(chat["id"], "⚠️ همهٔ %s کار لیست پاک شود؟" % fa(len(db.tasks(lst["id"]))),
                       reply_to=msg.get("message_id"),
                       markup=render.kb_confirm("rs:%d:y" % lst["id"], "rs:%d:n" % lst["id"], "✅ بله، پاک کن", "❌ نه"))

    def _set_lock(self, chat, user, locked, lst=None):
        lst = lst or self.cur_or_new(chat, user)
        if not self.can_admin(lst, chat, user.get("id")):
            return False
        self.mutate(lst, "قفل" if locked else "باز کردن قفل",
                    lambda: db.set_list(lst["id"], locked=1 if locked else 0, locked_by=user.get("id") if locked else None))
        return True

    def cmd_lock(self, msg, chat, user, arg, reply):
        if chat.get("type") == "private":
            return self.ack(msg, "این لیست شخصی است و فقط خودتان به آن دسترسی دارید 🙂")
        ok = self._set_lock(chat, user, True)
        self.ack(msg, LOCKED_MSG if ok else "فقط ادمین‌ها یا سازندهٔ لیست می‌توانند قفل کنند.")

    def cmd_unlock(self, msg, chat, user, arg, reply):
        if chat.get("type") == "private":
            return self.ack(msg, "این لیست شخصی است 🙂")
        ok = self._set_lock(chat, user, False)
        self.ack(msg, "🔓 قفل باز شد." if ok else "فقط ادمین‌ها، سازندهٔ لیست یا کسی که قفل کرده می‌تواند باز کند.")

    # ================================================================== reminders: setup (per list)
    def kb_rem(self, lst):
        c = db.chat(lst["chat_id"])
        return render.kb_rem(lst, db.list_reminders(lst["id"]), c, self.username, private=c["type"] == "private")

    def schedule_next(self, chat_id, kind, minute, after):
        nxt = dates.next_daily(after, minute, dates.WORK_DAYS if kind == "work" else None)
        c = db.chat(chat_id)
        if nxt and c["quiet_on"]:
            nxt = dates.quiet_end(nxt, c["quiet_start"], c["quiet_end"])
        return nxt

    def toggle_daily(self, lst, kind, minute, user):
        rows = [r for r in db.list_reminders(lst["id"]) if r["kind"] == kind and r["minute"] == minute]
        if rows:
            for r in rows:
                db.x("UPDATE reminders SET active=0 WHERE id=?", (r["id"],))
            return False
        db.add_reminder(lst["chat_id"], kind, minute, self.schedule_next(lst["chat_id"], kind, minute, self.clock()),
                        user.get("id"), lst["id"])
        return True

    def add_once(self, lst, ts, user):
        c = db.chat(lst["chat_id"])
        if c["quiet_on"]:
            ts = dates.quiet_end(ts, c["quiet_start"], c["quiet_end"])
        db.add_reminder(lst["chat_id"], "once", None, ts, user.get("id"), lst["id"])
        return ts

    def add_reminder_from_text(self, lst, kind, text, user, msg):
        if kind == "once":
            due = dates.parse_due(text, self.clock())
            if not due:
                self.ack(msg, "🤔 زمان را نفهمیدم. مثال: «فردا ۹»، «۱۸:۳۰»، «۲ ساعت دیگه»", ttl=20)
                return False
            ts = due[0] if due[1] else dates.due_effective(due[0], False)
            if ts <= self.clock():
                self.ack(msg, "⏳ این زمان گذشته است.", ttl=20)
                return False
            self.add_once(lst, ts, user)
        else:
            m = re.fullmatch(r"\s*(?:ساعت\s*)?(\d{1,2})(?:[:٫.](\d{2}))?\s*", parse.to_en_digits(text))
            if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
                self.ack(msg, "🤔 ساعت را به شکل «۱۸» یا «۱۸:۳۰» بنویسید.", ttl=20)
                return False
            minute = int(m.group(1)) * 60 + int(m.group(2) or 0)
            if not [r for r in db.list_reminders(lst["id"]) if r["kind"] == kind and r["minute"] == minute]:
                self.toggle_daily(lst, kind, minute, user)
        self.refresh(lst, kb=self.kb_rem(lst))
        return True

    def reschedule_chat(self, chat_id):
        """Quiet-hours change (group-level): recompute next_at of every recurring reminder in the chat."""
        for r in db.reminders(chat_id):
            if r["kind"] != "once":
                db.x("UPDATE reminders SET next_at=? WHERE id=?",
                     (self.schedule_next(chat_id, r["kind"], r["minute"], self.clock()), r["id"]))

    # ================================================================== reminders: firing
    def remaining(self, lst):
        return [(i, t) for i, t in enumerate(db.tasks(lst["id"]), 1) if not t["done"]]

    def fire_reminder(self, chat_id, now):
        """All active lists of the chat (used by tests / manual triggers)."""
        return self.fire_reminders(chat_id, [l["id"] for l in db.active_lists(chat_id)], now)

    def fire_reminders(self, chat_id, list_ids, now):
        """One post for the lists whose reminders fire together (combined when several), then queue DMs."""
        lists = [l for l in (db.get_list(i) for i in dict.fromkeys(list_ids)) if l and l["active"]]
        sections = [(l, self.remaining(l)) for l in lists]
        sections = [(l, r) for l, r in sections if r]
        if not sections:
            return False
        c = db.chat(chat_id)
        group = c["type"] in ("group", "supergroup")
        if len(sections) == 1:
            l, rem = sections[0]
            lines = ["⏰ *یادآوری* — %s" % render.md_escape(l["title"]), ""]
            lines += [render.task_line(i, t, now) for i, t in rem[:15]]
            if len(rem) > 15:
                lines.append("… و %s کار دیگر" % fa(len(rem) - 15))
            lines += ["", "باقی‌مانده: %s از %s" % (fa(len(rem)), fa(len(db.tasks(l["id"]))))]
        else:
            lines = ["⏰ *یادآوری*"]
            for l, rem in sections:
                lines += ["", "📋 *%s* (%s کار باز)" % (render.md_escape(l["title"]), fa(len(rem)))]
                lines += [render.task_line(i, t, now) for i, t in rem[:10]]
                if len(rem) > 10:
                    lines.append("… و %s کار دیگر" % fa(len(rem) - 10))
        markup = None
        if group:
            today = dates.now_dt(now).strftime("%Y-%m-%d")
            missing = [m for m in db.members(chat_id) if not m["dm_ok"]]
            if missing and c["nostart_day"] != today:
                db.set_chat(chat_id, nostart_day=today)
                lines += ["", "🔕 یادآور شخصی ندارند: " + "، ".join(render.md_escape(m["name"] or "?") for m in missing[:10])]
            if self.username:
                markup = {"inline_keyboard": [[{"text": "🔔 فعال‌سازی یادآور شخصی",
                                                "url": render.deep_link(self.username, chat_id)}]]}
        self.safe_send(chat_id, "\n".join(lines), markup=markup)
        if group:
            self.queue_member_dms([l for l, _r in sections], c, now)
        return True

    def tasks_for_user(self, lst, u):
        mine, free = [], []
        for i, t in self.remaining(lst):
            if t["assignee_id"] == u["user_id"] or (
                    t["assignee_username"] and u["username"] and t["assignee_username"].lower() == u["username"].lower()):
                mine.append((i, t))
            elif not t["assignee_id"] and not t["assignee_username"]:
                free.append((i, t))
        return mine, free

    def dm_payload(self, lists, c, u, now, head="⏰ یادآوری"):
        """DM for one member: their tasks + unassigned ones, grouped by list; ✅ buttons tick the right list."""
        if isinstance(lists, dict) or hasattr(lists, "keys"):
            lists = [lists]
        parts = [(l, *self.tasks_for_user(l, u)) for l in lists]
        parts = [(l, mine, free) for l, mine, free in parts if mine or free]
        if not parts:
            return None, None
        multi = len(parts) > 1
        lines = ["%s از «%s»%s" % (head, render.md_escape(c["title"] or "گروه"),
                                   "" if multi else " — " + render.md_escape(parts[0][0]["title"]))]
        buttons = []
        for l, mine, free in parts:
            lines.append("")
            if multi:
                lines.append("📋 *%s*" % render.md_escape(l["title"]))
            if mine:
                lines.append("👤 کارهای شما:")
                lines += [render.task_line(i, t, now) for i, t in mine]
            if free:
                lines.append("بی‌مسئول:")
                lines += [render.task_line(i, t, now) for i, t in free[:10]]
                if len(free) > 10:
                    lines.append("… و %s کار دیگر" % fa(len(free) - 10))
            pre = (render._short(l["title"], 10) + " ") if multi else ""
            buttons += [(pre, i, t) for i, t in mine + free]
        rows = [[render.B(render._short("✅ %s%s. %s" % (pre, fa(i), t["text"]), 34), "dt:%d" % t["id"])]
                for pre, i, t in buttons[:8]]
        rows.append([render.B("🔕 قطع یادآورها", "um"), render.B("⚙️ تنظیمات", "uset")])
        return "\n".join(lines), {"inline_keyboard": rows}

    def dm_not_before(self, u, now):
        return dates.quiet_end(now, u["quiet_start"], u["quiet_end"]) if u["quiet_on"] else now

    def queue_member_dms(self, lists, c, now):
        n = 0
        for u in db.members(c["chat_id"]):
            if not u["dm_ok"] or u["muted"]:
                continue
            text, kb = self.dm_payload(lists, c, u, now)
            if text:
                db.queue_dm(u["user_id"], c["chat_id"], text, kb, self.dm_not_before(u, now), now)
                n += 1
        return n

    def fire_due(self, lst, t, kind, now):
        c = db.chat(lst["chat_id"])
        n = self.num_of(lst, t["id"])
        when = "تا یک ساعت دیگر" if kind == "pre" else "رسیده"
        who = render.who(t)
        text = "⏰ موعد کار %s %s: %s%s" % (fa(n), when, render.md_escape(t["text"]),
                                           ("  — 👤 " + render.md_escape(who)) if who else "")
        self.safe_send(lst["chat_id"], text,
                       markup={"inline_keyboard": [[render.B("✅ انجام شد", "t:%d:%d" % (lst["id"], t["id"]))]]})
        if c["type"] in ("group", "supergroup"):
            u = db.user(t["assignee_id"]) if t["assignee_id"] else (
                db.user_by_username(t["assignee_username"]) if t["assignee_username"] else None)
            if u and u["dm_ok"] and not u["muted"]:
                db.queue_dm(u["user_id"], c["chat_id"], "⏰ موعد کار شما در «%s» %s:\n⬜ %s. %s" % (
                    render.md_escape(c["title"] or "گروه"), when, fa(n), render.md_escape(t["text"])),
                    {"inline_keyboard": [[render.B("✅ انجام شد", "dt:%d" % t["id"])]]}, self.dm_not_before(u, now), now)

    def tick(self, now=None):
        """One scheduler pass. Safe to call often; every send is guarded by a claim (no duplicates)."""
        now = self.clock() if now is None else now
        with self.lock:
            # 1) scheduled reminders: compare-and-set on next_at; lists firing together share one post
            fire = {}
            for r in db.due_reminders(now):
                nxt = None if r["kind"] == "once" else self.schedule_next(
                    r["chat_id"], r["kind"], r["minute"], max(now, r["next_at"]))
                if not db.claim_reminder(r["id"], r["next_at"], nxt):
                    continue
                if now - r["next_at"] > STALE:
                    log.info("reminder %s skipped (late by %ds)", r["id"], now - r["next_at"])
                    continue
                lid = r["list_id"] or (db.active_list(r["chat_id"]) or {"id": None})["id"]
                if lid:
                    fire.setdefault(r["chat_id"], []).append(lid)
            for chat_id, lids in fire.items():
                try:
                    self.fire_reminders(chat_id, lids, now)
                except Exception:
                    log.exception("reminders for %s failed", chat_id)
            # 2) per-task due reminders (claim row per task/kind/due)
            rows = db.q("SELECT t.*, l.chat_id AS l_chat, COALESCE(l.due_mode,'both') AS l_mode FROM tasks t "
                        "JOIN lists l ON l.id=t.list_id WHERE l.active=1 AND t.done=0 AND t.due_at IS NOT NULL")
            for t in rows:
                c = db.chat(t["l_chat"])
                if t["l_mode"] == "off":
                    continue
                eff = dates.due_effective(t["due_at"], t["due_has_time"])
                points = [("due", eff)]
                if t["l_mode"] == "both" and t["due_has_time"]:
                    points.insert(0, ("pre", eff - 3600))
                for kind, p in points:
                    if now < p or (kind == "pre" and now >= eff) or now - p > DUE_GRACE:
                        continue
                    if c["quiet_on"] and dates.in_quiet(now, c["quiet_start"], c["quiet_end"]):
                        continue   # wait until the quiet window ends
                    if db.claim_due(t["id"], kind, t["due_at"]):
                        try:
                            self.fire_due(db.get_list(t["list_id"]), t, kind, now)
                        except Exception:
                            log.exception("due reminder failed")
            # 3) DM outbox (claim row before sending)
            for o in db.outbox_due(now):
                if not db.claim_outbox(o["id"], now):
                    continue
                u = db.user(o["user_id"])
                if not u or u["muted"] or not u["dm_ok"]:
                    db.finish_outbox(o["id"], now, "muted/unavailable")
                    continue
                try:
                    self.call("sendMessage", chat_id=o["user_id"], text=o["text"],
                              reply_markup=json.loads(o["markup"]) if o["markup"] else None)
                    db.finish_outbox(o["id"], now)
                except ApiError as e:
                    db.finish_outbox(o["id"], now, str(e)[:200])
                    if e.code == 403:
                        db.set_user(o["user_id"], dm_ok=0)
                except NetError as e:
                    db.finish_outbox(o["id"], now, str(e)[:200])
            # 4) delayed deletes (acks, prompts, offers)
            for d in db.deletes_due(now):
                db.drop_delete(d["chat_id"], d["message_id"])
                self.delete(d["chat_id"], d["message_id"])
                db.drop_prompt(d["chat_id"], d["message_id"])
            # 5) remove expired ↩️ buttons
            for lst in db.q("SELECT * FROM lists WHERE active=1 AND undo_shown=1 AND undo_at<?",
                            (now - render.UNDO_WINDOW,)):
                db.set_list(lst["id"], undo_shown=0)
                self.refresh(lst)

    # ================================================================== per-user (DM) settings
    def user_settings_text(self, uid):
        u = db.user(uid)
        groups = [g["title"] or "گروه" for g in db.groups_of(uid)]
        return "⚙️ *یادآور شخصی*\nیادآورها: %s\n🌙 ساعت سکوت: %s\nگروه‌ها: %s" % (
            "خاموش 🔕" if u["muted"] else "روشن 🔔",
            ("روشن، " + render.quiet_label(u["quiet_start"], u["quiet_end"])) if u["quiet_on"] else "خاموش",
            "، ".join(render.md_escape(g) for g in groups) or "هنوز هیچ (در گروه دکمهٔ «🔔 فعال‌سازی یادآور شخصی» را بزنید)")

    def kb_user(self, uid):
        u = db.user(uid)
        return {"inline_keyboard": [
            [render.B("🔔 روشن کردن یادآورها" if u["muted"] else "🔕 خاموش کردن یادآورها", "um")],
            [render.B("🌙 ساعت سکوت: %s" % ("روشن" if u["quiet_on"] else "خاموش"), "uq"),
             render.B("🕰 ساعت‌های سکوت", "uqh")],
        ]}

    # ================================================================== buttons
    NEED_MOD = {"t", "add", "pedit", "pdel", "as", "du", "clr", "ttl", "undo", "rt", "ro", "rw",
                "dm", "roff", "qt", "qs", "rs", "arc", "arcy", "dell", "dely"}

    def on_callback(self, cb):
        data = cb.get("data") or ""
        user = cb.get("from") or {}
        cmsg = cb.get("message") or {}
        chat = cmsg.get("chat") or {}
        self.record(chat, user)
        parts = data.split(":")
        a = parts[0]
        try:
            if a in ("um", "uq", "uqh", "uqs", "uset", "dt"):
                return self.cb_user(cb, user, cmsg, parts)
            if a in ("new0", "show0"):
                self.answer(cb)
                lst = db.active_list(chat["id"])
                if a == "new0" and not lst:
                    self.new_list(chat, user)
                else:
                    self.refresh(lst or db.ensure_list(chat["id"], user.get("id")), repost=True)
                self.delete(chat["id"], cmsg.get("message_id"))
                return
            if a in ("cv", "cvx"):
                return self.cb_offer(cb, chat, user, cmsg, a == "cv")
            if a == "tg":
                return self.cb_target(cb, chat, user, cmsg, int(parts[1]))
            if a in ("newl", "nw") and (len(parts) < 2 or parts[1] == "0" or db.get_list(int(parts[1])) is None):
                return self.cb_newlist(cb, chat, user)
            if a == "t" and len(parts) == 2:            # v1 buttons: t:<task_id>
                t = db.get_task(int(parts[1]))
                parts = ["t", str(t["list_id"]) if t else "0", parts[1]]
            lst = db.get_list(int(parts[1]))
            if not lst or lst["chat_id"] != chat.get("id"):
                return self.answer(cb)
            if not lst["active"]:
                return self.answer(cb, "این لیست بایگانی شده است.")
            db.use_list(lst["id"], self.clock())
            if a in self.NEED_MOD and not self.can_modify(lst, chat, user.get("id")):
                return self.answer(cb, LOCKED_MSG, alert=True)
            return self.cb_list(cb, chat, user, cmsg, lst, a, parts[2:])
        except (ValueError, IndexError, TypeError, KeyError) as e:
            log.info("bad callback %r: %s", data[:40], e)
        self.answer(cb)

    def cb_newlist(self, cb, chat, user):
        if len(db.active_lists(chat["id"])) >= db.MAX_ACTIVE:
            return self.answer(cb, LIMIT_MSG, alert=True)
        self.ask(chat, user, "newlist", None, text=NEWLIST_PROMPT)
        return self.answer(cb, "✍️ عنوان لیست تازه را بنویسید")

    def cb_target(self, cb, chat, user, cmsg, list_id):
        p = db.prompt(chat["id"], cmsg.get("message_id"))
        db.drop_prompt(chat["id"], cmsg.get("message_id"))
        self.delete(chat["id"], cmsg.get("message_id")) or self._edit_plain(chat["id"], cmsg, "✔️")
        lst = db.get_list(list_id) if list_id else None
        if not p or not lst or not lst["active"] or lst["chat_id"] != chat.get("id"):
            return self.answer(cb, "لغو شد" if not list_id else None)
        req = json.loads(p["payload"])
        fake = {"chat": chat, "from": user, "message_id": None}
        self.run_cmd(req["name"], fake, chat, user, req["arg"], None, lst)
        return self.answer(cb, "📋 " + lst["title"])

    def cb_offer(self, cb, chat, user, cmsg, yes):
        p = db.prompt(chat["id"], cmsg.get("message_id"))
        db.drop_prompt(chat["id"], cmsg.get("message_id"))
        if not yes or not p:
            self.delete(chat["id"], cmsg.get("message_id"))
            return self.answer(cb)
        lst = db.ensure_list(chat["id"], user.get("id"))
        if not self.can_modify(lst, chat, user.get("id")):
            return self.answer(cb, LOCKED_MSG, alert=True)
        self.delete(chat["id"], cmsg.get("message_id"))
        items = parse.parse_import(p["payload"] or "")
        self.add_items(lst, [t for t, _d in items], user, [d for _t, d in items])
        self.answer(cb, "📋 %s کار به لیست اضافه شد" % fa(len(items)))

    def cb_list(self, cb, chat, user, cmsg, lst, a, args):
        L = lst["id"]

        def menu(kb):
            self.refresh(lst, kb=kb)

        if a == "t":
            t = self.task_in(lst, int(args[0]))
            if not t:
                self.refresh(lst)
                return self.answer(cb, "این کار دیگر در لیست نیست.")
            done = self.toggle(lst, t, user)
            return self.answer(cb, "✅ انجام شد" if done else "↩️ تیک برداشته شد")
        if a in ("r", "bk"):
            self.refresh(lst)
            return self.answer(cb, "🔄 به‌روز شد" if a == "r" else None)
        if a == "more":
            menu(render.kb_more(lst, db.chat(lst["chat_id"])))
            return self.answer(cb)
        if a == "add":
            self.ask(chat, user, "add", lst)
            return self.answer(cb, "✍️ کارها را در جواب پیام ربات بنویسید")
        if a == "pk":
            if not db.tasks(L):
                return self.answer(cb, "لیست خالی است.")
            menu(render.kb_pick(lst, db.tasks(L), args[0]))
            return self.answer(cb, render.PICK_TITLE.get(args[0]))
        if a in ("pedit", "pdel", "pasg", "pdue"):
            return self.cb_picked(cb, chat, user, lst, a, int(args[0]), menu)
        if a == "as":
            t, target = self.task_in(lst, int(args[0])), int(args[1])
            if not t:
                return self.answer(cb)
            u = db.user(target) if target else None
            kw = ({"assignee_id": u["user_id"], "assignee_username": u["username"], "assignee_name": u["name"]} if u
                  else {"assignee_id": None, "assignee_username": None, "assignee_name": None})
            self.mutate(lst, "مسئول " + fa(self.num_of(lst, t["id"])), lambda: db.set_task(t["id"], **kw))
            return self.answer(cb, ("👤 " + (u["name"] or "")) if u else "🚫 بدون مسئول")
        if a == "du":
            t, v = self.task_in(lst, int(args[0])), args[1]
            if not t:
                return self.answer(cb)
            n = self.num_of(lst, t["id"])
            if v == "w":
                self.refresh(lst)
                self.ask(chat, user, "due", lst, t["id"],
                         text="📅 %%s موعد کار %s را بنویسید: «فردا ۱۸»، «شنبه»، «۲۵ مهر ۱۰:۳۰»، «۲ ساعت دیگه»" % fa(n))
                return self.answer(cb, "✍️ موعد را بنویسید")
            if v == "x":
                self.mutate(lst, "حذف موعد " + fa(n), lambda: db.set_task(t["id"], due_at=None, due_has_time=0))
                return self.answer(cb, "موعد برداشته شد")
            due = dates.parse_due(render.DUE_PRESETS[int(v)][1], self.clock())
            self.mutate(lst, "موعد " + fa(n), lambda: db.set_task(t["id"], due_at=due[0], due_has_time=1 if due[1] else 0))
            return self.answer(cb, "⏰ " + dates.fmt_due(due[0], due[1], self.clock()))
        if a == "clr":
            n = self.mutate(lst, "پاک کردن انجام‌شده‌ها", lambda: db.clear_done(L))
            return self.answer(cb, ("🧹 %s کار پاک شد (↩️ واگرد)" % fa(n)) if n else "کار انجام‌شده‌ای نیست")
        if a == "lock":
            if chat.get("type") == "private":
                return self.answer(cb, "لیست شخصی است؛ قفل لازم نیست 🙂")
            if not self._set_lock(chat, user, not lst["locked"], lst):
                return self.answer(cb, "فقط سازندهٔ لیست، قفل‌کننده یا ادمین‌ها", alert=True)
            return self.answer(cb, "🔓 قفل باز شد" if lst["locked"] else "🔒 قفل شد")
        if a == "ttl":
            self.refresh(lst)
            self.ask(chat, user, "title", lst, text="🏷 %s عنوان تازهٔ لیست را بنویسید:")
            return self.answer(cb, "✍️ عنوان را بنویسید")
        if a == "st":   # removed feature: buttons on old messages just refresh the list
            self.refresh(lst)
            return self.answer(cb, "این گزینه حذف شده است.")
        if a in ("nw", "newl"):
            return self.cb_newlist(cb, chat, user)
        if a == "nwy":                 # confirm button from the single-list version
            self.refresh(lst)
            return self.answer(cb, "حالا چند لیست ممکن است؛ از ⚙️ ← 🆕 لیست جدید بسازید.")
        if a == "ls":
            menu(render.kb_lists(lst, self.lists_with_tasks(lst["chat_id"])))
            return self.answer(cb, "📚 لیست‌ها")
        if a == "go":
            self.refresh(lst, repost=True)
            return self.answer(cb, "📋 " + lst["title"])
        if a == "arc":
            menu(render.kb_confirm("arcy:%d" % L, "more:%d" % L, "🗄 بله، بایگانی کن", "◀️ نه"))
            return self.answer(cb, "این لیست بایگانی شود؟ (پیامش می‌ماند، دکمه‌ها برداشته می‌شوند)")
        if a == "dell":
            menu(render.kb_confirm("dely:%d" % L, "more:%d" % L, "🗑 بله، حذف کامل", "◀️ نه"))
            return self.answer(cb, "این لیست و همهٔ کارهایش برای همیشه حذف شود؟", alert=True)
        if a in ("arcy", "dely"):
            self.close_list(lst, delete=a == "dely")
            return self.answer(cb, "🗑 لیست حذف شد" if a == "dely" else "🗄 لیست بایگانی شد")
        if a == "undo":
            if not lst["undo_json"] or self.clock() - (lst["undo_at"] or 0) > render.UNDO_WINDOW:
                self.refresh(lst)
                return self.answer(cb, "زمان واگرد گذشته است.")
            label = lst["undo_label"]
            db.restore(L, json.loads(lst["undo_json"]))
            db.set_list(L, undo_json=None, undo_at=None, undo_label=None, undo_shown=0)
            self.after_change(lst)
            return self.answer(cb, "↩️ برگشت: " + (label or ""))
        if a == "rs":
            if args[0] == "y":
                self.mutate(lst, "پاک کردن کل لیست", lambda: db.clear_all(L))
            if not self.delete(chat["id"], cmsg.get("message_id")):
                self._edit_plain(chat["id"], cmsg, "🗑 لیست پاک شد." if args[0] == "y" else "لغو شد.")
            return self.answer(cb, "🗑 لیست پاک شد (↩️ واگرد)" if args[0] == "y" else "لغو شد")
        return self.cb_reminders(cb, chat, user, lst, a, args, menu)

    def cb_picked(self, cb, chat, user, lst, a, task_id, menu):
        t = self.task_in(lst, task_id)
        if not t:
            self.refresh(lst)
            return self.answer(cb, "این کار دیگر در لیست نیست.")
        n = self.num_of(lst, t["id"])
        if a == "pedit":
            self.refresh(lst)
            self.ask(chat, user, "edit", lst, t["id"],
                     text="✏️ %%s متن تازهٔ کار %s را بنویسید:\n«%s»" % (fa(n), render.md_escape(t["text"])[:100]))
            return self.answer(cb, "✍️ متن تازه را بنویسید")
        if a == "pdel":
            self.mutate(lst, "حذف " + fa(n), lambda: db.del_task(t["id"]))
            return self.answer(cb, "🗑 کار %s حذف شد (↩️ واگرد)" % fa(n))
        if a == "pasg":
            members = db.members(lst["chat_id"])
            if not members:
                return self.answer(cb, "هنوز عضوی ثبت نشده؛ در متن کار @username بنویسید.", alert=True)
            menu(render.kb_assign(lst, t, members))
            return self.answer(cb, "👤 مسئول کار %s؟" % fa(n))
        menu(render.kb_due(lst, t))
        return self.answer(cb, "📅 موعد کار %s؟" % fa(n))

    def cb_reminders(self, cb, chat, user, lst, a, args, menu):
        L, cid = lst["id"], lst["chat_id"]
        if a == "rem":
            menu(self.kb_rem(lst))
            return self.answer(cb)
        if a == "rk":
            menu(render.kb_times(lst, args[0], db.list_reminders(L)))
            return self.answer(cb, "⏰ ساعت را انتخاب کنید (دوباره بزنید = حذف)")
        if a == "rt":
            kind, minute = args[0], int(args[1])
            on = self.toggle_daily(lst, kind, minute, user)
            menu(render.kb_times(lst, kind, db.list_reminders(L)))
            return self.answer(cb, ("✅ یادآور %s %s" if on else "❌ حذف شد: %s %s") %
                               (render.REM_KIND[kind], fa("%02d:%02d" % (minute // 60, minute % 60))))
        if a == "rw":
            menu(self.kb_rem(lst))
            self.ask(chat, user, "remtime", lst, payload=args[0],
                     text="⏰ %s ساعت یادآور را بنویسید (مثل «۱۸:۳۰»)." if args[0] != "once" else
                     "⏰ %s زمان یادآور یک‌باره را بنویسید (مثل «فردا ۹» یا «۲ ساعت دیگه»).")
            return self.answer(cb, "✍️ زمان را بنویسید")
        if a == "ro":
            now = self.clock()
            ts = now + 3600 if args[0] == "1h" else dates.parse_due(
                {"t18": "امروز 18", "f9": "فردا 9", "f18": "فردا 18"}[args[0]], now)[0]
            if ts <= now:
                return self.answer(cb, "این زمان گذشته است.")
            ts = self.add_once(lst, ts, user)
            menu(self.kb_rem(lst))
            return self.answer(cb, "✅ یادآور: " + dates.fmt_due(ts, True, now))
        if a == "dm":
            order = [m for m, _ in render.DUE_MODES]
            cur = lst["due_mode"] or "both"
            nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "both"
            db.set_list(L, due_mode=nxt)
            menu(self.kb_rem(lst))
            return self.answer(cb, "⏳ موعد کارها: " + dict(render.DUE_MODES)[nxt])
        if a == "roff":
            db.x("UPDATE reminders SET active=0 WHERE list_id=?", (L,))
            menu(self.kb_rem(lst))
            return self.answer(cb, "🔕 همهٔ یادآورها خاموش شد")
        if a == "qt":
            c = db.chat(cid)
            db.set_chat(cid, quiet_on=0 if c["quiet_on"] else 1)
            self.reschedule_chat(cid)
            menu(self.kb_rem(lst) if args and args[0] == "r" else render.kb_more(lst, db.chat(cid)))
            return self.answer(cb, "🌙 ساعت سکوت " + ("خاموش شد" if c["quiet_on"] else "روشن شد"))
        if a == "qh":
            c = db.chat(cid)
            menu(render.kb_quiet_hours("qs", str(L), (c["quiet_start"], c["quiet_end"]), "more:%d" % L))
            return self.answer(cb, "🌙 در این ساعت‌ها یادآوری ارسال نمی‌شود")
        if a == "qs":
            s, e = int(args[0]), int(args[1])
            db.set_chat(cid, quiet_start=s, quiet_end=e, quiet_on=1)
            self.reschedule_chat(cid)
            menu(render.kb_more(lst, db.chat(cid)))
            return self.answer(cb, "🌙 ساعت سکوت: " + render.quiet_label(s, e))
        self.answer(cb)

    def _edit_plain(self, chat_id, cmsg, text):
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=cmsg.get("message_id"), text=text)
        except (ApiError, NetError):
            pass

    def cb_user(self, cb, user, cmsg, parts):
        uid = user.get("id")
        if not db.user(uid):
            db.upsert_user(uid, display_name(user), user.get("username"))
        a = parts[0]
        if a == "dt":
            t = db.get_task(int(parts[1]))
            lst = db.get_list(t["list_id"]) if t else None
            if not t or not lst["active"]:
                return self.answer(cb, "این کار دیگر در لیست نیست.")
            c = db.chat(lst["chat_id"])
            gchat = {"id": lst["chat_id"], "type": c["type"]}
            member = lst["chat_id"] == uid or db.q("SELECT 1 FROM members WHERE chat_id=? AND user_id=?",
                                                   (lst["chat_id"], uid))
            if not member:
                return self.answer(cb, "شما عضو ثبت‌شدهٔ این گروه نیستید.", alert=True)
            if not self.can_modify(lst, gchat, uid):
                return self.answer(cb, LOCKED_MSG, alert=True)
            if t["done"]:
                return self.answer(cb, "✅ قبلاً انجام شده")
            self.toggle(lst, t, user)
            self._mark_dm_button(cmsg, t["id"])
            return self.answer(cb, "✅ انجام شد؛ لیست گروه هم به‌روز شد")
        u = db.user(uid)
        if a == "um":
            db.set_user(uid, muted=0 if u["muted"] else 1)
            self.answer(cb, "🔔 یادآورها روشن شد" if u["muted"] else "🔕 یادآورها خاموش شد")
        elif a == "uq":
            db.set_user(uid, quiet_on=0 if u["quiet_on"] else 1)
            self.answer(cb, "🌙 ساعت سکوت " + ("خاموش شد" if u["quiet_on"] else "روشن شد"))
        elif a == "uqh":
            try:
                self.call("editMessageText", chat_id=cmsg["chat"]["id"], message_id=cmsg["message_id"],
                          text="🌙 ساعت سکوت پیام‌های شخصی:",
                          reply_markup=render.kb_quiet_hours("uqs", str(uid), (u["quiet_start"], u["quiet_end"]), "uset"))
            except (ApiError, NetError, KeyError):
                pass
            return self.answer(cb)
        elif a == "uqs":
            db.set_user(uid, quiet_start=int(parts[2]), quiet_end=int(parts[3]), quiet_on=1)
            self.answer(cb, "🌙 " + render.quiet_label(int(parts[2]), int(parts[3])))
        else:
            self.answer(cb)
        try:   # show the settings card (in place of the message the button was on)
            self.call("editMessageText", chat_id=cmsg["chat"]["id"], message_id=cmsg["message_id"],
                      text=self.user_settings_text(uid), reply_markup=self.kb_user(uid))
        except (ApiError, NetError, KeyError):
            pass

    def _mark_dm_button(self, cmsg, task_id):
        kb = (cmsg.get("reply_markup") or {}).get("inline_keyboard")
        if not kb:
            return
        new = [[({"text": "✔️ انجام شد", "callback_data": b["callback_data"]}
                 if b.get("callback_data") == "dt:%d" % task_id else b) for b in row] for row in kb]
        try:
            self.call("editMessageReplyMarkup", chat_id=cmsg["chat"]["id"], message_id=cmsg["message_id"],
                      reply_markup={"inline_keyboard": new})
        except (ApiError, NetError):
            pass
