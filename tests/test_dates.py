import os, sys, datetime, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dates
from dates import parse_due, fmt_due, in_quiet, quiet_end, next_daily


def T(y, mo, d, h=0, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=dates.TEHRAN).timestamp()


NOW = T(2026, 10, 11, 13, 20)    # Sunday (یکشنبه) 19 Mehr 1405


class TestDates(unittest.TestCase):
    def test_jalali(self):
        self.assertEqual(dates.gregorian_to_jalali(2026, 10, 11), (1405, 7, 19))
        self.assertEqual(dates.jalali_to_gregorian(1405, 7, 19), (2026, 10, 11))
        self.assertEqual(dates.jalali_to_gregorian(1404, 1, 1), (2025, 3, 21))
        self.assertEqual(dates.jalali_to_gregorian(1403, 12, 30), (2025, 3, 20))

    def test_parse(self):
        cases = {
            "فردا ۱۸": (T(2026, 10, 12, 18), True),
            "فردا ساعت ۹ صبح": (T(2026, 10, 12, 9), True),
            "پس‌فردا": (T(2026, 10, 13), False),
            "شنبه": (T(2026, 10, 17), False),
            "سه‌شنبه ۱۰": (T(2026, 10, 13, 10), True),
            "۲۵ مهر": (T(2026, 10, 17), False),
            "۲۵ مهر ۱۰:۳۰": (T(2026, 10, 17, 10, 30), True),
            "۱۴۰۵/۸/۱": (T(2026, 10, 23), False),
            "۱۸:۳۰": (T(2026, 10, 11, 18, 30), True),
            "۱۰": (T(2026, 10, 11, 22), True),            # 10 at 13:20 -> 22:00 today
            "9:00": (T(2026, 10, 12, 9), True),           # explicit minutes in the past -> tomorrow
            "۵ عصر": (T(2026, 10, 11, 17), True),
            "۲ ساعت دیگه": (NOW + 7200, True),
            "۳۰ دقیقه دیگر": (NOW + 1800, True),
            "امشب": (T(2026, 10, 11, 21), True),
            "۱ فروردین": (T(2027, 3, 21), False),          # past this year -> next year
        }
        for s, want in cases.items():
            self.assertEqual(parse_due(s, NOW), want, s)
        for s in ("سلام", "۲۵ ۲۵", "۳۲ مهر", "فردا ۲۵", ""):
            self.assertIsNone(parse_due(s, NOW), s)

    def test_fmt_and_overdue(self):
        self.assertEqual(fmt_due(T(2026, 10, 11, 18), True, NOW), "امروز ۱۸:۰۰")
        self.assertEqual(fmt_due(T(2026, 10, 12), False, NOW), "فردا")
        self.assertEqual(fmt_due(T(2026, 10, 14, 9), True, NOW), "چهارشنبه ۰۹:۰۰")
        self.assertEqual(fmt_due(T(2026, 11, 1), False, NOW), "۱۰ آبان")
        self.assertTrue(dates.is_overdue(T(2026, 10, 11, 12), True, NOW))
        self.assertFalse(dates.is_overdue(T(2026, 10, 11), False, NOW))   # date-only: overdue after the day

    def test_quiet(self):
        self.assertTrue(in_quiet(T(2026, 10, 11, 23, 30), 23, 7))
        self.assertTrue(in_quiet(T(2026, 10, 12, 6, 59), 23, 7))
        self.assertFalse(in_quiet(T(2026, 10, 12, 7, 0), 23, 7))
        self.assertTrue(in_quiet(T(2026, 10, 12, 0, 30), 0, 8))
        self.assertFalse(in_quiet(T(2026, 10, 11, 23, 30), 0, 8))
        self.assertEqual(quiet_end(T(2026, 10, 11, 23, 30), 23, 7), T(2026, 10, 12, 7))
        self.assertEqual(quiet_end(T(2026, 10, 12, 3), 23, 7), T(2026, 10, 12, 7))
        self.assertEqual(quiet_end(T(2026, 10, 12, 12), 23, 7), T(2026, 10, 12, 12))

    def test_next_daily(self):
        self.assertEqual(next_daily(NOW, 18 * 60), T(2026, 10, 11, 18))
        self.assertEqual(next_daily(NOW, 9 * 60), T(2026, 10, 12, 9))
        self.assertEqual(next_daily(T(2026, 10, 15, 10), 9 * 60, dates.WORK_DAYS), T(2026, 10, 17, 9))


if __name__ == "__main__":
    unittest.main()
