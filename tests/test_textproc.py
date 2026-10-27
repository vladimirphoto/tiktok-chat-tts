import unittest
from dataclasses import replace

from ttchat import textproc as tp
from ttchat.settings import Settings


class CleanTextTests(unittest.TestCase):
    def test_removes_links(self):
        self.assertEqual(tp.clean_text("зайди на https://spam.ru/x?a=1 сейчас"), "зайди на сейчас")
        self.assertEqual(tp.clean_text("смотри www.site.com"), "смотри")
        self.assertEqual(tp.clean_text("пиши в shop.ru пожалуйста"), "пиши в пожалуйста")

    def test_removes_emoji_and_tiktok_emotes(self):
        self.assertEqual(tp.clean_text("круто 😍🔥 [rose] очень"), "круто очень")
        self.assertEqual(tp.clean_text("❤️ привет"), "привет")

    def test_keeps_bracket_cyrillic(self):
        self.assertIn("привет", tp.clean_text("[привет]"))

    def test_collapses_stretched_letters(self):
        self.assertEqual(tp.clean_text("приветттттт"), "привет")
        self.assertEqual(tp.clean_text("ааааааа"), "а")
        self.assertEqual(tp.clean_text("Анна и Ванна"), "Анна и Ванна")  # двойные буквы целы

    def test_does_not_mangle_numbers(self):
        self.assertEqual(tp.clean_text("у меня 1000000 рублей"), "у меня 1000000 рублей")

    def test_collapses_repeated_punctuation(self):
        self.assertEqual(tp.clean_text("что?????"), "что?")
        self.assertEqual(tp.clean_text("ого!!!"), "ого!")

    def test_removes_at_sign_and_collapses_spaces(self):
        self.assertEqual(tp.clean_text("@вася   привет"), "вася привет")


class NickTests(unittest.TestCase):
    def test_cleans_nick(self):
        self.assertEqual(tp.clean_nick("Маша_Иванова😍"), "Маша Иванова")

    def test_drops_long_digit_runs(self):
        self.assertEqual(tp.clean_nick("user8472659314"), "user")

    def test_empty_nick_gets_default(self):
        self.assertEqual(tp.clean_nick("😍😍"), tp.DEFAULT_NICK)
        self.assertEqual(tp.clean_nick(""), tp.DEFAULT_NICK)
        self.assertEqual(tp.clean_nick(None), tp.DEFAULT_NICK)


class TruncateTests(unittest.TestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(tp.truncate("привет мир", 50), "привет мир")

    def test_cuts_on_word_boundary(self):
        self.assertEqual(tp.truncate("один два три четыре пять", 15), "один два три")

    def test_hard_cut_when_no_space(self):
        self.assertEqual(tp.truncate("a" * 100, 10), "a" * 10)


class BlacklistTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(tp.parse_blacklist("Плохо, ЁЖ;\n  слово "), ["плохо", "еж", "слово"])
        self.assertEqual(tp.parse_blacklist(""), [])


class RecentTrackerTests(unittest.TestCase):
    def test_window(self):
        now = [0.0]
        tracker = tp.RecentTracker(window=10, clock=lambda: now[0])
        self.assertFalse(tracker.seen("a"))
        now[0] = 5
        self.assertTrue(tracker.seen("a"))
        now[0] = 16
        self.assertFalse(tracker.seen("a"))  # прошло больше окна от последнего показа

    def test_capacity(self):
        tracker = tp.RecentTracker(window=1000, capacity=3, clock=lambda: 0.0)
        for key in "abcde":
            tracker.seen(key)
        self.assertLessEqual(len(tracker._seen), 4)


class PrepareChatTests(unittest.TestCase):
    def setUp(self):
        self.s = Settings()

    def test_basic_phrase_with_nick(self):
        r = tp.prepare_chat("Маша", "Всем привет!", self.s)
        self.assertEqual(r.phrase, "Маша: Всем привет!")
        self.assertIsNone(r.reason)

    def test_without_nick(self):
        r = tp.prepare_chat("Маша", "Всем привет", replace(self.s, read_nick=False))
        self.assertEqual(r.phrase, "Всем привет")

    def test_skips_empty(self):
        self.assertEqual(tp.prepare_chat("a", "   ", self.s).reason, "пустое сообщение")

    def test_skips_commands(self):
        self.assertEqual(tp.prepare_chat("a", "!sound 1", self.s).reason, "команда")
        self.assertEqual(tp.prepare_chat("a", "/me hi", self.s).reason, "команда")
        r = tp.prepare_chat("a", "!ура", replace(self.s, skip_commands=False))
        self.assertIsNotNone(r.phrase)

    def test_links_skipped_or_stripped(self):
        self.assertEqual(tp.prepare_chat("a", "заходи https://x.ru", self.s).reason, "ссылка")
        r = tp.prepare_chat("a", "заходи https://x.ru", replace(self.s, skip_links=False))
        self.assertEqual(r.phrase, "a: заходи")

    def test_only_emoji_has_nothing_to_read(self):
        self.assertEqual(tp.prepare_chat("a", "😍😍🔥", self.s).reason, "нет текста для чтения")

    def test_only_russian(self):
        s = replace(self.s, only_russian=True)
        self.assertEqual(tp.prepare_chat("a", "hello world", s).reason, "не по-русски")
        self.assertIsNotNone(tp.prepare_chat("a", "hello мир", s).phrase)

    def test_blacklist(self):
        s = replace(self.s, blacklist="казино, ставки")
        self.assertEqual(tp.prepare_chat("a", "Лучшее КАЗИНО тут", s).reason, "чёрный список")

    def test_repeats(self):
        tracker = tp.RecentTracker()
        self.assertIsNotNone(tp.prepare_chat("a", "Привет!", self.s, tracker).phrase)
        self.assertEqual(tp.prepare_chat("b", "привет", self.s, tracker).reason, "повтор")
        r = tp.prepare_chat("b", "привет", replace(self.s, ignore_repeats=False), tracker)
        self.assertIsNotNone(r.phrase)

    def test_followers_only(self):
        s = replace(self.s, followers_only=True)
        self.assertEqual(tp.prepare_chat("a", "привет", s, is_follower=False).reason,
                         "не подписчик")
        self.assertEqual(tp.prepare_chat("a", "привет", s).reason, "подписчик не определён")
        self.assertIsNotNone(tp.prepare_chat("a", "привет", s, is_follower=True).phrase)
        self.assertIsNotNone(tp.prepare_chat("a", "привет", self.s, is_follower=False).phrase)

    def test_truncates_to_max_len(self):
        s = replace(self.s, max_len=20, read_nick=False)
        r = tp.prepare_chat("a", "слово " * 20, s)
        self.assertLessEqual(len(r.phrase), 20)

    def test_gift_and_follow_phrases(self):
        self.assertEqual(tp.gift_phrase("Аня", "Rose", 1), "Аня, спасибо за подарок Rose")
        self.assertEqual(tp.gift_phrase("Аня", "Rose", 5), "Аня, спасибо за подарки Rose, 5 штук")
        self.assertEqual(tp.follow_phrase("Аня"), "Аня, спасибо за подписку")


if __name__ == "__main__":
    unittest.main()
