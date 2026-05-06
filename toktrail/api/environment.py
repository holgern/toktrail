from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from pathlib import Path

from toktrail.api._common import _get_harness
from toktrail.api.models import HarnessEnvironment
from toktrail.api.paths import resolve_source_path
from toktrail.errors import InvalidAPIUsageError
from toktrail.paths import new_copilot_otel_file_path


def prepare_environment(
    harness: str,
    *,
    source_path: Path | None = None,
    base_env: Mapping[str, str] | None = None,
    shell: str = "bash",
) -> HarnessEnvironment:
    _ = base_env
    definition = _get_harness(harness)
    if definition.name == "copilot":
        path = (
            source_path.expanduser()
            if source_path is not None
            else new_copilot_otel_file_path().expanduser()
        )
        env = {
            "COPILOT_OTEL_ENABLED": "true",
            "COPILOT_OTEL_EXPORTER_TYPE": "file",
            "COPILOT_OTEL_FILE_EXPORTER_PATH": str(path),
            "TOKTRAIL_COPILOT_FILE": str(path),
        }
        return HarnessEnvironment(
            harness=definition.name,
            source_path=path,
            env=env,
            shell_exports=_render_shell_exports(shell, env),
            instructions=(
                "Apply these environment variables before starting GitHub Copilot CLI.",
            ),
        )

    resolved = resolve_source_path(definition.name, source_path)
    return HarnessEnvironment(
        harness=definition.name,
        source_path=resolved,
        env={},
        shell_exports=(),
        instructions=(),
    )


def _render_shell_exports(shell: str, env: Mapping[str, str]) -> tuple[str, ...]:
    normalized = shell.lower()
    items = tuple(env.items())
    if normalized in {"bash", "zsh"}:
        return tuple(f"export {key}={shlex.quote(value)}" for key, value in items)
    if normalized == "fish":
        return tuple(f"set -gx {key} {_quote_fish(value)}" for key, value in items)
    if normalized in {"nu", "nushell"}:
        return tuple(f"$env.{key} = {json.dumps(value)}" for key, value in items)
    if normalized in {"powershell", "pwsh"}:
        return tuple(f"$env:{key} = {_quote_powershell(value)}" for key, value in items)
    msg = "Unsupported shell. Use bash, zsh, fish, nu, or powershell."
    raise InvalidAPIUsageError(msg)


def _quote_fish(value: str) -> str:
    return "'" + value.replace("'", "\\'") + "'"


def _quote_powershell(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


__all__ = ["prepare_environment"]
