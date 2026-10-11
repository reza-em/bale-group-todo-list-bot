"""Button UX, prompts, permissions, reminders and DM routing against a fake Bale API."""
import os, sys, json, datetime, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db, render, dates
from api import ApiError
from app import TodoBot

GID = 4918886273                      # Bale group ids are positive
GROUP = {"id": GID, "type": "group", "title": "خانه"}
REZA = {"id": 1, "first_name": "رضا", "username": "reza"}
ALI = {"id": 2, "first_name": "علی", "username": "ali"}
SARA = {"id": 3, "first_name": "سارا"}


def T(y, mo, d, h=0, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=dates.TEHRAN).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeApi:
    def __init__(self):
        self.calls, self.next_mid, self.fail = [], 500, {}
        self.admins = {1}

    def call(self, method, **p):
        self.calls.append((method, p))
        if method in self.fail:
            raise ApiError(method, *self.fail[method])
        if method == "sendMessage":
            self.next_mid += 1
            return {"message_id": self.next_mid, "chat": {"id": p["chat_id"]}, "text": p["text"]}
        if method == "getChatMember":
            return {"status": "administrator" if p["user_id"] in self.admins else "member"}
        if method == "getChatAdministrators":
            return [{"user": {"id": i}, "status": "administrator"} for i in self.admins]
        return True

    def of(self, method):
        return [p for m, p in self.calls if m == method]

    def sent_texts(self, chat_id=None):
        return [p["text"] for p in self.of("sendMessage") if chat_id is None or p["chat_id"] == chat_id]


class Base(unittest.TestCase):
    def setUp(self):
        db.connect(":memory:")
        self.api = FakeApi()
        self.clock = Clock(T(2026, 10, 11, 13, 0))   # Sunday 19 Mehr 1405, 13:00 Tehran
        self.bot = TodoBot(self.api, "reminds_bot", clock=self.clock)
        self.mid = 0

    def msg(self, text, user=REZA, chat=GROUP, reply_to=None, reply_text=None, reply_from=None):
        self.mid += 1
        m = {"message_id": self.mid, "chat": chat, "from": user, "text": text}
        if reply_to or reply_text:
            m["reply_to_message"] = {"message_id": reply_to or 1, "text": reply_text or "", "chat": chat,
                                     "from": reply_from or {"id": 99, "is_bot": True, "username": "reminds_bot"}}
        self.bot.handle_update({"update_id": self.mid, "message": m})
        return self.mid

    def cb(self, data, user=REZA, chat=GROUP, mid=None, markup=None):
        m = {"message_id": mid or (self.lst()["message_id"] if self.lst() else 1), "chat": chat}
        if markup:
            m["reply_markup"] = markup
        self.bot.handle_update({"update_id": 999, "callback_query": {"id": "q", "data": data, "from": user, "message": m}})
        return self.api.of("answerCallbackQuery")[-1]

    def lst(self, chat_id=GID):
        return db.active_list(chat_id)

    def tasks(self, chat_id=GID):
        return db.tasks(self.lst(chat_id)["id"])

    def last_kb(self):
        return self.api.of("editMessageText")[-1]["reply_markup"]["inline_keyboard"]

    def datas(self, kb):
        return [b.get("callback_data") for row in kb for b in row]


