import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from parse import parse_command, parse_numbers, parse_import, parse_add_lines, split_num_text, to_fa_digits


class TestCommands(unittest.TestCase):
    def test_slash(self):
        self.assertEqual(parse_command("/done 1 3 5"), ("done", "1 3 5"))
        self.assertEqual(parse_command("/done@reminds_bot ۲", "reminds_bot"), ("done", "۲"))
        self.assertIsNone(parse_command("/done@other_bot 2", "reminds_bot"))
        self.assertEqual(parse_command("/start"), ("help", ""))
        self.assertEqual(parse_command("/clear_done"), ("clear_done", ""))
        self.assertIsNone(parse_command("/unknowncmd"))

    def test_multiline_add(self):
        self.assertEqual(parse_command("/add\nنان\nشیر"), ("add", "نان\nشیر"))
        self.assertEqual(parse_command("/add نان\nشیر"), ("add", "نان\nشیر"))

    def test_fa_aliases(self):
        self.assertEqual(parse_command("انجام شد 3"), ("done", "3"))
        self.assertEqual(parse_command("انجامشد ۳"), ("done", "۳"))
        self.assertEqual(parse_command("انجام\u200cشد ۳ و ۵"), ("done", "۳ و ۵"))
        self.assertEqual(parse_command("حذف ۲"), ("del", "۲"))
        self.assertEqual(parse_command("اضافه خرید نان"), ("add", "خرید نان"))
        self.assertEqual(parse_command("لیست"), ("list", ""))
        self.assertEqual(parse_command("ویرایش ۲ متن تازه"), ("edit", "۲ متن تازه"))
        self.assertEqual(parse_command("لیست جدید"), ("new", ""))
        self.assertEqual(parse_command("برگشت ۴"), ("undo", "۴"))

    def test_no_false_positives(self):
        for t in ["انجام شد؟", "حذف شد بالاخره", "لیست خرید رو کی میاره؟", "اضافه", "سلام", "انجام شد", "قفل در خرابه"]:
            self.assertIsNone(parse_command(t), t)
        self.assertEqual(parse_command("اضافه", is_reply=True), ("add", ""))


class TestNumbers(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(parse_numbers("1 3 5"), ([1, 3, 5], []))
        self.assertEqual(parse_numbers("۱،۳"), ([1, 3], []))
        self.assertEqual(parse_numbers("٢ و ٤"), ([2, 4], []))
        self.assertEqual(parse_numbers("2-4"), ([2, 3, 4], []))
        self.assertEqual(parse_numbers("#2, 2"), ([2], []))
        self.assertEqual(parse_numbers("abc")[1], ["abc"])

    def test_split(self):
        self.assertEqual(split_num_text("۳ متن جدید"), (3, "متن جدید"))
        self.assertEqual(split_num_text("متن"), (None, "متن"))

    def test_fa_digits(self):
        self.assertEqual(to_fa_digits(1405), "۱۴۰۵")


class TestImport(unittest.TestCase):
    def test_numbered_with_marks(self):
        src = "📋 لیست کارها\n\n✅ ۱. خرید نان  — رضا\n⬜ ۲. تمیز کردن خانه\n⬜ 3) پرداخت قبض\n\nپیشرفت: ۱ از ۳ (۳۳٪)\n▰▰▰▱▱▱▱▱▱▱\n🕒 آخرین به‌روزرسانی: ۱۹ مهر"
        got = parse_import(src)
        self.assertEqual(got[1:], [("تمیز کردن خانه", False), ("پرداخت قبض", False)])
        self.assertTrue(got[0][1])
        self.assertTrue(got[0][0].startswith("خرید نان"))

    def test_bullets_and_plain(self):
        self.assertEqual(parse_import("- شیر\n• پنیر\n* کره\n▫️ تخم‌مرغ"),
                         [("شیر", False), ("پنیر", False), ("کره", False), ("تخم‌مرغ", False)])
        self.assertEqual(parse_import("نان\nشیر\n\nپنیر"), [("نان", False), ("شیر", False), ("پنیر", False)])

    def test_done_markers(self):
        self.assertEqual(parse_import("☑ اول\n1. دوم ✅\n[x] سوم\n[ ] چهارم\n2️⃣ پنجم"),
                         [("اول", True), ("دوم", True), ("سوم", True), ("چهارم", False), ("پنجم", False)])

    def test_title_dropped_when_list_is_marked(self):
        self.assertEqual(parse_import("کارهای امروز:\n1. الف\n2. ب"), [("الف", False), ("ب", False)])

    def test_add_lines(self):
        self.assertEqual(parse_add_lines("2 کیلو برنج"), ["2 کیلو برنج"])
        self.assertEqual(parse_add_lines("- نان"), ["نان"])
        self.assertEqual(parse_add_lines("نان\n2. شیر\n\n- پنیر"), ["نان", "شیر", "پنیر"])


if __name__ == "__main__":
    unittest.main()


class TestMeta(unittest.TestCase):
    def test_extract(self):
        from parse import extract_meta, looks_like_list, mentions
        self.assertEqual(extract_meta("خرید نان @ali | فردا ۱۸"), ("خرید نان", "ali", "فردا ۱۸"))
        self.assertEqual(extract_meta("تماس با @sara_k"), ("تماس با", "sara_k", None))
        self.assertEqual(extract_meta("ایمیل a@b.com"), ("ایمیل a@b.com", None, None))
        self.assertTrue(looks_like_list("1. a\n2. b"))
        self.assertFalse(looks_like_list("سلام\nخوبی"))
        self.assertTrue(mentions("hi @Reminds_Bot", "reminds_bot"))

    def test_new_aliases(self):
        self.assertEqual(parse_command("/due 3 فردا"), ("due", "3 فردا"))
        self.assertEqual(parse_command("یادآور"), ("remind", ""))
