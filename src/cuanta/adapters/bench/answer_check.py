from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from cuanta.domain.answer_check import AnswerSpec
from cuanta.domain.report import FileRef, file_refs


class AnswerCheck:
    def check(self, spec: AnswerSpec, report: str | None, root: str) -> tuple[bool, str]:
        if not spec.keywords or not spec.citations:
            return False, "Answer check requires keywords and citation patterns"
        if report is None or not report.strip():
            return False, "Delivered answer unavailable or empty"
        missing = [
            word for word in spec.keywords if not word or word.casefold() not in report.casefold()
        ]
        if missing:
            return False, "Missing answer keywords: " + ", ".join(missing)
        normalized = report.replace("\\", "/")
        if re.search(r"(?<!\w)[A-Za-z]:[^\s`]*\.[A-Za-z][\w]{0,7}:\d", normalized):
            return False, "Answer citations must use sandbox-relative paths"
        if re.search(r"(?<![\w./])/[\S`]*\.[A-Za-z][\w]{0,7}:\d", normalized):
            return False, "Answer citations must use sandbox-relative paths"
        refs = file_refs(normalized)
        labels = {ref.label for ref in refs}
        for pattern in spec.citations:
            if not pattern:
                return False, "Empty citation pattern"
            try:
                matches = tuple(re.finditer(pattern, normalized))
            except re.error:
                return False, "Invalid citation pattern: " + pattern
            if not matches:
                return False, "Missing answer citation: " + pattern
            for match in matches:
                matched = file_refs(match.group())
                if not matched or any(ref.label not in labels for ref in matched):
                    return False, "Citation pattern must match a complete file:line reference"
        if not refs:
            return False, "Delivered answer has no file:line references"
        try:
            folder = Path(root).resolve(strict=True)
            if not folder.is_dir():
                return False, "Answer source root is not a directory"
            for ref in refs:
                error = self._reference(ref, folder)
                if error:
                    return False, error
            for match in re.finditer(
                r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z][\w]{0,7}):(\d{1,6})-(\d+)(?!\d)",
                normalized,
            ):
                start, end = int(match[2]), int(match[3])
                if end < start:
                    return False, "Answer citation range is reversed"
                error = self._reference(FileRef(match[1], end), folder)
                if error:
                    return False, error
        except (OSError, RuntimeError, UnicodeError):
            return False, "Answer citation source is unavailable"
        return True, "Answer keywords and source citations verified"

    def _reference(self, ref: FileRef, root: Path) -> str:
        relative = PurePosixPath(ref.path)
        if relative.is_absolute() or ".." in relative.parts or "\x00" in ref.path:
            return "Answer citation escapes the sandbox: " + ref.label
        path = (root / ref.path).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            return "Answer citation is not a sandbox file: " + ref.label
        lines = path.read_text(encoding="utf-8").splitlines()
        if not 1 <= ref.line <= len(lines):
            return "Answer citation line is out of range: " + ref.label
        return ""