# ------------------------------------------------------------------------------------------ render
class TestRender(unittest.TestCase):
    def setUp(self):
        db.connect(":memory:")
        self.now = T(2026, 10, 11, 13, 0)

    def task(self, i, **kw):
        t = {"id": i, "text": "کار*%d" % i, "done": 0, "done_by": None, "assignee_id": None, "assignee_username": None,
             "assignee_name": None, "due_at": None, "due_has_time": 0}
        t.update(kw)
        return t

    def lst(self, **kw):
        d = {"id": 1, "chat_id": GID, "title": "📋 لیست کارها", "locked": 0, "undo_at": None, "undo_label": None}
        d.update(kw)
        return d

    def test_progress_and_marks(self):
        tasks = [self.task(i, done=int(i <= 3), done_by="رضا" if i <= 3 else None) for i in range(1, 8)]
        txt = render.render_text(self.lst(), tasks, now=self.now)
        self.assertIn("پیشرفت: ۳ از ۷ (۴۳٪)", txt)
        self.assertIn("✅ ۱. کار∗1  — رضا", txt)
        self.assertIn("▰▰▰▰▱▱▱▱▱▱", txt)
        self.assertIn("۱۹ مهر، ساعت ۱۳:۰۰", txt)

    def test_assignee_due_overdue(self):
        tasks = [self.task(1, assignee_username="ali", due_at=T(2026, 10, 12, 18), due_has_time=1),
                 self.task(2, assignee_name="سارا", due_at=T(2026, 10, 10), due_has_time=0)]
        txt = render.render_text(self.lst(), tasks, now=self.now)
        self.assertIn("⬜ ۱. کار∗1  (👤 @ali · ⏰ فردا ۱۸:۰۰)", txt)
        self.assertIn("(👤 سارا · ⚠️ دیروز)", txt)

    def test_main_keyboard_few_and_many(self):
        few = render.kb_main(self.lst(), [self.task(i) for i in range(1, 9)], self.now)["inline_keyboard"]
        self.assertEqual(len(few), 9)
        self.assertTrue(few[0][0]["text"].startswith("⬜ ۱. "))
        self.assertEqual([b["text"] for b in few[-1]], ["➕ افزودن", "⏰ یادآور", "⚙️ بیشتر"])
        many = render.kb_main(self.lst(), [self.task(i, done=int(i == 1)) for i in range(1, 13)], self.now)["inline_keyboard"]
        self.assertEqual([len(r) for r in many], [5, 5, 2, 3])
        self.assertEqual(many[0][0], {"text": "✅۱", "callback_data": "t:1:1"})

    def test_undo_row_is_short_lived(self):
        l = self.lst(undo_at=self.now - 30, undo_label="حذف ۳")
        self.assertIn("undo:1", [b["callback_data"] for r in render.kb_main(l, [], self.now)["inline_keyboard"] for b in r])
        l = self.lst(undo_at=self.now - 500, undo_label="حذف ۳")
        self.assertNotIn("undo:1", [b["callback_data"] for r in render.kb_main(l, [], self.now)["inline_keyboard"] for b in r])

    def test_long_list_fits(self):
        tasks = [self.task(i, text="متن خیلی طولانی " * 20) for i in range(150)]
        self.assertLessEqual(len(render.render_text(self.lst(), tasks, now=self.now)), 4096)
        self.assertLessEqual(sum(len(r) for r in render.kb_main(self.lst(), tasks, self.now)["inline_keyboard"]), 100)

    def test_deep_link(self):
        self.assertEqual(render.deep_link("reminds_bot", GID), "https://ble.ir/reminds_bot?start=g4918886273")
        self.assertEqual(render.deep_link("reminds_bot", -100), "https://ble.ir/reminds_bot?start=gm100")


