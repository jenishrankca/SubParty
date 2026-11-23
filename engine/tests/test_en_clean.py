"""EN postfilters: dedupe repeats, drop CJK echo / garbage, prefer bare."""

from __future__ import annotations

import unittest

from context_translate import pick_best_candidate
from en_clean import clean_en, dedupe_repeated_tokens, is_garbage_en, sanitize_translation


class TestEnClean(unittest.TestCase):
    def test_dedupe_runaway_words(self) -> None:
        raw = "I know I know I know I know"
        self.assertEqual(dedupe_repeated_tokens(raw), "I know")
        raw2 = "the the the the man"
        self.assertEqual(dedupe_repeated_tokens(raw2), "the man")

    def test_garbage_cjk_echo(self) -> None:
        zh = "你在干什么"
        self.assertTrue(is_garbage_en(zh, zh=zh))
        self.assertTrue(is_garbage_en("", zh=zh))
        self.assertTrue(is_garbage_en("你在干什么", zh=zh))
        self.assertFalse(is_garbage_en("What are you doing?", zh=zh))

    def test_garbage_repetition_ratio(self) -> None:
        bad = "go go go go go home"
        # After dedupe may become clean; flag raw as garbage if still triple
        self.assertTrue(is_garbage_en("yes yes yes yes yes", zh="好"))

    def test_clean_prefers_fallback(self) -> None:
        zh = "快走"
        contextual = "go go go go go go go"
        bare = "Go quickly."
        out = clean_en(contextual, zh=zh, fallback=bare)
        self.assertEqual(out, "Go quickly.")

    def test_sanitize_drops_echo(self) -> None:
        zh = "别动"
        info = sanitize_translation(zh, zh=zh, bare="Don't move.")
        self.assertEqual(info["text_en"], "Don't move.")
        self.assertTrue(info["was_garbage"])
        self.assertTrue(info["used_fallback"])

    def test_pick_prefers_bare_over_broken_contextual(self) -> None:
        zh = "他走了"
        bare = "He left."
        contextual = "He left He left He left He left."
        self.assertEqual(pick_best_candidate(bare, contextual, zh=zh), "He left.")

    def test_before_after_examples(self) -> None:
        """Document Live EN before/after for Jenish report."""
        cases = [
            ("我知道了", "I know I know I know I know", "I know"),
            ("别废话", "别废话", ""),  # echo → drop
            ("快跑", "run run run run run", "run"),
        ]
        for zh, before, expect in cases:
            after = clean_en(before, zh=zh)
            if expect:
                self.assertEqual(after, expect, msg=f"{zh!r}: {before!r} → {after!r}")
            else:
                self.assertEqual(after, expect)


if __name__ == "__main__":
    unittest.main()
