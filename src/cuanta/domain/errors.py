from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    DOMAIN_FAILURE = 1
    ENVIRONMENT = 2
    NOT_AVAILABLE = 3
    INTERRUPTED = 130


class CuantaError(Exception):
    exit_code: ExitCode = ExitCode.ENVIRONMENT

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class DomainFailure(CuantaError):
    exit_code = ExitCode.DOMAIN_FAILURE


class EnvironmentFailure(CuantaError):
    exit_code = ExitCode.ENVIRONMENT


class NotAvailable(CuantaError):
    exit_code = ExitCode.NOT_AVAILABLE
