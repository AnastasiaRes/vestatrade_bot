"""Reproducible, secret-free Stage 6 release manifest helpers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.agents.semantic_interpreter import (
    SEMANTIC_AUDIT_PROMPT_HASH,
    SEMANTIC_PROMPT_HASH,
    SEMANTIC_PROMPT_VERSION,
)
from app.answer_v2.renderer import RENDERER_PROMPT, RENDERER_PROMPT_VERSION


_PUBLIC_CUTOVER_FLAGS = frozenset(
    {
        "DIALOGUE_V2_ROUTING_ENABLED",
        "DIALOGUE_V2_SHADOW_COMPARE_ENABLED",
        "DIALOGUE_V2_LIVE_DELIVERY_ENABLED",
        "DIALOGUE_V2_INTERNAL_CANARY_ENABLED",
        "DIALOGUE_V2_INTERNAL_CANARY_PERCENT",
        "DIALOGUE_V2_LEGACY_DRY_RUN_COMPARE_ENABLED",
        "DIALOGUE_V2_FORCE_LEGACY",
    }
)


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(project_root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def source_tree_sha256(project_root: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        paths = sorted(
            path
            for folder in ("app", "scripts", "tests")
            for path in (project_root / folder).rglob("*.py")
            if path.is_file()
        )
        for path in paths:
            digest.update(str(path.relative_to(project_root)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return None
    return digest.hexdigest()


def json_collection_count(path: Path | None) -> int | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("products", "items", "offers"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
    return None


def build_release_manifest(
    project_root: Path,
    *,
    catalog_path: Path | None,
    feed100_path: Path | None,
    registry_revision: str,
    llm_provider: str,
    llm_model: str,
    feature_flags: dict[str, bool | int | str | None],
    catalog_source: str | None = None,
    catalog_count: int | None = None,
    testset_path: Path | None = None,
    generation_settings: dict[str, int | float | str | bool | None] | None = None,
    run_timestamp: str | None = None,
) -> dict[str, Any]:
    diff = _git(project_root, "diff", "--no-ext-diff", "HEAD", "--", "app", "tests")
    return {
        "schema_version": "1.0",
        "git_head": _git(project_root, "rev-parse", "HEAD"),
        "source_tree_sha256": source_tree_sha256(project_root),
        "source_dirty": bool(diff) if diff is not None else None,
        "source_diff_sha256": (
            hashlib.sha256((diff or "").encode("utf-8")).hexdigest()
            if diff is not None
            else None
        ),
        "catalog_sha256": file_sha256(catalog_path),
        "catalog_source": catalog_source,
        "catalog_count": (
            catalog_count
            if catalog_count is not None
            else json_collection_count(catalog_path)
        ),
        "feed100_sha256": file_sha256(feed100_path),
        "testset_sha256": file_sha256(testset_path),
        "schema_versions": {
            "semantic": "1.1",
            "dialogue_state": "2.0",
            "catalog_contract": "1.0",
            "commerce": "1.0",
            "answer_plan": "1.0",
            "cutover": "1.0",
        },
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "prompt_contracts": {
            "semantic_version": SEMANTIC_PROMPT_VERSION,
            "semantic_sha256": SEMANTIC_PROMPT_HASH,
            "semantic_audit_sha256": SEMANTIC_AUDIT_PROMPT_HASH,
            "renderer_version": RENDERER_PROMPT_VERSION,
            "renderer_sha256": hashlib.sha256(
                RENDERER_PROMPT.encode("utf-8")
            ).hexdigest(),
        },
        "generation_settings": dict(generation_settings or {}),
        "rollout_registry_revision": registry_revision,
        "run_timestamp": run_timestamp,
        # Manifests are intended for durable rollout evidence.  Persist only
        # the public cutover controls, never arbitrary environment settings.
        "feature_flags": {
            key: feature_flags[key]
            for key in sorted(_PUBLIC_CUTOVER_FLAGS.intersection(feature_flags))
        },
    }
