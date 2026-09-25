from __future__ import annotations

from typing import Any

from cuanta.domain.detection import Detection


def detection_payload(detection: Detection) -> dict[str, Any]:
    stack = detection.stack
    return {
        "root": detection.root,
        "project": detection.project_name,
        "stack": {
            "language": stack.language,
            "language_version": stack.language_version,
            "framework": stack.framework,
            "framework_version": stack.framework_version,
            "package_manager": stack.package_manager,
            "test_runner": stack.test_runner,
            "entry_points": list(stack.entry_points),
            "manifests": list(stack.manifests),
            "verified": stack.verified,
            "test_command": stack.test_command,
            "typecheck_command": stack.typecheck_command,
            "build_command": stack.build_command,
        },
        "file_count": detection.file_count,
        "size_tier": detection.size_tier.value,
        "docs_state": detection.docs_state.value,
        "forge_state": detection.forge_state.value,
        "verify_tier": detection.verify_tier.value,
        "verify_evidence": detection.verify.evidence,
        "graph_mode": detection.graph_mode.value,
        "graph_evidence": detection.graph_evidence,
        "vcs": detection.vcs,
        "engines": [
            {"name": engine.name, "version": engine.version, "path": engine.path}
            for engine in detection.engines
        ],
        "run": detection.run_note,
        "telemetry": detection.telemetry_note,
    }
