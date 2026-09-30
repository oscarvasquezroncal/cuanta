from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Reservation:
    identifier: str
    group: str
    trial: str
    cap: float


class TrialBudget:
    def __init__(self, path: Path, group: str, cap: float) -> None:
        self.path = path
        self.group = group
        self.cap = cap
        self.lock = path.with_suffix(path.suffix + ".lock")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "groups": {}}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("Unsupported trial budget journal")
        return value

    def _group(self, value: dict[str, Any]) -> dict[str, Any]:
        groups = value.get("groups")
        if not isinstance(groups, dict):
            raise ValueError("Invalid trial budget groups")
        group = groups.setdefault(self.group, {"cap_usd": self.cap, "attempts": []})
        if not isinstance(group, dict) or group.get("cap_usd") != self.cap:
            raise ValueError("Trial budget group cap changed; reconcile the journal first")
        if not isinstance(group.get("attempts"), list):
            raise ValueError("Invalid trial budget attempts")
        return group

    def _save(self, value: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def reserve(self, trial: str, cap: float) -> Reservation:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.lock.open("x", encoding="utf-8")
        except FileExistsError as error:
            raise ValueError(
                "Another trial holds the budget lock; reconcile before retrying"
            ) from error
        try:
            with handle:
                value = self._read()
                group = self._group(value)
                attempts = group["attempts"]
                spent = 0.0
                for attempt in attempts:
                    actual = attempt.get("cost_usd")
                    if attempt.get("state") == "recorded":
                        raise ValueError("An earlier trial is awaiting acceptance or cleanup; stop")
                    if attempt.get("state") != "settled" or not isinstance(actual, int | float):
                        raise ValueError(
                            "An earlier trial has unknown spend; reconcile before retrying"
                        )
                    if not math.isfinite(actual) or actual < 0:
                        raise ValueError("Invalid recorded trial spend")
                    spent += actual
                if spent + cap > self.cap + 1e-9:
                    raise ValueError(f"Refusing {trial}: ${self.cap - spent:.4f} remains")
                reservation = Reservation(uuid4().hex, self.group, trial, cap)
                attempts.append(
                    {
                        "id": reservation.identifier,
                        "trial": trial,
                        "reserved_usd": cap,
                        "cost_usd": None,
                        "state": "reserved",
                    }
                )
                self._save(value)
                return reservation
        finally:
            self.lock.unlink(missing_ok=True)

    def settle(self, reservation: Reservation, actual: float, run_id: str) -> None:
        if not math.isfinite(actual) or actual < 0:
            raise ValueError("Invalid actual trial spend")
        handle = self.lock.open("x", encoding="utf-8")
        try:
            with handle:
                value = self._read()
                group = self._group(value)
                attempt = next(
                    item for item in group["attempts"] if item["id"] == reservation.identifier
                )
                if attempt.get("state") != "reserved":
                    raise ValueError("Trial reservation was already settled")
                attempt.update({"cost_usd": actual, "state": "recorded", "run_id": run_id})
                self._save(value)
        finally:
            self.lock.unlink(missing_ok=True)

    def complete(self, reservation: Reservation) -> None:
        handle = self.lock.open("x", encoding="utf-8")
        try:
            with handle:
                value = self._read()
                group = self._group(value)
                attempt = next(
                    item for item in group["attempts"] if item["id"] == reservation.identifier
                )
                if attempt.get("state") != "recorded":
                    raise ValueError("Trial cost must be recorded before completing cleanup")
                attempt["state"] = "settled"
                self._save(value)
        finally:
            self.lock.unlink(missing_ok=True)
