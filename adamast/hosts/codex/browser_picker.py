"""Codex facade for the shared localhost taxonomy picker transport."""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path
from typing import Any

from adamast.hosts.interactive.browser_picker import (
    allowed_option,
    open_browser_picker,
    picker_alive,
    picker_options,
    read_browser_choice,
    wait_for_browser_choice,
)
from adamast.hosts.interactive.browser_picker import (
    apply_browser_choice as _apply_browser_choice,
)
from adamast.hosts.interactive.browser_picker import (
    serve_picker as _serve_picker,
)
from adamast.hosts.interactive.browser_picker import (
    start_browser_picker as _start_browser_picker,
)

from .session_routes import create_fresh_session_route
from .state import load_state, save_state

PICKER_DIR = ".adamast-codex-picker"
WORKER_MODULE = "adamast.hosts.codex.browser_picker"

__all__ = [
    "apply_browser_choice",
    "allowed_option",
    "open_browser_picker",
    "picker_alive",
    "picker_options",
    "read_browser_choice",
    "serve_picker",
    "start_browser_picker",
    "wait_for_browser_choice",
]


def start_browser_picker(
    trace_output: Path,
    session_id: str,
    *,
    store_dir: Path,
    selection: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
    routing_root: Path | None = None,
    default_trace_output: Path | None = None,
    task_group: str = "default",
    project_scope: str = "explicit",
    project_id: str | None = None,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    return _start_browser_picker(
        trace_output,
        session_id,
        store_dir=store_dir,
        picker_dir=PICKER_DIR,
        worker_module=WORKER_MODULE,
        selection=selection,
        event=event,
        routing_root=routing_root,
        default_trace_output=default_trace_output,
        task_group=task_group,
        project_scope=project_scope,
        project_id=project_id,
        timeout_seconds=timeout_seconds,
    )


def apply_browser_choice(
    request: dict[str, Any],
    choice: str,
) -> dict[str, Any]:
    return _apply_browser_choice(
        request,
        choice,
        host_label="Codex",
        load_state=load_state,
        save_state=save_state,
        create_fresh_session_route=create_fresh_session_route,
    )


def serve_picker(
    *,
    store_dir: Path,
    ready_path: Path,
    result_path: Path,
    timeout_seconds: int,
    request_path: Path | None = None,
    open_browser: bool = True,
) -> int:
    return _serve_picker(
        store_dir=store_dir,
        ready_path=ready_path,
        result_path=result_path,
        timeout_seconds=timeout_seconds,
        request_path=request_path,
        open_browser=open_browser,
        apply_choice=apply_browser_choice,
        host_label="Codex",
        open_url=webbrowser.open,
    )


_picker_options = picker_options


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AdaMAST Codex browser picker")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--store-dir", required=True)
    parser.add_argument("--ready-path", required=True)
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--request-path")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--no-open-browser", action="store_true")
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    return serve_picker(
        store_dir=Path(args.store_dir),
        ready_path=Path(args.ready_path),
        result_path=Path(args.result_path),
        timeout_seconds=args.timeout_seconds,
        request_path=Path(args.request_path) if args.request_path else None,
        open_browser=not args.no_open_browser,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