# ------------------------------------------------------------------------------------------ UX
class TestButtonsUX(Base):
    def test_welcome_on_add(self):
        self.bot.handle_update({"update_id": 1, "message": {"message_id": 1, "chat": GROUP, "from": REZA,
                                "new_chat_members": [{"id": 9, "is_bot": True, "username": "reminds_bot"}]}})
        p = self.api.of("sendMessage")[0]
        self.assertEqual(len(p["text"].splitlines()), 4)
        self.assertEqual(p["reply_markup"]["inline_keyboard"], [[{"text": "📋 ساخت لیست", "callback_data": "new0"}]])
        self.bot.handle_update({"update_id": 2, "callback_query": {"id": "q", "data": "new0", "from": REZA,
                                "message": {"message_id": 501, "chat": GROUP}}})
        self.assertIsNotNone(self.lst())
        self.assertTrue(self.api.of("pinChatMessage"))
        self.assertIn({"chat_id": GID, "message_id": 501}, self.api.of("deleteMessage"))

    def test_add_button_force_reply_then_reply(self):
        self.msg("/new")
        list_mid = self.lst()["message_id"]
        self.cb("add:%d" % self.lst()["id"])
        prompt = self.api.of("sendMessage")[-1]
        self.assertEqual(prompt["reply_markup"], {"force_reply": True, "selective": True})
        self.assertIn("@reza", prompt["text"])
        pmid = self.api.next_mid
        umid = self.msg("نان\n- شیر @ali\nپنیر | فردا ۱۸", reply_to=pmid)
        ts = self.tasks()
        self.assertEqual([t["text"] for t in ts], ["نان", "شیر", "پنیر"])
        self.assertEqual(ts[1]["assignee_username"], "ali")
        self.assertEqual(ts[2]["due_at"], T(2026, 10, 12, 18))
        deleted = self.api.of("deleteMessage")
        self.assertIn({"chat_id": GID, "message_id": pmid}, deleted)
        self.assertIn({"chat_id": GID, "message_id": umid}, deleted)
        self.assertEqual(self.api.of("editMessageText")[-1]["message_id"], list_mid)
        self.assertEqual(len(self.api.of("pinChatMessage")), 1)

    def test_prompt_accepts_next_plain_message_and_fallback(self):
        self.api.fail["sendMessage_force"] = None
        self.msg("/new")
        orig = self.api.call

        def no_force(method, **p):
            if method == "sendMessage" and "force_reply" in (p.get("reply_markup") or {}):
                self.api.calls.append((method, p))
                raise ApiError(method, 400, "bad reply_markup")
            return orig(method, **p)
        self.api.call = no_force
        self.cb("add:%d" % self.lst()["id"])
        self.assertTrue(db.kv_get("cap.force_reply").startswith("rejected"))
        self.msg("کار بدون ریپلای")                     # same user, within PROMPT_TTL
        self.assertEqual([t["text"] for t in self.tasks()], ["کار بدون ریپلای"])
        self.msg("گپ معمولی")                           # prompt consumed: chatter is ignored
        self.assertEqual(len(self.tasks()), 1)

    def test_toggle_uses_toast_not_messages(self):
        self.msg("/add a\nb")
        n_sent = len(self.api.of("sendMessage"))
        ans = self.cb("t:%d:%d" % (self.lst()["id"], self.tasks()[1]["id"]), user=ALI)
        self.assertEqual(ans["text"], "✅ انجام شد")
        self.assertEqual(self.tasks()[1]["done_by"], "علی")
        self.assertEqual(len(self.api.of("sendMessage")), n_sent)

    def test_text_command_ack_autodeletes(self):
        self.msg("/add a")
        ack_mid = self.api.next_mid
        self.bot.tick(self.clock.t + 5)
        self.assertNotIn({"chat_id": GID, "message_id": ack_mid}, self.api.of("deleteMessage"))
        self.bot.tick(self.clock.t + 11)
        self.assertIn({"chat_id": GID, "message_id": ack_mid}, self.api.of("deleteMessage"))

    def test_more_menu_edits_same_message_and_delete_undo(self):
        self.msg("/add a\nb\nc")
        L, mid = self.lst()["id"], self.lst()["message_id"]
        self.cb("more:%d" % L)
        e = self.api.of("editMessageText")[-1]
        self.assertEqual(e["message_id"], mid)
        self.assertIn("pk:%d:del" % L, self.datas(self.last_kb()))
        self.cb("pk:%d:del" % L)
        self.assertIn("pdel:%d:%d" % (L, self.tasks()[1]["id"]), self.datas(self.last_kb()))
        self.cb("pdel:%d:%d" % (L, self.tasks()[1]["id"]))
        self.assertEqual([t["text"] for t in self.tasks()], ["a", "c"])
        self.assertIn("undo:%d" % L, self.datas(self.last_kb()))
        ans = self.cb("undo:%d" % L)
        self.assertIn("برگشت", ans["text"])
        self.assertEqual([t["text"] for t in self.tasks()], ["a", "b", "c"])

    def test_undo_button_expires_via_tick(self):
        self.msg("/new")
        self.msg("/add a")
        self.assertIn("undo:%d" % self.lst()["id"], self.datas(self.last_kb()))
        self.clock.t += 200
        self.bot.tick()
        self.assertNotIn("undo:%d" % self.lst()["id"], self.datas(self.last_kb()))
        self.assertEqual(self.lst()["undo_shown"], 0)

    def test_assign_via_buttons(self):
        self.msg("سلام", user=ALI)          # Ali interacts -> becomes a known member
        self.msg("/add خرید")
        L, tid = self.lst()["id"], self.tasks()[0]["id"]
        self.cb("pasg:%d:%d" % (L, tid))
        self.assertIn("as:%d:%d:2" % (L, tid), self.datas(self.last_kb()))
        self.cb("as:%d:%d:2" % (L, tid))
        self.assertEqual(self.tasks()[0]["assignee_id"], 2)
        self.assertIn("👤 @ali", self.api.of("editMessageText")[-1]["text"])

    def test_due_preset_and_written(self):
        self.msg("/add a\nb")
        L, t1, t2 = self.lst()["id"], self.tasks()[0]["id"], self.tasks()[1]["id"]
        self.cb("du:%d:%d:3" % (L, t1))                  # فردا ۱۸
        self.assertEqual(self.tasks()[0]["due_at"], T(2026, 10, 12, 18))
        self.cb("du:%d:%d:w" % (L, t2))
        pmid = self.api.next_mid
        self.msg("۲۵ مهر ۱۰:۳۰", reply_to=pmid)
        self.assertEqual(self.tasks()[1]["due_at"], T(2026, 10, 17, 10, 30))
        self.cb("du:%d:%d:x" % (L, t1))
        self.assertIsNone(self.tasks()[0]["due_at"])

    def test_list_offer_only_when_addressed(self):
        self.msg("خرید:\n1. نان\n2. شیر")                       # not addressed -> ignored
        self.assertEqual(self.api.of("sendMessage"), [])
        self.msg("@reminds_bot\n1. نان\n2. شیر ✅")
        offer = self.api.of("sendMessage")[-1]
        self.assertEqual(offer["reply_markup"]["inline_keyboard"][0][0]["text"], "📋 تبدیل به لیست")
        self.cb("cv", mid=self.api.next_mid)
        self.assertEqual([(t["text"], t["done"]) for t in self.tasks()], [("نان", 0), ("شیر", 1)])

    def test_new_list_confirm_archives_old(self):
        self.msg("/add a")
        old = self.lst()
        self.cb("nw:%d" % old["id"])
        self.assertIn("nwy:%d" % old["id"], self.datas(self.last_kb()))
        self.cb("nwy:%d" % old["id"])
        self.assertNotEqual(self.lst()["id"], old["id"])
        self.assertIn("📦 بایگانی شد", [p for p in self.api.of("editMessageText") if p["message_id"] == old["message_id"]][-1]["text"])
        ans = self.cb("t:%d:%d" % (old["id"], db.tasks(old["id"])[0]["id"]), mid=old["message_id"])
        self.assertIn("بایگانی", ans["text"])

    def test_legacy_v1_button(self):
        self.msg("/add a")
        self.cb("t:%d" % self.tasks()[0]["id"])
        self.assertEqual(self.tasks()[0]["done"], 1)

    def test_celebrate_once(self):
        self.msg("/add a\nb")
        self.msg("انجام شد ۱ ۲")
        self.assertEqual(sum("🎉" in t for t in self.api.sent_texts()), 1)
        self.msg("/done 2")
        self.assertEqual(sum("🎉" in t for t in self.api.sent_texts()), 1)


