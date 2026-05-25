from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import typer


@dataclass(frozen=True)
class StatuslineRuntime:
    build_statusline_cli: Callable[
        ..., tuple[object, tuple[object, ...], dict[str, object], int]
    ]
    wrap_refresh_json_payload: Callable[..., object]
    statusline_install_instructions: Callable[[str], str]
    load_resolved_toktrail_config_or_exit: Callable[[typer.Context], object]
    render_statusline_quota_label: Callable[[object], str]
    statusline_config_with_override: Callable[[object, str, str], object]
    strip_statusline_sections: Callable[[str], str]
    render_statusline_config_sections: Callable[..., str]


_RUNTIME: StatuslineRuntime | None = None


def configure_statusline_runtime(runtime: StatuslineRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def _runtime() -> StatuslineRuntime:
    if _RUNTIME is None:
        msg = "statusline runtime is not configured"
        raise RuntimeError(msg)
    return _RUNTIME


def statusline_impl(
    ctx: typer.Context,
    *,
    json_output: bool,
    harness: str | None,
    provider_id: str | None,
    model_id: str | None,
    source_session_id: str | None,
    session: str | None,
    basis: str | None,
    refresh: str | None,
    no_refresh: bool,
    refresh_details: bool,
    raw: bool | None,
    max_width: int | None,
    stale_after: int | None,
) -> None:
    report, refresh_results, _payload, _elapsed_ms = _runtime().build_statusline_cli(
        ctx,
        harness=harness,
        provider_id=provider_id,
        model_id=model_id,
        source_session_id=source_session_id,
        session_mode=session,
        basis=basis,
        refresh=refresh,
        no_refresh=no_refresh,
        refresh_details=refresh_details,
        raw=raw,
        max_width=max_width,
        stale_after=stale_after,
    )
    if json_output:
        typer.echo(
            json.dumps(
                _runtime().wrap_refresh_json_payload(
                    report.as_dict(),
                    refresh_results=refresh_results,
                    include_refresh=refresh_details,
                ),
                indent=2,
            )
        )
        return
    typer.echo(report.line)


def statusline_test_impl(
    ctx: typer.Context,
    *,
    basis: str,
    refresh: str,
    no_refresh: bool,
    refresh_details: bool,
    raw: bool | None,
    max_width: int | None,
    stale_after: int | None,
    json_output: bool,
) -> None:
    report, refresh_results, payload, elapsed_ms = _runtime().build_statusline_cli(
        ctx,
        harness=None,
        provider_id=None,
        model_id=None,
        source_session_id=None,
        session_mode="auto",
        basis=basis,
        refresh=refresh,
        no_refresh=no_refresh,
        refresh_details=refresh_details,
        raw=raw,
        max_width=max_width,
        stale_after=stale_after,
    )
    if json_output:
        body = {
            "line": report.line,
            "elapsed_ms": elapsed_ms,
            "report": payload,
        }
        body = _runtime().wrap_refresh_json_payload(
            body,
            refresh_results=refresh_results,
            include_refresh=refresh_details,
        )
        typer.echo(json.dumps(body, indent=2))
        return
    typer.echo(f"Source: {report.source_session_id or '(today fallback)'}")
    typer.echo(f"Model: {(report.provider_id or '-')} / {(report.model_id or '-')}")
    typer.echo(f"Timing: {elapsed_ms}ms")
    typer.echo("Output cache: miss")
    typer.echo(
        "Refresh: "
        + ("none" if not refresh_results else f"{len(refresh_results)} source(s)")
    )
    typer.echo(f"Quota: {_runtime().render_statusline_quota_label(report)}")
    if payload is not None:
        typer.echo("Payload:")
        typer.echo(json.dumps(payload, indent=2))
    typer.echo("Line:")
    typer.echo(report.line)


def statusline_install_impl(target: str) -> None:
    typer.echo(_runtime().statusline_install_instructions(target))


def statusline_config_show_impl(ctx: typer.Context) -> None:
    loaded = _runtime().load_resolved_toktrail_config_or_exit(ctx)
    statusline_config = loaded.config.statusline
    typer.echo(f"config path:   {loaded.config_path}")
    typer.echo(f"config exists: {'yes' if loaded.config_exists else 'no'}")
    typer.echo(f"default harness: {statusline_config.default_harness}")
    typer.echo(f"basis:         {statusline_config.basis}")
    typer.echo(f"refresh:       {statusline_config.refresh}")
    typer.echo(f"session:       {statusline_config.session}")
    typer.echo(f"max width:     {statusline_config.max_width}")
    typer.echo(f"stale after:   {statusline_config.cache.stale_after_secs}")
    typer.echo("elements:      " + ", ".join(statusline_config.elements))
    typer.echo(f"context windows: {len(loaded.config.context_windows)}")


def statusline_config_set_impl(
    ctx: typer.Context,
    *,
    key: str,
    value: str,
) -> None:
    loaded = _runtime().load_resolved_toktrail_config_or_exit(ctx)
    updated = _runtime().statusline_config_with_override(
        loaded.config.statusline,
        key,
        value,
    )
    config_path: Path = loaded.config_path
    existing_text = (
        config_path.read_text(encoding="utf-8")
        if config_path.exists()
        else "config_version = 1\n"
    )
    stripped = _runtime().strip_statusline_sections(existing_text).strip()
    rendered = _runtime().render_statusline_config_sections(
        updated,
        context_windows=loaded.config.context_windows,
    )
    output = stripped
    if output:
        output += "\n\n"
    output += rendered + "\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(output, encoding="utf-8")
    typer.echo(f"Updated statusline config: {config_path}")
