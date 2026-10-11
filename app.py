"""Group to-do list logic. Transport-agnostic: give it any object with call(method, **params)."""
import time, logging
import db, parse, render
from api import ApiError, NetError
from parse import to_fa_digits as fa

log = logging.getLogger("app")
ADMIN_TTL = 300
PIN_WARN_EVERY = 6 * 3600

HELP = """👋 سلام! من ربات *لیست کارها* هستم.

*استفاده در گروه*
۱. من را به گروه اضافه کنید.
۲. من را *ادمین* کنید (برای سنجاق کردن لیست و دیدن پیام‌های بدون اسلش مثل «انجام شد ۳»). بدون ادمین هم دستورهای اسلش‌دار کار می‌کنند.
۳. با  /new  لیست را بسازید؛ لیست ارسال و سنجاق می‌شود و از آن به بعد همان پیام ویرایش می‌شود.

*دستورها*
/add متن — افزودن کار (چند خط = چند کار)
/import — روی پیام یک لیست آماده ریپلای کنید تا خط‌به‌خط وارد شود (✅ ها انجام‌شده وارد می‌شوند)
/done ۳ — تیک زدن (چندتایی: /done ۱ ۳ ۵ یا ۲-۴)
/undo ۳ — برداشتن تیک
/del ۳ — حذف کار
/edit ۳ متن جدید — ویرایش
/title عنوان — تغییر عنوان لیست
/list — نمایش دوباره لیست (پایین گفتگو) و سنجاق
/new — شروع لیست تازه (لیست قبلی بایگانی می‌شود)
/clear_done — حذف کارهای انجام‌شده
/reset — پاک کردن کل لیست (با تأیید)
/lock و /unlock — فقط ادمین‌ها و سازندهٔ لیست بتوانند تغییر دهند

*معادل فارسی:*  «اضافه متن»، «انجام شد ۳»، «برگشت ۳»، «حذف ۳»، «ویرایش ۳ متن»، «لیست»، «لیست جدید»
اعداد فارسی هم قبول است. دکمه‌های زیر لیست هم تیک می‌زنند.

*در چت خصوصی* هم لیست شخصی دارید؛ آنجا هر متنی بفرستید (هر خط یک کار) اضافه می‌شود."""

PIN_HINT = "📌 برای سنجاق کردن لیست، لطفاً من را در گروه *ادمین* کنید (با دسترسی سنجاق پیام). لیست بدون سنجاق هم کار می‌کند 🙂"
LOCKED_MSG = "🔒 لیست قفل است؛ فقط ادمین‌ها و سازندهٔ لیست می‌توانند تغییرش دهند."


def display_name(u):
    if not u:
        return "؟"
    n = ((u.get("first_name") or "") + " " + (u.get("last_name") or "")).strip()
    return n or (u.get("username") or str(u.get("id", "")))


def fa_list(nums):
    return "، ".join(fa(n) for n in nums)


