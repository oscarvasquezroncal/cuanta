from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-~+/]{12,}=*"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)
_ASSIGNMENT = re.compile(
    r"(?i)((?:api[_-]?key|secret|token|password|passwd|authorization|access[_-]?key)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}{]{6,})"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\(?:[^\\\s\"'<>|]+\\)*([^\\\s\"'<>|]*)")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:[^/\s\"'<>|]+/)+([^/\s\"'<>|]*)")

REDACTED = "[redacted]"
EMAIL_MARK = "[email]"


def redact_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _ASSIGNMENT.sub(lambda match: f"{match.group(1)}{REDACTED}", text)


def redact_emails(text: str) -> str:
    return _EMAIL.sub(EMAIL_MARK, text)


def trim_paths(text: str) -> str:
    text = _WINDOWS_PATH.sub(lambda match: f".../{match.group(1)}", text)
    return _POSIX_PATH.sub(lambda match: f".../{match.group(1)}", text)


def omit_tool_output(text: str) -> str:
    return f"ok, {len(text)} chars (omitted)"


def redact_for_storage(text: str) -> str:
    return redact_emails(redact_secrets(text))


def redact_for_remote(text: str) -> str:
    return trim_paths(redact_emails(redact_secrets(text)))
