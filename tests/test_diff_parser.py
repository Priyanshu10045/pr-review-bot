"""Unit tests for Unified Diff Parser."""

from __future__ import annotations

from src.utils.diff_parser import DiffParser


class TestDiffParser:
    """Test suite for unified diff parsing and commentable line detection."""

    SAMPLE_DIFF = """diff --git a/src/calc.py b/src/calc.py
index 1234567..89abcdef 100644
--- a/src/calc.py
+++ b/src/calc.py
@@ -1,5 +1,7 @@
 def add(a, b):
-    return a - b
+    return a + b
+
+def multiply(a, b):
+    return a * b
"""

    def test_parse_diff_files(self):
        parsed = DiffParser.parse(self.SAMPLE_DIFF)
        assert "src/calc.py" in parsed
        diff_file = parsed["src/calc.py"]
        assert diff_file.old_path == "src/calc.py"
        assert len(diff_file.hunks) == 1
        assert 2 in diff_file.valid_commentable_lines
        assert 4 in diff_file.valid_commentable_lines

    def test_get_commentable_lines(self):
        lines = DiffParser.get_commentable_lines(self.SAMPLE_DIFF, "src/calc.py")
        assert 2 in lines
        assert 5 in lines
        # Line outside diff hunk should not be in commentable lines
        assert 100 not in lines

    def test_empty_diff(self):
        parsed = DiffParser.parse("")
        assert parsed == {}
        lines = DiffParser.get_commentable_lines("", "any_file.py")
        assert lines == set()