class TodoBot:
    def __init__(self, api, username=None, clock=time.time):
        self.api, self.username, self.clock = api, username, clock
        self._admin_cache = {}

    # ------------------------------------------------------------------ transport helpers
    def call(self, method, **p):
        return self.api.call(method, **p)

    def send(self, chat_id, text, reply_to=None, markup=None):
        try:
            return self.call("sendMessage", chat_id=chat_id, text=text, reply_to_message_id=reply_to, reply_markup=markup)
        except ApiError as e:
            if reply_to and e.code in (400, 404):  # replied-to message gone -> send without reply
                return self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=markup)
            raise

    def safe_send(self, *a, **k):
        try:
            return self.send(*a, **k)
        except (ApiError, NetError) as e:
            log.warning("send failed: %s", e)

    # ------------------------------------------------------------------ permissions
    def is_admin(self, chat_id, user_id):
        key = (chat_id, user_id)
        hit = self._admin_cache.get(key)
        if hit and self.clock() - hit[1] < ADMIN_TTL:
            return hit[0]
        res = None
        try:
            m = self.call("getChatMember", chat_id=chat_id, user_id=user_id)
            res = (m or {}).get("status") in ("creator", "administrator", "owner")
            db.kv_set("cap.getChatMember", "ok")
        except (ApiError, NetError) as e:
            log.info("getChatMember failed (%s), trying getChatAdministrators", e)
            db.kv_set("cap.getChatMember", "fail: %s" % e)
            try:
                admins = self.call("getChatAdministrators", chat_id=chat_id)
                if not isinstance(admins, list):
                    raise ApiError("getChatAdministrators", 0, "unexpected result")
                res = any((a.get("user") or {}).get("id") == user_id for a in admins)
                db.kv_set("cap.getChatAdministrators", "ok")
            except (ApiError, NetError) as e2:
                log.warning("admin check unavailable: %s", e2)
                db.kv_set("cap.getChatAdministrators", "fail: %s" % e2)
                res = False
        self._admin_cache[key] = (res, self.clock())
        return res

    def can_modify(self, lst, chat, user_id):
        if chat.get("type") == "private" or not lst["locked"]:
            return True
        return user_id == lst["creator_id"] or self.is_admin(chat["id"], user_id)

    def can_admin(self, lst, chat, user_id):
        if chat.get("type") == "private":
            return True
        return user_id == lst["creator_id"] or self.is_admin(chat["id"], user_id)

    # ------------------------------------------------------------------ list message
    def refresh(self, lst, repost=False, chat_type=None):
        """Edit the list message in place; send (and pin) a new one if there is none or the edit fails."""
        lst = db.get_list(lst["id"])
        tasks = db.tasks(lst["id"])
        text, kb = render.render_text(lst, tasks, now=self.clock()), render.keyboard(lst, tasks)
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
            if repost and mid and mid != new_mid:   # tidy: remove the old copy if we may (ignored otherwise)
                try:
                    self.call("deleteMessage", chat_id=lst["chat_id"], message_id=mid)
                except (ApiError, NetError):
                    pass
            self.pin(lst["chat_id"], new_mid, chat_type)
        return new_mid

    def pin(self, chat_id, mid, chat_type=None):
        try:
            self.call("pinChatMessage", chat_id=chat_id, message_id=mid, disable_notification=True)
            db.kv_set("cap.pinChatMessage", "ok")
            return True
        except (ApiError, NetError) as e:
            log.info("pin failed in %s: %s", chat_id, e)
            db.kv_set("cap.pinChatMessage", "fail: %s" % e)
            if chat_type in ("group", "supergroup", "channel") or (chat_type is None and chat_id < 0):
                key = "pinwarn.%s" % chat_id
                if self.clock() - float(db.kv_get(key, 0)) > PIN_WARN_EVERY:
                    db.kv_set(key, self.clock())
                    self.safe_send(chat_id, PIN_HINT)
            return False

    def after_change(self, lst, chat_type=None):
        self.refresh(lst, chat_type=chat_type)
        tasks = db.tasks(lst["id"])
        all_done = bool(tasks) and all(t["done"] for t in tasks)
        lst = db.get_list(lst["id"])
        if all_done and not lst["celebrated"]:
            db.set_list(lst["id"], celebrated=1)
            self.safe_send(lst["chat_id"], "🎉 آفرین! همهٔ %s کار انجام شد. خسته نباشید! 👏" % fa(len(tasks)))
        elif not all_done and lst["celebrated"]:
            db.set_list(lst["id"], celebrated=0)

    # ------------------------------------------------------------------ updates
    def handle_update(self, up):
        kind = next((k for k in up if k != "update_id"), "?")
        m = up.get(kind) if isinstance(up.get(kind), dict) else {}
        ctype = ((m.get("chat") or (m.get("message") or {}).get("chat") or {}).get("type"))
        log.info("update %s: %s in %s chat%s", up.get("update_id"), kind, ctype,
                 " (text starts with /)" if (m.get("text") or "").startswith("/") else "")
        if "callback_query" in up:
            return self.on_callback(up["callback_query"])
        msg = up.get("message") or up.get("edited_message")
        if msg and "message" in up:
            return self.on_message(msg)

    def on_message(self, msg):
        chat, user = msg.get("chat") or {}, msg.get("from") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        me = [m for m in (msg.get("new_chat_members") or []) if m.get("is_bot") and
              (m.get("username") or "").lower() == (self.username or "").lower()]
        if me:
            return self.safe_send(chat_id, HELP)
        text = msg.get("text") or ""
        reply = msg.get("reply_to_message")
        cmd = parse.parse_command(text, self.username, is_reply=bool(reply))
        if not cmd:
            if chat.get("type") == "private" and text.strip() and not text.startswith("/"):
                cmd = ("add", text)
            else:
                return
        name, arg = cmd
        log.info("cmd %s in %s chat", name, chat.get("type"))
        handler = getattr(self, "cmd_" + name)
        return handler(msg, chat, user, arg, reply)

    def _guard(self, lst, chat, user, msg):
        if self.can_modify(lst, chat, user.get("id")):
            return True
        self.safe_send(chat["id"], LOCKED_MSG, reply_to=msg.get("message_id"))
        return False

    def ack(self, msg, text):
        return self.safe_send(msg["chat"]["id"], text, reply_to=msg.get("message_id"))

    # ------------------------------------------------------------------ commands
    def cmd_help(self, msg, chat, user, arg, reply):
        self.safe_send(chat["id"], HELP)

    def cmd_list(self, msg, chat, user, arg, reply):
        lst = db.ensure_list(chat["id"], user.get("id"))
        self.refresh(lst, repost=True, chat_type=chat.get("type"))

    def cmd_new(self, msg, chat, user, arg, reply):
        old = db.active_list(chat["id"])
        if old and not db.tasks(old["id"]) and not arg:   # empty list already: just show it
            return self.refresh(old, repost=True, chat_type=chat.get("type"))
        if old and not self._guard(old, chat, user, msg):
            return
        title = arg.strip()[:100] if arg and arg.strip() else None
        lst = db.create_list(chat["id"], user.get("id"), title)
        self.refresh(lst, chat_type=chat.get("type"))

    def cmd_add(self, msg, chat, user, arg, reply):
        items = parse.parse_add_lines(arg)
        if not items and reply:
            return self.cmd_import(msg, chat, user, arg, reply)
        if not items:
            return self.ack(msg, "✍️ بعد از /add متن کار را بنویسید. مثلاً:\n/add خرید نان\nبرای چند کار، هر کار را در یک خط بنویسید.")
        lst = db.ensure_list(chat["id"], user.get("id"))
        if not self._guard(lst, chat, user, msg):
            return
        start = len(db.tasks(lst["id"])) + 1
        db.add_tasks(lst["id"], items, user.get("id"))
        self.after_change(lst, chat.get("type"))
        if len(items) == 1:
            self.ack(msg, "➕ کار %s اضافه شد." % fa(start))
        else:
            self.ack(msg, "➕ %s کار اضافه شد (%s تا %s)." % (fa(len(items)), fa(start), fa(start + len(items) - 1)))

    def cmd_import(self, msg, chat, user, arg, reply):
        src = (reply or {}).get("text") or (reply or {}).get("caption") or ""
        if not src and arg:
            src = arg
        items = parse.parse_import(src)
        if not items:
            return self.ack(msg, "📥 روی پیامی که لیست کارها در آن است ریپلای کنید و /import بفرستید (هر خط یک کار).")
        lst = db.ensure_list(chat["id"], user.get("id"))
        if not self._guard(lst, chat, user, msg):
            return
        db.add_tasks(lst["id"], items, user.get("id"), done_by=display_name(user))
        self.after_change(lst, chat.get("type"))
        nd = sum(1 for _t, d in items if d)
        extra = " (%s مورد انجام‌شده)" % fa(nd) if nd else ""
        self.ack(msg, "📥 %s کار وارد لیست شد%s." % (fa(len(items)), extra))

    def _nums_or_usage(self, msg, arg, lst, usage):
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

    def _set_done(self, msg, chat, user, arg, done):
        lst = db.active_list(chat["id"])
        if not lst or not db.tasks(lst["id"]):
            return self.ack(msg, "📋 لیست خالی است. با /add کار اضافه کنید.")
        if not self._guard(lst, chat, user, msg):
            return
        usage = "مثال: /%s ۳  یا  /%s ۱ ۳ ۵" % (("done",) * 2 if done else ("undo",) * 2)
        nums = self._nums_or_usage(msg, arg, lst, usage)
        if not nums:
            return
        for n in nums:
            t = db.task_by_num(lst["id"], n)
            if bool(t["done"]) != done:
                db.set_done(t["id"], done, display_name(user))
        self.after_change(lst, chat.get("type"))
        tasks = db.tasks(lst["id"])
        d = sum(1 for t in tasks if t["done"])
        if done:
            self.ack(msg, "✅ %s انجام شد. (%s از %s)" % (fa_list(nums), fa(d), fa(len(tasks))))
        else:
            self.ack(msg, "↩️ تیک %s برداشته شد." % fa_list(nums))

    def cmd_done(self, msg, chat, user, arg, reply):
        self._set_done(msg, chat, user, arg, True)

    def cmd_undo(self, msg, chat, user, arg, reply):
        self._set_done(msg, chat, user, arg, False)

    def cmd_del(self, msg, chat, user, arg, reply):
        lst = db.active_list(chat["id"])
        if not lst or not db.tasks(lst["id"]):
            return self.ack(msg, "📋 لیست خالی است.")
        if not self._guard(lst, chat, user, msg):
            return
        nums = self._nums_or_usage(msg, arg, lst, "مثال: /del ۳  یا  /del ۲ ۴")
        if not nums:
            return
        ids = [db.task_by_num(lst["id"], n)["id"] for n in nums]
        for i in ids:
            db.del_task(i)
        db.touch(lst["id"])
        self.after_change(lst, chat.get("type"))
        self.ack(msg, "🗑 %s حذف شد. شماره‌ها مرتب شدند." % fa_list(nums))

    def cmd_edit(self, msg, chat, user, arg, reply):
        n, text = parse.split_num_text(arg)
        if not n or not text:
            return self.ack(msg, "مثال: /edit ۳ متن جدید")
        lst = db.active_list(chat["id"])
        t = db.task_by_num(lst["id"], n) if lst else None
        if not t:
            return self.ack(msg, "⚠️ کار شمارهٔ %s پیدا نشد." % fa(n))
        if not self._guard(lst, chat, user, msg):
            return
        db.edit_task(t["id"], text[:300])
        db.touch(lst["id"])
        self.after_change(lst, chat.get("type"))
        self.ack(msg, "✏️ کار %s ویرایش شد." % fa(n))

    def cmd_title(self, msg, chat, user, arg, reply):
        if not arg.strip():
            return self.ack(msg, "مثال: /title کارهای این هفته")
        lst = db.ensure_list(chat["id"], user.get("id"))
        if not self._guard(lst, chat, user, msg):
            return
        db.set_list(lst["id"], title=arg.strip().replace("\n", " ")[:100])
        self.after_change(lst, chat.get("type"))
        self.ack(msg, "🏷 عنوان لیست عوض شد.")

    def cmd_clear_done(self, msg, chat, user, arg, reply):
        lst = db.active_list(chat["id"])
        if not lst:
            return self.ack(msg, "📋 لیستی وجود ندارد.")
        if not self._guard(lst, chat, user, msg):
            return
        n = db.clear_done(lst["id"])
        db.touch(lst["id"])
        self.after_change(lst, chat.get("type"))
        self.ack(msg, "🧹 %s کار انجام‌شده پاک شد." % fa(n) if n else "کار انجام‌شده‌ای نبود.")

    def cmd_reset(self, msg, chat, user, arg, reply):
        lst = db.active_list(chat["id"])
        if not lst or not db.tasks(lst["id"]):
            return self.ack(msg, "📋 لیست از قبل خالی است.")
        if not self._guard(lst, chat, user, msg):
            return
        kb = {"inline_keyboard": [[{"text": "✅ بله، پاک کن", "callback_data": "rs:%d:y" % lst["id"]},
                                   {"text": "❌ نه", "callback_data": "rs:%d:n" % lst["id"]}]]}
        self.safe_send(chat["id"], "⚠️ همهٔ %s کار لیست پاک شود؟" % fa(len(db.tasks(lst["id"]))),
                       reply_to=msg.get("message_id"), markup=kb)

    def _set_lock(self, msg, chat, user, locked):
        if chat.get("type") == "private":
            return self.ack(msg, "این لیست شخصی است و فقط خودتان به آن دسترسی دارید 🙂")
        lst = db.ensure_list(chat["id"], user.get("id"))
        if not self.can_admin(lst, chat, user.get("id")):
            return self.ack(msg, "فقط ادمین‌های گروه یا سازندهٔ لیست می‌توانند قفل را تغییر دهند.")
        db.set_list(lst["id"], locked=1 if locked else 0)
        self.after_change(lst, chat.get("type"))
        self.ack(msg, LOCKED_MSG if locked else "🔓 قفل باز شد؛ همه می‌توانند لیست را تغییر دهند.")

    def cmd_lock(self, msg, chat, user, arg, reply):
        self._set_lock(msg, chat, user, True)

    def cmd_unlock(self, msg, chat, user, arg, reply):
        self._set_lock(msg, chat, user, False)

    # ------------------------------------------------------------------ buttons
    def answer(self, cb, text=None, alert=False):
        try:
            self.call("answerCallbackQuery", callback_query_id=cb["id"], text=text, show_alert=alert or None)
        except (ApiError, NetError) as e:
            log.info("answerCallbackQuery failed: %s", e)

    def on_callback(self, cb):
        data = cb.get("data") or ""
        user = cb.get("from") or {}
        cmsg = cb.get("message") or {}
        chat = cmsg.get("chat") or {}
        parts = data.split(":")
        try:
            if parts[0] == "t":
                return self._cb_toggle(cb, chat, user, int(parts[1]))
            if parts[0] == "r":
                lst = db.get_list(int(parts[1]))
                if lst and lst["chat_id"] == chat.get("id"):
                    if lst["active"]:
                        self.refresh(lst, chat_type=chat.get("type"))
                    return self.answer(cb, "🔄 به‌روز شد" if lst["active"] else "این لیست قدیمی است؛ /list را بزنید.")
                return self.answer(cb)
            if parts[0] == "rs":
                return self._cb_reset(cb, chat, user, cmsg, int(parts[1]), parts[2] == "y")
        except (ValueError, IndexError):
            pass
        self.answer(cb)

    def _cb_toggle(self, cb, chat, user, task_id):
        t = db.get_task(task_id)
        lst = db.get_list(t["list_id"]) if t else None
        if not t or lst["chat_id"] != chat.get("id"):
            return self.answer(cb, "این کار دیگر در لیست نیست.")
        if not lst["active"]:
            return self.answer(cb, "این لیست قدیمی است؛ /list را بزنید.")
        if not self.can_modify(lst, chat, user.get("id")):
            return self.answer(cb, LOCKED_MSG.replace("*", ""), alert=True)
        done = not t["done"]
        db.set_done(t["id"], done, display_name(user))
        db.touch(lst["id"])
        self.answer(cb, "✅ انجام شد" if done else "↩️ تیک برداشته شد")
        self.after_change(lst, chat.get("type"))

    def _cb_reset(self, cb, chat, user, cmsg, list_id, yes):
        lst = db.get_list(list_id)
        if not lst or lst["chat_id"] != chat.get("id"):
            return self.answer(cb)
        if not self.can_modify(lst, chat, user.get("id")):
            return self.answer(cb, LOCKED_MSG.replace("*", ""), alert=True)
        if yes:
            db.clear_all(lst["id"])
            db.set_list(lst["id"], celebrated=0)
            db.touch(lst["id"])
            txt = "🗑 لیست پاک شد."
        else:
            txt = "لغو شد؛ لیست دست نخورد."
        try:
            self.call("editMessageText", chat_id=chat["id"], message_id=cmsg.get("message_id"), text=txt)
        except (ApiError, NetError):
            pass
        self.answer(cb, txt)
        if yes:
            self.refresh(lst, chat_type=chat.get("type"))
