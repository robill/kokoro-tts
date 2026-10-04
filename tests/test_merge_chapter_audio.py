"""Unit tests for numbered chapter audio collection and ffconcat quoting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from merge_chapter_audio import _concat_quote, collect_chapter_mp3s


class ChapterAudioCollectionTests(unittest.TestCase):
    """Verify strict numeric ordering and complete-range checks."""

    def test_collect_in_numeric_order(self) -> None:
        """Return chapter MP3s in number order, not lexical folder order."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for number in reversed(range(1, 11)):
                chapter = root / f"chapter_{number:04d}"
                chapter.mkdir()
                (chapter / f"chapter_{number:04d}.mp3").write_bytes(b"audio")

            paths = collect_chapter_mp3s(root, 1, 10)

        self.assertEqual([path.parent.name for path in paths], [f"chapter_{i:04d}" for i in range(1, 11)])

    def test_reject_missing_chapter(self) -> None:
        """Fail rather than silently omit a missing chapter MP3."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chapter = root / "chapter_0001"
            chapter.mkdir()
            (chapter / "chapter_0001.mp3").write_bytes(b"audio")

            with self.assertRaisesRegex(FileNotFoundError, "\[2\]"):
                collect_chapter_mp3s(root, 1, 2)

    def test_escape_concat_file_quotes(self) -> None:
        """Escape single quotes in paths for ffmpeg concat input syntax."""
        path = Path("C:/Audio/O'Brien/chapter_0001.mp3")

        self.assertEqual(_concat_quote(path), "'C:/Audio/O'\\''Brien/chapter_0001.mp3'")

    def test_reject_invalid_range(self) -> None:
        """Reject reversed chapter ranges."""
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "less than or equal"):
                collect_chapter_mp3s(Path(temporary), 4, 3)


if __name__ == "__main__":
    unittest.main()
