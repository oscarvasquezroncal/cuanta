from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cuanta.application.mandate_flow import MandateOptions, per_role_run
from dev.acceptance import verify
from dev.results import ROOT, Report, python
from dev.spec import Spec, Trial, load


def payload(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig")
    decoder = json.JSONDecoder()
    offset = 0
    values: list[dict[str, Any]] = []
    while offset < len(raw):
        while offset < len(raw) and raw[offset].isspace():
            offset += 1
        if offset == len(raw):
            break
        value, offset = decoder.raw_decode(raw, offset)
        if isinstance(value, dict):
            values.append(value)
    if not values:
        raise ValueError("No JSON payload")
    return values[-1]


def cli(spec: Spec, *arguments: str) -> list[str]:
    return python("-m", "cuanta", "--json", "--project", str(spec.project), *arguments)


def display(arguments: list[str], windows: bool | None = None) -> str:
    use_windows = os.name == "nt" if windows is None else windows
    return subprocess.list2cmdline(arguments) if use_windows else shlex.join(arguments)


def per_role(trial: Trial) -> bool:
    options = MandateOptions(engine=trial.engine, simple=trial.simple, shape=trial.shape)
    return trial.cross_engine or per_role_run(options, trial.type, trial.engine)


def command(spec: Spec, trial: Trial) -> list[str]:
    arguments = [
        "mandate",
        "--sandbox",
        "--type",
        trial.type,
        "--what",
        trial.what,
        "--why",
        trial.why,
        "--tests",
        trial.tests,
        "--out-of-scope",
        trial.out_of_scope,
        "--depth",
        trial.depth,
        "--engine",
        trial.engine,
        "--max-budget-usd",
        str(trial.cap),
    ]
    if trial.cross_engine:
        arguments.append("--cross-engine")
    if per_role(trial):
        arguments.extend(["--cross-budget-usd", str(trial.cap)])
    if trial.simple:
        arguments.append("--simple")
    for value in trial.role_models:
        serialized = value
        role, _, reference = value.partition("=")
        engine, qualified, model = reference.partition(":")
        if not trial.cross_engine and qualified:
            if engine != trial.engine:
                raise ValueError("Native role model engine must match the trial engine")
            serialized = f"{role}={model}"
        arguments.extend(["--role-model", serialized])
    if trial.model:
        arguments.extend(["--model", trial.model])
    if trial.shape:
        arguments.extend(["--shape", trial.shape])
    if trial.mode == "classic":
        arguments.append("--classic")
    return cli(spec, *arguments)


def identifier(value: dict[str, Any]) -> str:
    sandbox = value.get("sandbox") or {}
    trial = sandbox.get("trial") or {}
    steps = value.get("steps") or []
    result = (
        trial.get("run_id") or value.get("run_id") or (steps[0].get("run_id") if steps else None)
    )
    if not isinstance(result, str) or not result:
        raise ValueError("Run ID unavailable; stop before another trial")
    return result


def cost(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Cost unknown; stop before another trial")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Invalid cost; stop before another trial")
    return result


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def tokens_of(totals: Any) -> dict[str, Any]:
    values = mapping(totals)
    output = values.get("output")
    reasoning = values.get("reasoning")
    return {
        "fresh_tokens": values.get("fresh_input"),
        "cache_read_tokens": values.get("cache_read"),
        "cache_write_tokens": values.get("cache_write"),
        "output_tokens": (
            output + (reasoning if isinstance(reasoning, int) else 0)
            if isinstance(output, int)
            else None
        ),
    }


def metrics_row(report: Report, spec: Spec, trial: Trial, run_id: str) -> dict[str, Any]:
    step = report.run(trial.name + "-metrics", cli(spec, "costs", "--metrics"))
    if step.code:
        raise ValueError("Costs metrics unavailable")
    rows = payload(Path(step.log)).get("metrics")
    if not isinstance(rows, list):
        raise ValueError("Costs metrics missing")
    for item in rows:
        if isinstance(item, dict) and item.get("run_id") == run_id:
            return item
    raise ValueError(f"No costs metrics row for {run_id}")


def r3_fields(run: dict[str, Any], totals: Any, metrics: dict[str, Any]) -> dict[str, Any]:
    forecast = mapping(metrics.get("forecast"))
    blocked = mapping(mapping(run.get("governor")).get("blocked")) or mapping(
        metrics.get("blocked")
    )
    return {
        **tokens_of(totals),
        "first_request_fixed_tokens": mapping(run.get("overhead")).get("fixed_context_tokens"),
        "reads_blocked": blocked.get("reads"),
        "blocked_tokens_estimate": blocked.get("tokens_estimate"),
        "forecast_p50_usd": forecast.get("p50_usd"),
        "forecast_p90_usd": forecast.get("p90_usd"),
        "forecast_actual_usd": metrics.get("actual_usd"),
        "p90_minus_actual_usd": metrics.get("p90_minus_actual_usd"),
        "outcome": run.get("outcome") or metrics.get("outcome"),
    }


def collect(report: Report, spec: Spec, trial: Trial, launch: dict[str, Any]) -> dict[str, Any]:
    run_id = identifier(launch)
    duration = report.steps[-1].seconds
    show = report.run(trial.name + "-show", cli(spec, "runs", "show", run_id))
    run = launch if show.code else payload(Path(show.log))
    roles = per_role(trial)
    if roles and any(step.get("cost_usd") is None for step in launch.get("steps", [])):
        raise ValueError("Cross-engine role cost unknown; stop before another trial")
    actual = launch.get("spent_usd") if roles else run.get("actual_usd")
    if actual is None and not roles:
        actual = run.get("cost_usd")
    actual = cost(actual)
    sources = [step.get("cost_source") for step in launch.get("steps", [])]
    source = run.get("cost_source") if not sources else sorted({str(item) for item in sources})
    row: dict[str, Any] = {
        "name": trial.name,
        "run_id": run_id,
        "cap_usd": trial.cap,
        "cost_usd": actual,
        "cost_source": source,
        "estimate": run.get("estimate"),
        "estimate_factor": run.get("estimate_factor"),
        "duration_s": duration,
        "turns": run.get("turns"),
        "max_turns": run.get("max_turns"),
        "end_reason": run.get("end_reason"),
        "first_request": run.get("overhead"),
        "steps": launch.get("steps", []),
        "mode": trial.mode,
        "provider": trial.engine,
    }
    if show.code:
        row["metrics_error"] = "Run metrics unavailable"
        return row
    spectrum = report.run(trial.name + "-spectrum", cli(spec, "spectrum", run_id))
    try:
        if spectrum.code:
            raise ValueError("Spectrum metrics unavailable")
        tokens = payload(Path(spectrum.log))
        row["tokens"] = tokens.get("totals")
        row["spectrum"] = tokens
    except (OSError, ValueError) as error:
        row["metrics_error"] = str(error)
        return row
    metrics: dict[str, Any] = {}
    try:
        metrics = metrics_row(report, spec, trial, run_id)
    except (OSError, ValueError) as error:
        row["r3_error"] = str(error)
    recorded = metrics.get("mode")
    if recorded is not None and recorded != trial.mode:
        row["metrics_error"] = f"Run recorded mode {recorded}; the trial asked for {trial.mode}"
    row.update(r3_fields(run, row["tokens"], metrics))
    return row


def save(
    report: Report,
    rows: list[dict[str, Any]],
    spec: Spec,
    error: str = "",
    unknown: list[str] | None = None,
) -> None:
    total = sum(row["cost_usd"] for row in rows)
    summary = {
        "trials": rows,
        "known_spend_usd": total,
        "total_cap_usd": spec.total_cap,
        "error": error,
        "unknown_spend_trials": unknown or [],
    }
    (report.directory / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    lines = [
        "| Trial | Run | Cost | Source | Cap | Acceptance checks |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for row in rows:
        accepted = row.get("acceptance")
        status = "skipped" if accepted is None else "passed" if accepted["passed"] else "failed"
        lines.append(
            f"| {row['name']} | {row['run_id']} | ${row['cost_usd']:.4f} | "
            f"{row['cost_source']} | ${row['cap_usd']:.2f} | {status} |"
        )
    (report.directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(spec: Spec, dry_run: bool, only: str | None, skip_accept: bool) -> int:
    trials = [trial for trial in spec.trials if only is None or trial.name == only]
    if not trials:
        raise ValueError("No trial matches --only")
    if dry_run:
        for trial in trials:
            print(f"{trial.name}: ${trial.cap:.2f}; {display(command(spec, trial))}")
            if trial.recovery_note:
                print(f"  Recovery: {trial.recovery_note}")
        print(
            f"Caps: ${sum(trial.cap for trial in trials):.2f} / ${spec.total_cap:.2f}; spend $0.00"
        )
        return 0
    if not spec.project.is_dir():
        raise ValueError("Project directory does not exist")
    report = Report("trial", ROOT)
    rows: list[dict[str, Any]] = []
    error = ""
    unknown: list[str] = []
    try:
        for trial in trials:
            remaining = spec.total_cap - sum(row["cost_usd"] for row in rows)
            if remaining + 1e-9 < trial.cap:
                raise ValueError(f"Refusing {trial.name}: ${remaining:.4f} remains")
            launch = report.run(trial.name + "-mandate", command(spec, trial), timeout=3600)
            unknown.append(trial.name)
            row = collect(report, spec, trial, payload(Path(launch.log)))
            rows.append(row)
            unknown.remove(trial.name)
            save(report, rows, spec)
            print(
                f"{trial.name}: ${row['cost_usd']:.4f} ({row['cost_source']}); cap ${trial.cap:.2f}"
            )
            for decision in ("accept", "reject"):
                print(
                    display(
                        ["cuanta", "--project", str(spec.project), "runs", decision, row["run_id"]]
                    )
                )
            if row["cost_usd"] > trial.cap + 1e-9:
                raise ValueError(f"{trial.name} exceeded its cap; stop")
            if row.get("metrics_error"):
                raise ValueError(row["metrics_error"])
            if not skip_accept:
                row["acceptance"] = verify(report, trial, spec.project, row["run_id"])
                row["accepted"] = row["acceptance"]["passed"]
            save(report, rows, spec)
            if launch.code:
                raise ValueError(f"{trial.name} mandate failed; metrics saved")
    except (OSError, ValueError, KeyError, TypeError) as failure:
        error = str(failure)
    save(report, rows, spec, error, unknown)
    label = "Known spend" if unknown else "Spend"
    print(f"{label}: ${sum(row['cost_usd'] for row in rows):.4f} / ${spec.total_cap:.2f}")
    if unknown:
        print(f"Spend unavailable: {', '.join(unknown)}; no further trial started")
    if error:
        print(f"fail trial: {error}")
    print(f"Logs: {report.directory}")
    return 1 if error else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run capped sandbox trials from a private TOML spec."
    )
    parser.add_argument("spec", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--skip-accept", action="store_true")
    options = parser.parse_args()
    try:
        return run(load(options.spec), options.dry_run, options.only, options.skip_accept)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"fail trial spec: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
