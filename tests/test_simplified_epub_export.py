"""Tests for the cache-driven simplified EPUB exporter."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from bilingual_epub_tts import _load_chapters
from bilingual_epub_tts import _synthesize_vocabulary_appendix
from chinese_llm_preprocess import PROMPT_VERSION, _source_digest, _source_payload
from simplified_epub_export import _replace_chinese_paragraphs, export_simplified_epub


class ChineseParagraphReplacementTests(unittest.TestCase):
    """Verify XHTML replacement changes only safe Chinese paragraph bodies."""

    def test_replace_chinese_and_preserve_english_markup(self) -> None:
        """Replace Hanzi content while retaining the surrounding XHTML bytes."""
        source = (
            '<html><body><p class="cn">原文</p>'
            '<p class="en"><span>English text</span></p></body></html>'
        ).encode("utf-8")

        result = _replace_chinese_paragraphs(source, ["简化内容 & 保留"], 1)

        self.assertIn('<p class="cn">简化内容 &amp; 保留</p>'.encode("utf-8"), result)
        self.assertIn('<p class="en"><span>English text</span></p>'.encode("utf-8"), result)

    def test_reject_chinese_inline_markup(self) -> None:
        """Refuse to discard inline tags from a Chinese source paragraph."""
        source = '<html><body><p><em>中文</em>段落</p></body></html>'.encode("utf-8")

        with self.assertRaisesRegex(ValueError, "inline XHTML elements"):
            _replace_chinese_paragraphs(source, ["简化中文段落"], 1)

    def test_reject_replacement_count_mismatch(self) -> None:
        """Reject a cache with a different Chinese paragraph count."""
        source = '<html><body><p>第一段</p><p>第二段</p></body></html>'.encode("utf-8")

        with self.assertRaisesRegex(ValueError, "more Chinese paragraphs|alignment mismatch"):
            _replace_chinese_paragraphs(source, ["简化段落"], 1)


class SimplifiedEpubExportTests(unittest.TestCase):
    """Verify ZIP preservation and cache-driven replacement end to end."""

    def _make_source_epub(self, root: Path) -> Path:
        """Create a minimal paired Chinese/English EPUB fixture."""
        source = root / "source.epub"
        members = [
            (
                "mimetype",
                b"application/epub+zip",
                zipfile.ZIP_STORED,
            ),
            (
                "META-INF/container.xml",
                b"<container/>",
                zipfile.ZIP_DEFLATED,
            ),
            (
                "META-INF/calibre_bookmarks.txt",
                b"source reader position",
                zipfile.ZIP_DEFLATED,
            ),
            (
                "OEBPS/content.opf",
                b"<package><metadata><title>Fixture</title></metadata></package>",
                zipfile.ZIP_DEFLATED,
            ),
            (
                "OEBPS/Text/0001_1_Fixture_split_000.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第一章</h1></body></html>'.encode("utf-8"),
                zipfile.ZIP_DEFLATED,
            ),
            (
                "OEBPS/Text/0001_1_Fixture_split_001.xhtml",
                (
                    '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter 1: Fixture</h1>'
                    '<p class="cn">原始中文段落。</p><p class="en"><span>Original English paragraph.</span></p>'
                    '</body></html>'
                ).encode("utf-8"),
                zipfile.ZIP_DEFLATED,
            ),
            (
                "OEBPS/untouched.txt",
                b"Leave this package member unchanged.",
                zipfile.ZIP_DEFLATED,
            ),
        ]
        with zipfile.ZipFile(source, "w") as archive:
            archive.comment = b"fixture comment"
            for name, data, compression in members:
                archive.writestr(name, data, compress_type=compression)
        return source

    def test_export_preserves_package_and_source(self) -> None:
        """Export one cached chapter without changing the source or English text."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._make_source_epub(root)
            original_source = source.read_bytes()
            chapter = _load_chapters(source)[1]
            cache_dir = root / "cache"
            cache_chapter = cache_dir / "chapter_0001"
            cache_chapter.mkdir(parents=True)
            cache_record = {
                "chapter": 1,
                "source_sha256": _source_digest(_source_payload(chapter)),
                "prompt_version": PROMPT_VERSION,
                "model": "test-model",
                "result": {
                    "narration_paragraphs": ["简化后的中文段落。"],
                    "vocabulary": [
                        {"hanzi": "词语", "pinyin": "cí yǔ", "english": "term & meaning"}
                    ],
                },
            }
            (cache_chapter / "chinese_simplification.json").write_text(
                json.dumps(cache_record, ensure_ascii=False), encoding="utf-8"
            )
            output = root / "study.epub"

            export_simplified_epub(
                source,
                cache_dir,
                output,
                start=1,
                end=1,
                include_glossary=True,
            )

            self.assertEqual(source.read_bytes(), original_source)
            with zipfile.ZipFile(source) as original, zipfile.ZipFile(output) as exported:
                self.assertEqual(original.namelist(), exported.namelist())
                self.assertEqual(original.comment, exported.comment)
                self.assertEqual(original.read("mimetype"), exported.read("mimetype"))
                self.assertEqual(
                    original.getinfo("mimetype").compress_type,
                    exported.getinfo("mimetype").compress_type,
                )
                self.assertEqual(
                    original.read("OEBPS/content.opf"),
                    exported.read("OEBPS/content.opf"),
                )
                self.assertEqual(
                    original.read("OEBPS/untouched.txt"),
                    exported.read("OEBPS/untouched.txt"),
                )
                body = exported.read("OEBPS/Text/0001_1_Fixture_split_001.xhtml").decode("utf-8")
                self.assertIn("简化后的中文段落。", body)
                self.assertIn("<span>Original English paragraph.</span>", body)
                self.assertIn("Vocabulary 词汇表", body)
                self.assertIn("<em>cí yǔ</em>", body)
                self.assertIn("term &amp; meaning", body)
                self.assertNotIn("原始中文段落。", body)
                self.assertIsNone(exported.testzip())

    def test_refuse_to_overwrite_source(self) -> None:
        """Protect the source EPUB from being selected as the destination."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._make_source_epub(root)
            cache_dir = root / "cache"
            cache_dir.mkdir()

            with self.assertRaisesRegex(ValueError, "different file"):
                export_simplified_epub(source, cache_dir, source)

    def test_refuse_to_overwrite_existing_destination(self) -> None:
        """Keep an existing destination unchanged unless overwrite is enabled."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._make_source_epub(root)
            cache_dir = root / "cache"
            cache_dir.mkdir()
            output = root / "study.epub"
            output.write_bytes(b"existing output")

            with self.assertRaises(FileExistsError):
                export_simplified_epub(source, cache_dir, output)

            self.assertEqual(output.read_bytes(), b"existing output")

    def test_preserve_reader_bookmarks_when_overwriting(self) -> None:
        """Keep Calibre reading progress while refreshing an existing study EPUB."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._make_source_epub(root)
            chapter = _load_chapters(source)[1]
            cache_dir = root / "cache"
            cache_chapter = cache_dir / "chapter_0001"
            cache_chapter.mkdir(parents=True)
            cache_record = {
                "chapter": 1,
                "source_sha256": _source_digest(_source_payload(chapter)),
                "prompt_version": PROMPT_VERSION,
                "model": "test-model",
                "result": {
                    "narration_paragraphs": ["简化后的中文段落。"],
                    "vocabulary": [],
                },
            }
            (cache_chapter / "chinese_simplification.json").write_text(
                json.dumps(cache_record, ensure_ascii=False), encoding="utf-8"
            )
            output = root / "study.epub"
            bookmark = b"Reader's current Chapter 721 position"
            with zipfile.ZipFile(source) as original, zipfile.ZipFile(output, "w") as previous:
                for info in original.infolist():
                    data = original.read(info)
                    if info.filename == "META-INF/calibre_bookmarks.txt":
                        data = bookmark
                    previous.writestr(info, data)

            export_simplified_epub(source, cache_dir, output, start=1, end=1, overwrite=True)

            with zipfile.ZipFile(output) as study:
                self.assertEqual(study.read("META-INF/calibre_bookmarks.txt"), bookmark)


class VocabularyAudioAppendixTests(unittest.TestCase):
    """Verify the chapter vocabulary appendix speaks Hanzi and English only."""

    def test_synthesize_terms_and_meanings_without_pinyin(self) -> None:
        """Route Hanzi to Chinese G2P and English definitions to the English voice."""
        class FakeBackend:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str, float]] = []

            def synthesize_with_fallback(
                self, text: str, language: str, voice: str, speed: float
            ) -> tuple[np.ndarray, int]:
                self.calls.append((text, language, voice, speed))
                return np.full(100, 0.1, dtype=np.float32), 1000

        with tempfile.TemporaryDirectory() as temporary:
            backend = FakeBackend()
            audio, rate = _synthesize_vocabulary_appendix(
                Path(temporary),
                backend,
                {"zh": "zf_003", "en": "af_heart"},
                {"zh": 0.75, "en": 1.0},
                [{"hanzi": "玉简", "pinyin": "yù jiǎn", "english": "jade slip"}],
                600,
            )

        self.assertEqual(
            backend.calls,
            [
                ("玉简", "zh", "zf_003", 0.75),
                ("jade slip", "en", "af_heart", 1.0),
            ],
        )
        self.assertEqual(rate, 1000)
        self.assertEqual(len(audio), 450)  # 100 Hanzi + 250 ms gap + 100 English.
        self.assertTrue(np.isfinite(audio).all())


if __name__ == "__main__":
    unittest.main()
