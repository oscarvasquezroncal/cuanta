from __future__ import annotations

from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.messages import Message, english, msg

INDEX_REBUILD = "cuanta index --rebuild"
SIDECARS = ("-journal", "-wal", "-shm")
LEFTOVER_KINDS = ("rebuild-", "corrupt-", "new-")
BACKUP_KINDS = ("rebuild-", "corrupt-")
CARRIED_PROVENANCES = ("agent-note:", "economy-summary:")


def sidecars(database: str) -> tuple[str, ...]:
    return tuple(database + suffix for suffix in SIDECARS)


def rebuild_leftover(database: str, name: str) -> bool:
    return name in sidecars(database) or any(
        name.startswith(f"{database}.{kind}") for kind in LEFTOVER_KINDS
    )


def rebuild_backup(database: str, name: str) -> bool:
    return not name.endswith(SIDECARS) and any(
        name.startswith(f"{database}.{kind}") for kind in BACKUP_KINDS
    )


def carried(provenance: str) -> bool:
    return provenance.startswith(CARRIED_PROVENANCES)


class IndexRefused(EnvironmentFailure):
    def __init__(self, reason: Message, advice: Message) -> None:
        self.reason = reason
        self.advice = advice
        super().__init__(english(reason), english(advice))


class IndexBusy(IndexRefused):
    def __init__(self, path: str) -> None:
        super().__init__(msg("index.busy", path=path), msg("index.busy_fix", command=INDEX_REBUILD))


class IndexReadOnly(IndexRefused):
    def __init__(self, path: str) -> None:
        super().__init__(
            msg("index.read_only", path=path), msg("index.read_only_fix", command=INDEX_REBUILD)
        )


class IndexRecoveryBusy(IndexRefused):
    def __init__(self, path: str) -> None:
        super().__init__(msg("index.recovery_busy", path=path), msg("index.recovery_busy_fix"))
