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

    def test_rename_and_deletion_parsing(self):
        diff_text = """diff --git a/old_name.py b/new_name.py
similarity index 95%
rename from old_name.py
rename to new_name.py
@@ -1,3 +1,3 @@
 def test():
-    return 1
+    return 2
diff --git a/deleted.py b/deleted.py
deleted file mode 100644
@@ -1,2 +0,0 @@
-def gone():
-    pass
"""
        parsed = DiffParser.parse(diff_text)
        assert "new_name.py" in parsed
        assert parsed["new_name.py"].is_renamed is True
        assert parsed["new_name.py"].old_path == "old_name.py"
        assert "deleted.py" in parsed
        assert parsed["deleted.py"].is_deleted is True

    def test_is_line_in_diff_and_get_changed_files(self):
        diff_text = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -10,4 +10,5 @@
  def run():
+     print("start")
      pass
\\ No newline at end of file
"""
        files = DiffParser.get_changed_files(diff_text)
        assert "src/app.py" in files
        assert DiffParser.is_line_in_diff(diff_text, "src/app.py", 11) is True
        assert DiffParser.is_line_in_diff(diff_text, "src/app.py", 999) is False
        assert DiffParser.is_line_in_diff(diff_text, "unknown.py", 11) is False
