"""Tests for English-first English/original/A2 chapter triplets."""

from __future__ import annotations

import unittest

import numpy as np
from bs4 import BeautifulSoup

from english_first_chapter import _append_triplet_audio, _replace_paragraph_sequence


class EnglishFirstTripletTests(unittest.TestCase):
    """Verify paragraph-group alignment and English-first output ordering."""

    def test_grouped_english_precedes_original_and_a2(self) -> None:
        """Place multiple translation paragraphs before one source/A2 pair."""
        xhtml = (
            '<html><body><p class="en"><span>Translation one.</span></p>'
            '<p class="zh">原文一。</p><p class="en">Translation two.</p>'
            '<p class="en">Translation three.</p><p class="zh">原文二。</p>'
            '</body></html>'
        ).encode("utf-8")

        rewritten, records = _replace_paragraph_sequence(
            xhtml,
            ["Translation one.", "Translation two.", "Translation three."],
            ["原文一。", "原文二。"],
            ["简化一。", "简化二。"],
            [
                {"english_paragraphs": [1], "chinese_paragraphs": [1]},
                {"english_paragraphs": [2, 3], "chinese_paragraphs": [2]},
            ],
            [],
            745,
        )
        soup = BeautifulSoup(rewritten, "html.parser")
        paragraphs = [" ".join(p.get_text(" ", strip=True).split()) for p in soup.find_all("p")]

        self.assertEqual(
            paragraphs,
            [
                "Translation one.",
                "原文： 原文一。",
                "A2改写： 简化一。",
                "Translation two.",
                "Translation three.",
                "原文： 原文二。",
                "A2改写： 简化二。",
            ],
        )
        self.assertIn("原文：", paragraphs[1])
        self.assertIn("A2改写：", paragraphs[2])
        self.assertEqual(records[1]["english_paragraphs"], [2, 3])

    def test_reject_incomplete_english_alignment(self) -> None:
        """Require every English paragraph to be assigned exactly once."""
        xhtml = '<html><body><p>English.</p><p>中文。</p></body></html>'.encode("utf-8")
        with self.assertRaisesRegex(ValueError, "once in source order"):
            _replace_paragraph_sequence(
                xhtml,
                ["English."],
                ["中文。"],
                ["简体。"],
                [{"english_paragraphs": [1, 1], "chinese_paragraphs": [1]}],
                [],
                745,
            )

    def test_translation_notice_precedes_untranslated_original_and_a2(self) -> None:
        """Keep the source/A2 passage and label its incomplete English coverage."""
        xhtml = '<html><body><p>Partial English.</p><p>中文一。</p><p>中文二。</p></body></html>'.encode("utf-8")
        notice = "The English source omits part of this passage."
        rewritten, records = _replace_paragraph_sequence(
            xhtml,
            ["Partial English."],
            ["中文一。", "中文二。"],
            ["简体一。", "简体二。"],
            [{
                "english_paragraphs": [1],
                "chinese_paragraphs": [1, 2],
                "translation_notice": notice,
            }],
            [],
            751,
        )
        paragraphs = [
            " ".join(p.get_text(" ", strip=True).split())
            for p in BeautifulSoup(rewritten, "html.parser").find_all("p")
        ]
        self.assertEqual(paragraphs, [
            "Partial English.",
            "Translation note: " + notice,
            "原文： 中文一。",
            "A2改写： 简体一。",
            "原文： 中文二。",
            "A2改写： 简体二。",
        ])
        self.assertEqual(records[0]["translation_notice"], notice)

    def test_append_audio_with_a2_cue_between_chinese_versions(self) -> None:
        """Place the spoken simplification cue after original Chinese and before A2."""
        output: list[np.ndarray] = []
        original = np.full(10, 0.1, dtype=np.float32)
        cue = np.full(5, 0.2, dtype=np.float32)
        a2 = [np.full(8, 0.3, dtype=np.float32)]

        _append_triplet_audio(output, original, cue, a2, sample_rate=100)
        merged = np.concatenate(output)

        self.assertTrue(np.allclose(merged[:10], original))
        self.assertTrue(np.allclose(merged[35:40], cue))
        self.assertTrue(np.allclose(merged[65:73], a2[0]))
        self.assertEqual(len(merged), 98)  # 10 + 25ms silence + 5 + 25ms + 8 + 25ms.


if __name__ == "__main__":
    unittest.main()
