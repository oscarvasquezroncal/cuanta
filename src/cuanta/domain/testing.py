from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

SUMMARY_MAX_LINES = 12


class GatewayStatus(StrEnum):
    GREEN = "green"
    RED = "red"
    PERSISTENT = "persistent_failure"


@dataclass(frozen=True, slots=True)
class Frame:
    path: str
    line: int


@dataclass(frozen=True, slots=True)
class TestFailure:
    test: str
    error_type: str
    message: str
    frames: tuple[Frame, ...] = ()


@dataclass(frozen=True, slots=True)
class TestOutcome:
    passed: int
    failed: int
    errored: int
    skipped: int
    duration_s: float
    failures: tuple[TestFailure, ...]
    exit_code: int = 0

    @property
    def red(self) -> bool:
        return self.failed + self.errored > 0 or bool(self.failures)


@dataclass(frozen=True, slots=True)
class Signature:
    id: str
    error: str
    verbatim: str
    tests: int
    first: str
    location: str


_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
_TEMP = re.compile(
    r"(?:[A-Za-z]:)?[\\/](?:tmp|temp|var[\\/]folders|private[\\/]var[\\/]folders|"
    r"Users[\\/][^\\/\s]+[\\/]AppData[\\/]Local[\\/]Temp)[\\/][^\s'\"():,]*",
    re.IGNORECASE,
)
_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/)(?:[^\s'\"():,\\/]+[\\/])+([^\s'\"():,\\/]+)")
_LINE_REF = re.compile(r"(?::\d+)+\b|\bline \d+\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SPACES = re.compile(r"\s+")


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def normalize_message(message: str) -> str:
    text = first_line(message)
    text = _UUID.sub("<uuid>", text)
    text = _HEX.sub("<hex>", text)
    text = _TEMP.sub("<tmp>", text)
    text = _ABS_PATH.sub(lambda match: match.group(1), text)
    text = _LINE_REF.sub("", text)
    text = _NUMBER.sub("<n>", text)
    return _SPACES.sub(" ", text).strip()


def signature_key(error_type: str, message: str) -> str:
    return f"{error_type.strip() or 'Error'}: {normalize_message(message)}"


def signature_id(error_type: str, message: str) -> str:
    return hashlib.sha1(signature_key(error_type, message).encode("utf-8")).hexdigest()[:12]


EXTERNAL_MARKERS = (
    "site-packages",
    "dist-packages",
    "node_modules",
    "vendor/",
    "vendor\\",
    "/lib/python",
    "\\lib\\python",
    "<frozen",
    "<string>",
    "node:internal",
    "internal/",
    "/usr/lib/",
    "/usr/local/go/src",
    "/rustc/",
    ".cargo/registry",
)


def _relative(path: str, root: str) -> str:
    normalized = path.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/") + "/"
    if base != "/" and normalized.lower().startswith(base.lower()):
        return normalized[len(base) :]
    return normalized


def is_project_frame(path: str, root: str) -> bool:
    normalized = path.replace("\\", "/")
    lowered = normalized.lower()
    if any(marker.replace("\\", "/").lower() in lowered for marker in EXTERNAL_MARKERS):
        return False
    absolute = normalized.startswith("/") or bool(re.match(r"^[A-Za-z]:/", normalized))
    if not absolute:
        return True
    base = root.replace("\\", "/").rstrip("/").lower()
    return bool(base) and lowered.startswith(base + "/")


def representative_location(frames: Sequence[Frame], root: str) -> str:
    for frame in frames:
        if is_project_frame(frame.path, root):
            relative = _relative(frame.path, root)
            return f"{relative}:{frame.line}" if frame.line else relative
    return ""


def cluster(failures: Iterable[TestFailure], root: str) -> tuple[Signature, ...]:
    groups: dict[str, list[TestFailure]] = {}
    for failure in failures:
        groups.setdefault(signature_id(failure.error_type, failure.message), []).append(failure)
    signatures: list[Signature] = []
    for key, members in groups.items():
        head = members[0]
        location = next(
            (
                found
                for found in (representative_location(member.frames, root) for member in members)
                if found
            ),
            "",
        )
        verbatim = first_line(head.message)
        prefix = (
            f"{head.error_type}: " if head.error_type and head.error_type not in verbatim else ""
        )
        signatures.append(
            Signature(
                id=key,
                error=signature_key(head.error_type, head.message),
                verbatim=f"{prefix}{verbatim}".strip(),
                tests=len(members),
                first=head.test,
                location=location,
            )
        )
    signatures.sort(key=lambda signature: (-signature.tests, signature.first))
    return tuple(signatures)


def persistent_signatures(previous: Iterable[str], current: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(previous) & set(current)))


def gateway_status(outcome: TestOutcome, persistent: Sequence[str]) -> GatewayStatus:
    if persistent:
        return GatewayStatus.PERSISTENT
    return GatewayStatus.RED if outcome.red else GatewayStatus.GREEN


def summary(outcome: TestOutcome, signatures: Sequence[Signature], runner: str) -> tuple[str, ...]:
    lines = [
        f"{runner}: {outcome.passed} passed, {outcome.failed} failed, {outcome.errored} errors, "
        f"{outcome.skipped} skipped in {outcome.duration_s:.2f}s"
    ]
    for signature in signatures:
        if len(lines) >= SUMMARY_MAX_LINES - 1 and len(signatures) > SUMMARY_MAX_LINES - 1:
            remaining = len(signatures) - (len(lines) - 1)
            lines.append(f"... {remaining} more signatures")
            break
        where = f" @ {signature.location}" if signature.location else ""
        lines.append(f"[{signature.id}] x{signature.tests} {signature.verbatim[:160]}{where}")
    return tuple(lines[:SUMMARY_MAX_LINES])


_PY_FRAME = re.compile(r'File "([^"]+)", line (\d+)')
_COLON_FRAME = re.compile(r"((?:[A-Za-z]:)?[^\s:()\"']+\.[A-Za-z]{1,5}):(\d+)(?::\d+)?")


def frames_from_text(text: str) -> tuple[Frame, ...]:
    frames: list[Frame] = []
    for line in text.splitlines():
        python = _PY_FRAME.search(line)
        if python:
            frames.append(Frame(python.group(1), int(python.group(2))))
            continue
        for match in _COLON_FRAME.finditer(line):
            frames.append(Frame(match.group(1), int(match.group(2))))
    return tuple(frames)


_ERROR_PREFIX = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Failure|Warning|Exit|Interrupt))\b:?\s*(.*)$"
)


def split_error(text: str, fallback_type: str = "Error") -> tuple[str, str]:
    line = first_line(text)
    match = _ERROR_PREFIX.match(line)
    if match:
        return match.group(1).rsplit(".", 1)[-1], match.group(2) or line
    return fallback_type, line