class TestPermissions(Base):
    def test_lock_when_bale_denies_admin_lookup(self):
        self.api.fail["getChatMember"] = (403, "Forbidden: permission_denied")
        self.api.fail["getChatAdministrators"] = (403, "Forbidden: permission_denied")
        self.msg("/add a", user=ALI)                       # Ali creates the list
        self.cb("lock:%d" % self.lst()["id"], user=SARA)  # not creator, admin unknown
        self.assertFalse(self.lst()["locked"])
        self.cb("lock:%d" % self.lst()["id"], user=ALI)
        self.assertTrue(self.lst()["locked"])
        ans = self.cb("t:%d:%d" % (self.lst()["id"], self.tasks()[0]["id"]), user=SARA)
        self.assertTrue(ans["show_alert"])
        self.assertEqual(self.tasks()[0]["done"], 0)
        self.cb("t:%d:%d" % (self.lst()["id"], self.tasks()[0]["id"]), user=ALI)
        self.assertEqual(self.tasks()[0]["done"], 1)

    def test_admin_can_lock(self):
        self.msg("/add a", user=ALI)
        self.cb("lock:%d" % self.lst()["id"], user=REZA)   # admin via getChatMember
        self.assertTrue(self.lst()["locked"])


# ------------------------------------------------------------------------------------------ reminders
class TestReminders(Base):
    def setUp(self):
        super().setUp()
        self.msg("/add نان @ali\nشیر\nقبض", user=REZA)
        self.msg("سلام", user=ALI)
        self.msg("سلام", user=SARA)
        db.upsert_user(2, "علی", "ali")
        # Ali started the bot via the deep link
        self.msg("/start g%d" % GID, user=ALI, chat={"id": 2, "type": "private"})
        self.L = self.lst()["id"]
        self.api.calls.clear()

    def test_deep_link_registers(self):
        u = db.user(2)
        self.assertEqual(u["dm_ok"], 1)
        self.assertTrue(db.q("SELECT 1 FROM members WHERE chat_id=? AND user_id=2", (GID,)))

    def test_daily_reminder_fires_once_group_and_dm(self):
        self.cb("rt:%d:daily:%d" % (self.L, 18 * 60))
        r = db.reminders(GID)[0]
        self.assertEqual(r["next_at"], T(2026, 10, 11, 18))
        self.bot.tick(T(2026, 10, 11, 17, 59))
        self.assertEqual(self.api.sent_texts(GID), [])
        self.bot.tick(T(2026, 10, 11, 18, 0, ))
        group_posts = [t for t in self.api.sent_texts(GID) if "یادآوری" in t]
        self.assertEqual(len(group_posts), 1)
        self.assertIn("@ali", group_posts[0])
        self.assertIn("یادآور شخصی ندارند: ", group_posts[0])
        self.assertIn("سارا", group_posts[0])
        dms = [p for p in self.api.of("sendMessage") if p["chat_id"] == 2]
        self.assertEqual(len(dms), 1)
        self.assertIn("کارهای شما", dms[0]["text"])
        self.assertIn("dt:", json.dumps(dms[0]["reply_markup"]))
        self.assertFalse([p for p in self.api.of("sendMessage") if p["chat_id"] == 3])   # Sara never started
        self.bot.tick(T(2026, 10, 11, 18, 0, ))                                          # no duplicate
        self.assertEqual(len([t for t in self.api.sent_texts(GID) if "یادآوری" in t]), 1)
        self.assertEqual(db.reminders(GID)[0]["next_at"], T(2026, 10, 12, 18))
        # next day: the 'no personal reminder' line is not repeated the same day, but is the next day
        self.bot.tick(T(2026, 10, 12, 18, 0))
        self.assertIn("یادآور شخصی ندارند", self.api.sent_texts(GID)[-1])

    def test_claim_is_compare_and_set(self):
        rid = db.add_reminder(GID, "daily", 600, 1000.0)
        self.assertTrue(db.claim_reminder(rid, 1000.0, 2000.0))
        self.assertFalse(db.claim_reminder(rid, 1000.0, 2000.0))

    def test_stale_reminder_after_downtime_is_skipped(self):
        self.cb("rt:%d:daily:%d" % (self.L, 9 * 60))
        self.bot.tick(T(2026, 10, 12, 12, 0))     # 3h late
        self.assertEqual([t for t in self.api.sent_texts(GID) if "یادآوری" in t], [])
        self.assertEqual(db.reminders(GID)[0]["next_at"], T(2026, 10, 13, 9))

    def test_once_reminder_deactivates(self):
        self.cb("ro:%d:1h" % self.L)
        self.bot.tick(self.clock.t + 3601)
        self.bot.tick(self.clock.t + 7200)
        self.assertEqual(len([t for t in self.api.sent_texts(GID) if "یادآوری" in t]), 1)
        self.assertEqual(db.reminders(GID), [])

    def test_work_days_skip_thursday_friday(self):
        self.cb("rt:%d:work:%d" % (self.L, 9 * 60))
        self.clock.t = T(2026, 10, 15, 10)        # Thursday
        self.bot.reschedule_chat(GID)
        self.assertEqual(db.reminders(GID)[0]["next_at"], T(2026, 10, 17, 9))   # Saturday

    def test_group_quiet_hours_toggle_and_presets(self):
        self.cb("rt:%d:daily:%d" % (self.L, 23 * 60 + 30))
        self.assertEqual(db.reminders(GID)[0]["next_at"], T(2026, 10, 12, 7))   # default quiet 23-07
        self.cb("qt:%d:r" % self.L)                                              # 🌙 off
        self.assertEqual(db.chat(GID)["quiet_on"], 0)
        self.assertEqual(db.reminders(GID)[0]["next_at"], T(2026, 10, 11, 23, 30))
        self.assertIn("🌙 ساعت سکوت: خاموش", json.dumps(self.last_kb(), ensure_ascii=False))
        self.cb("qs:%d:0:8" % self.L)                                            # preset 00-08, turns it on
        c = db.chat(GID)
        self.assertEqual((c["quiet_on"], c["quiet_start"], c["quiet_end"]), (1, 0, 8))
        self.assertEqual(db.reminders(GID)[0]["next_at"], T(2026, 10, 11, 23, 30))

    def test_user_quiet_defers_dm_and_can_be_turned_off(self):
        db.set_chat(GID, quiet_on=0)
        self.cb("rt:%d:daily:%d" % (self.L, 23 * 60 + 30))
        self.bot.tick(T(2026, 10, 11, 23, 30))
        self.assertTrue([t for t in self.api.sent_texts(GID) if "یادآوری" in t])    # group post (group quiet off)
        self.assertFalse([p for p in self.api.of("sendMessage") if p["chat_id"] == 2])
        self.bot.tick(T(2026, 10, 12, 7, 0))                                       # Ali's quiet ends
        self.assertEqual(len([p for p in self.api.of("sendMessage") if p["chat_id"] == 2]), 1)
        # Ali turns his quiet hours off in DM
        self.cb("uq", user=ALI, chat={"id": 2, "type": "private"}, mid=77)
        self.assertEqual(db.user(2)["quiet_on"], 0)
        self.bot.tick(T(2026, 10, 12, 23, 30))
        self.assertEqual(len([p for p in self.api.of("sendMessage") if p["chat_id"] == 2]), 2)

    def test_user_quiet_presets_and_mute(self):
        pv = {"id": 2, "type": "private"}
        self.cb("uqs:2:22:7", user=ALI, chat=pv, mid=77)
        self.assertEqual((db.user(2)["quiet_start"], db.user(2)["quiet_end"]), (22, 7))
        self.cb("um", user=ALI, chat=pv, mid=77)
        self.assertEqual(db.user(2)["muted"], 1)
        db.set_chat(GID, quiet_on=0)
        self.bot.fire_reminder(GID, T(2026, 10, 11, 12))
        self.bot.tick(T(2026, 10, 11, 12))
        self.assertFalse([p for p in self.api.of("sendMessage") if p["chat_id"] == 2])

    def test_dm_tick_updates_group_list(self):
        db.set_chat(GID, quiet_on=0)
        self.bot.fire_reminder(GID, self.clock.t)
        self.bot.tick(self.clock.t)
        dm = [p for p in self.api.of("sendMessage") if p["chat_id"] == 2][-1]
        tid = self.tasks()[0]["id"]
        ans = self.cb("dt:%d" % tid, user=ALI, chat={"id": 2, "type": "private"}, mid=900, markup=dm["reply_markup"])
        self.assertIn("لیست گروه", ans["text"])
        self.assertEqual(self.tasks()[0]["done"], 1)
        self.assertEqual(self.api.of("editMessageText")[-1]["chat_id"], GID)       # pinned list edited
        self.assertIn("✔️ انجام شد", json.dumps(self.api.of("editMessageReplyMarkup")[-1], ensure_ascii=False))
        ans = self.cb("dt:%d" % tid, user={"id": 50, "first_name": "غریبه"}, chat={"id": 50, "type": "private"}, mid=1)
        self.assertTrue(ans["show_alert"])

    def test_dm_routing_mine_vs_free(self):
        lst = self.lst()
        mine, free = self.bot.tasks_for_user(lst, db.user(2))
        self.assertEqual([t["text"] for _i, t in mine], ["نان"])
        self.assertEqual([t["text"] for _i, t in free], ["شیر", "قبض"])

    def test_due_reminders_pre_and_due_once(self):
        tid = self.tasks()[0]["id"]
        db.set_task(tid, due_at=T(2026, 10, 11, 18), due_has_time=1)
        self.bot.tick(T(2026, 10, 11, 16, 59))
        self.assertEqual(self.api.sent_texts(GID), [])
        self.bot.tick(T(2026, 10, 11, 17, 0))
        self.bot.tick(T(2026, 10, 11, 17, 1))
        pre = [t for t in self.api.sent_texts(GID) if "تا یک ساعت دیگر" in t]
        self.assertEqual(len(pre), 1)
        self.bot.tick(T(2026, 10, 11, 18, 0))
        self.bot.tick(T(2026, 10, 11, 18, 5))
        self.assertEqual(len([t for t in self.api.sent_texts(GID) if "رسیده" in t]), 1)
        self.assertEqual(len([p for p in self.api.of("sendMessage") if p["chat_id"] == 2]), 2)   # assignee DMs

    def test_due_reminder_waits_for_group_quiet(self):
        tid = self.tasks()[1]["id"]
        db.set_task(tid, due_at=T(2026, 10, 12, 2), due_has_time=1)
        db.set_chat(GID, due_mode="due")
        self.bot.tick(T(2026, 10, 12, 2, 0))
        self.assertEqual(self.api.sent_texts(GID), [])
        self.bot.tick(T(2026, 10, 12, 7, 0))
        self.assertEqual(len([t for t in self.api.sent_texts(GID) if "رسیده" in t]), 1)

    def test_due_mode_cycle_and_off(self):
        self.cb("dm:%d" % self.L)
        self.assertEqual(db.chat(GID)["due_mode"], "due")
        self.cb("dm:%d" % self.L)
        self.assertEqual(db.chat(GID)["due_mode"], "off")
        db.set_task(self.tasks()[0]["id"], due_at=self.clock.t + 60, due_has_time=1)
        self.bot.tick(self.clock.t + 120)
        self.assertEqual(self.api.sent_texts(GID), [])

    def test_reminder_menu_has_deep_link_and_custom_time(self):
        self.cb("rem:%d" % self.L)
        kb = self.last_kb()
        urls = [b.get("url") for r in kb for b in r if b.get("url")]
        self.assertEqual(urls, ["https://ble.ir/reminds_bot?start=g%d" % GID])
        self.cb("rw:%d:daily" % self.L)
        pmid = self.api.next_mid
        self.msg("۷:۴۵", reply_to=pmid)
        self.assertEqual(db.reminders(GID)[0]["minute"], 7 * 60 + 45)
        self.assertIn("هر روز ۰۷:۴۵", self.api.of("editMessageText")[-1]["text"])

    def test_turn_off_all(self):
        self.cb("rt:%d:daily:540" % self.L)
        self.cb("rt:%d:daily:1080" % self.L)
        self.cb("roff:%d" % self.L)
        self.assertEqual(db.reminders(GID), [])

    def test_stats(self):
        self.cb("t:%d:%d" % (self.L, self.tasks()[0]["id"]), user=ALI)
        self.cb("t:%d:%d" % (self.L, self.tasks()[1]["id"]), user=ALI)
        self.cb("t:%d:%d" % (self.L, self.tasks()[2]["id"]), user=REZA)
        s = self.bot.stats_text(GID)
        self.assertIn("🥇 علی: ۲ کار", s)
        self.assertIn("🥈 رضا: ۱ کار", s)


class TestPrivate(Base):
    def test_private_plain_text_adds_and_no_dm_logic(self):
        pv = {"id": 1, "type": "private"}
        self.msg("نان\nشیر", chat=pv)
        self.assertEqual(len(self.tasks(1)), 2)
        self.api.fail["pinChatMessage"] = (400, "x")
        self.msg("/list", chat=pv)
        self.assertFalse([t for t in self.api.sent_texts() if "ادمین" in t])


if __name__ == "__main__":
    unittest.main()
