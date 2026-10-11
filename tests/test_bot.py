"""Rendering + command/toggle logic against a fake Bale API."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db, render
from api import ApiError
from app import TodoBot

GROUP = {"id": -100, "type": "group", "title": "G"}
REZA = {"id": 1, "first_name": "رضا"}
ALI = {"id": 2, "first_name": "علی"}


class FakeApi:
    def __init__(self):
        self.calls, self.next_mid, self.fail = [], 500, {}
        self.admins = {1}

    def call(self, method, **p):
        self.calls.append((method, p))
        if method in self.fail:
            err = self.fail[method]
            raise ApiError(method, *err)
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


class Base(unittest.TestCase):
    def setUp(self):
        db.connect(":memory:")
        self.api = FakeApi()
        self.bot = TodoBot(self.api, "reminds_bot", clock=lambda: 1760000000)
        self.mid = 0

    def msg(self, text, user=REZA, chat=GROUP, reply=None):
        self.mid += 1
        m = {"message_id": self.mid, "chat": chat, "from": user, "text": text}
        if reply:
            m["reply_to_message"] = {"message_id": 1, "text": reply, "chat": chat}
        self.bot.handle_update({"update_id": self.mid, "message": m})

    def cb(self, data, user=REZA, chat=GROUP, mid=501):
        self.bot.handle_update({"update_id": 999, "callback_query": {"id": "q1", "data": data, "from": user,
                                                                     "message": {"message_id": mid, "chat": chat}}})

    def lst(self):
        return db.active_list(GROUP["id"])


class TestRender(unittest.TestCase):
    def test_progress_and_marks(self):
        lst = {"id": 1, "title": "📋 لیست کارها", "locked": 0}
        tasks = [{"id": i, "text": "کار*%d" % i, "done": i <= 3, "done_by": "رضا" if i <= 3 else None} for i in range(1, 8)]
        txt = render.render_text(lst, tasks, now=1760000000)
        self.assertIn("پیشرفت: ۳ از ۷ (۴۳٪)", txt)
        self.assertIn("✅ ۱. کار∗1  — رضا", txt)
        self.assertIn("⬜ ۴.", txt)
        self.assertIn("▰▰▰▰▱▱▱▱▱▱", txt)
        self.assertIn("۱۷ مهر، ساعت ۱۲:ۨ".replace("ۨ", ""), txt)  # 2025-10-09 12:23 Tehran = 17 Mehr 1404
        kb = render.keyboard(lst, tasks)["inline_keyboard"]
        self.assertEqual([len(r) for r in kb], [5, 2, 1])
        self.assertEqual(kb[0][0], {"text": "✅۱", "callback_data": "t:1"})
        self.assertEqual(kb[-1][0]["callback_data"], "r:1")

    def test_few_tasks_wide_buttons(self):
        lst = {"id": 1, "title": "t", "locked": 1}
        kb = render.keyboard(lst, [{"id": 9, "text": "x" * 50, "done": 0}])["inline_keyboard"]
        self.assertTrue(kb[0][0]["text"].startswith("⬜ ۱. x"))
        self.assertLessEqual(len(kb[0][0]["text"]), 32)
        self.assertIn("🔒", render.render_text(lst, [], now=0))

    def test_jalali(self):
        self.assertEqual(render.gregorian_to_jalali(2026, 10, 11), (1405, 7, 19))
        self.assertEqual(render.gregorian_to_jalali(2025, 3, 21), (1404, 1, 1))

    def test_long_list_fits(self):
        lst = {"id": 1, "title": "t", "locked": 0}
        tasks = [{"id": i, "text": "متن خیلی طولانی " * 20, "done": 0, "done_by": None} for i in range(150)]
        self.assertLessEqual(len(render.render_text(lst, tasks)), 4096)
        self.assertLessEqual(sum(len(r) for r in render.keyboard(lst, tasks)["inline_keyboard"]), 100)


class TestFlow(Base):
    def test_new_sends_and_pins_then_edits(self):
        self.msg("/new")
        self.assertEqual(len(self.api.of("sendMessage")), 1)
        self.assertEqual(self.api.of("pinChatMessage")[0]["message_id"], 501)
        self.msg("/add نان\nشیر\nپنیر")
        self.assertEqual(self.api.of("editMessageText")[-1]["message_id"], 501)
        self.assertEqual([t["text"] for t in db.tasks(self.lst()["id"])], ["نان", "شیر", "پنیر"])
        # only the ack was sent, no new list message
        self.assertEqual(len(self.api.of("pinChatMessage")), 1)

    def test_done_multiple_persian_and_celebrate(self):
        self.msg("/add الف\nب\nج")
        self.msg("انجام شد ۱ ۳")
        tasks = db.tasks(self.lst()["id"])
        self.assertEqual([t["done"] for t in tasks], [1, 0, 1])
        self.assertEqual(tasks[0]["done_by"], "رضا")
        self.assertFalse(any("🎉" in p["text"] for p in self.api.of("sendMessage")))
        self.msg("/done 2")
        self.assertTrue(any("🎉" in p["text"] for p in self.api.of("sendMessage")))
        n = sum("🎉" in p["text"] for p in self.api.of("sendMessage"))
        self.msg("/done 2")  # already done: no second celebration
        self.assertEqual(sum("🎉" in p["text"] for p in self.api.of("sendMessage")), n)

    def test_undo_del_edit_clear(self):
        self.msg("/add a\nb\nc\nd")
        self.msg("/done 1 2")
        self.msg("/undo ۲")
        self.msg("/del 4")
        self.msg("/edit 3 C!")
        self.assertEqual([(t["text"], t["done"]) for t in db.tasks(self.lst()["id"])], [("a", 1), ("b", 0), ("C!", 0)])
        self.msg("/clear_done")
        self.assertEqual([t["text"] for t in db.tasks(self.lst()["id"])], ["b", "C!"])
        self.msg("/done 9")
        self.assertIn("در لیست نیست", self.api.of("sendMessage")[-1]["text"])

    def test_import_reply(self):
        self.msg("/import", reply="کارها:\n1. خرید ✅\n2. شستن\n3. پختن")
        self.assertEqual([(t["text"], t["done"]) for t in db.tasks(self.lst()["id"])],
                         [("خرید", 1), ("شستن", 0), ("پختن", 0)])
        self.msg("/add", reply="- یک\n- دو")
        self.assertEqual(len(db.tasks(self.lst()["id"])), 5)

    def test_button_toggle(self):
        self.msg("/add a\nb")
        tid = db.tasks(self.lst()["id"])[1]["id"]
        self.cb("t:%d" % tid, user=ALI)
        self.assertEqual(db.get_task(tid)["done"], 1)
        self.assertEqual(db.get_task(tid)["done_by"], "علی")
        self.assertEqual(self.api.of("answerCallbackQuery")[-1]["text"], "✅ انجام شد")
        self.cb("t:%d" % tid, user=ALI)
        self.assertEqual(db.get_task(tid)["done"], 0)

    def test_lock(self):
        self.msg("/add a", user=ALI)   # Ali creates the list
        self.msg("/lock", user={"id": 3, "first_name": "x"})   # not admin, not creator
        self.assertFalse(self.lst()["locked"])
        self.msg("/lock", user=REZA)   # admin
        self.assertTrue(self.lst()["locked"])
        tid = db.tasks(self.lst()["id"])[0]["id"]
        self.cb("t:%d" % tid, user={"id": 3, "first_name": "x"})
        self.assertEqual(db.get_task(tid)["done"], 0)
        self.assertTrue(self.api.of("answerCallbackQuery")[-1]["show_alert"])
        self.cb("t:%d" % tid, user=ALI)  # creator may
        self.assertEqual(db.get_task(tid)["done"], 1)

    def test_admin_check_fallback(self):
        self.api.fail["getChatMember"] = (400, "not supported")
        self.api.calls.clear()
        self.msg("/add a", user=ALI)
        self.msg("/lock", user=REZA)
        self.assertTrue(self.api.of("getChatAdministrators"))
        self.assertTrue(self.lst()["locked"])

    def test_edit_failure_reposts_and_repins(self):
        self.msg("/add a")
        self.api.fail["editMessageText"] = (400, "message to edit not found")
        self.msg("/add b")
        self.assertEqual(len(self.api.of("pinChatMessage")), 2)
        self.assertNotEqual(self.lst()["message_id"], 501)

    def test_not_modified_is_fine(self):
        self.msg("/add a")
        self.api.fail["editMessageText"] = (400, "Bad Request: message is not modified")
        self.cb("r:%d" % self.lst()["id"])
        self.assertEqual(len(self.api.of("pinChatMessage")), 1)

    def test_pin_without_admin_hint_once(self):
        self.api.fail["pinChatMessage"] = (403, "not enough rights")
        self.msg("/new")
        self.msg("/list")
        hints = [p for p in self.api.of("sendMessage") if "ادمین" in p["text"]]
        self.assertEqual(len(hints), 1)

    def test_reset_confirm(self):
        self.msg("/add a\nb")
        self.msg("/reset")
        self.assertEqual(len(db.tasks(self.lst()["id"])), 2)
        self.cb("rs:%d:n" % self.lst()["id"])
        self.assertEqual(len(db.tasks(self.lst()["id"])), 2)
        self.cb("rs:%d:y" % self.lst()["id"])
        self.assertEqual(len(db.tasks(self.lst()["id"])), 0)

    def test_private_plain_text_adds(self):
        pv = {"id": 1, "type": "private"}
        self.msg("نان\nشیر", chat=pv)
        self.assertEqual(len(db.tasks(db.active_list(1)["id"])), 2)
        self.api.fail["pinChatMessage"] = (400, "x")
        self.msg("/list", chat=pv)
        self.assertFalse([p for p in self.api.of("sendMessage") if "ادمین" in p["text"]])

    def test_title_and_new_archive(self):
        self.msg("/add a")
        self.msg("/title خرید هفته")
        self.assertEqual(self.lst()["title"], "خرید هفته")
        old = self.lst()["id"]
        self.msg("/new سفر")
        self.assertNotEqual(self.lst()["id"], old)
        self.assertEqual(self.lst()["title"], "سفر")
        self.cb("t:%d" % db.tasks(old)[0]["id"])
        self.assertIn("قدیمی", self.api.of("answerCallbackQuery")[-1]["text"])

    def test_bot_added_to_group_says_hello(self):
        self.bot.handle_update({"update_id": 1, "message": {"message_id": 1, "chat": GROUP, "from": REZA,
                                "new_chat_members": [{"id": 9, "is_bot": True, "username": "reminds_bot"}]}})
        self.assertIn("/add", self.api.of("sendMessage")[0]["text"])


if __name__ == "__main__":
    unittest.main()
