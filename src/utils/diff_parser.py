"""Unified diff parser for inspecting changed files, hunk headers, and valid inline comment lines."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class ParsedHunk(BaseModel):
    """Represents a single diff hunk block @@ -old_start,old_count +new_start,new_count @@."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[str] = Field(default_factory=list)
    valid_new_lines: set[int] = Field(default_factory=set)


class DiffFile(BaseModel):
    """Represents a modified file in a unified diff."""

    old_path: str | None = None
    new_path: str
    is_new: bool = False
    is_deleted: bool = False
    hunks: list[ParsedHunk] = Field(default_factory=list)
    valid_commentable_lines: set[int] = Field(default_factory=set)


class DiffParser:
    """Parses standard Git unified diffs into structured file and line metadata.

    Allows the agent and tool layer to validate whether a targeted line number
    actually exists inside the modified hunk of the PR.
    """

    HUNK_HEADER_REGEX = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$")

    @classmethod
    def parse(cls, unified_diff: str) -> dict[str, DiffFile]:
        """Parse a full unified diff text into a mapping of file_path -> DiffFile."""
        files: dict[str, DiffFile] = {}
        current_file: DiffFile | None = None
        current_hunk: ParsedHunk | None = None
        current_new_line = 0

        lines = unified_diff.splitlines()
        for line in lines:
            if line.startswith("diff --git "):
                # Save previous hunk/file
                if current_hunk and current_file:
                    current_file.hunks.append(current_hunk)
                    current_file.valid_commentable_lines.update(current_hunk.valid_new_lines)
                    current_hunk = None

                match = re.match(r"^diff --git a/(.+) b/(.+)$", line)
                if match:
                    old_path, new_path = match.group(1), match.group(2)
                    current_file = DiffFile(old_path=old_path, new_path=new_path)
                    files[new_path] = current_file
                else:
                    current_file = None
                continue

            if not current_file:
                continue

            if line.startswith("new file mode"):
                current_file.is_new = True
                continue
            elif line.startswith("deleted file mode"):
                current_file.is_deleted = True
                continue

            hunk_match = cls.HUNK_HEADER_REGEX.match(line)
            if hunk_match:
                if current_hunk:
                    current_file.hunks.append(current_hunk)
                    current_file.valid_commentable_lines.update(current_hunk.valid_new_lines)

                old_start = int(hunk_match.group(1))
                old_count = int(hunk_match.group(2) or "1")
                new_start = int(hunk_match.group(3))
                new_count = int(hunk_match.group(4) or "1")

                current_hunk = ParsedHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    header=line,
                )
                current_new_line = new_start
                continue

            if current_hunk:
                current_hunk.lines.append(line)
                if line.startswith("+"):
                    current_hunk.valid_new_lines.add(current_new_line)
                    current_new_line += 1
                elif line.startswith("-"):
                    # Deleted lines do not advance new line count
                    pass
                elif line.startswith(" ") or line == "":
                    # Context line inside the hunk
                    current_hunk.valid_new_lines.add(current_new_line)
                    current_new_line += 1

        if current_hunk and current_file:
            current_file.hunks.append(current_hunk)
            current_file.valid_commentable_lines.update(current_hunk.valid_new_lines)

        return files

    @classmethod
    def get_commentable_lines(cls, unified_diff: str, file_path: str) -> set[int]:
        """Return the set of line numbers in the new file that are inside diff hunks."""
        files = cls.parse(unified_diff)
        if file_path in files:
            return files[file_path].valid_commentable_lines
        return set()
