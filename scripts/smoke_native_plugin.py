"""Exercise the native plugin's real clean-install path.

CI runs this on Windows, macOS, and Linux for both supported hosts.  The smoke
test intentionally starts without an AdaMAST runtime in plugin data, installs
the just-built wheel, dispatches SessionStart, and checks the native config.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=("claude", "codex"), required=True)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    plugin_root = args.plugin_root.resolve()
    wheel = _wheel(args.wheel_dir.resolve())
    with tempfile.TemporaryDirectory(prefix=f"adamast-{args.host}-plugin-") as raw:
        root = Path(raw).resolve()
        plugin_data = root / "plugin data"
        config = root / args.host / "adamast.json"
        traces = root / "native traces"
        env = os.environ.copy()
        env["ADAMAST_PACKAGE_SPEC"] = str(wheel)
        env["ADAMAST_CONFIG"] = str(config)
        env["ADAMAST_TRACE_OUTPUT"] = str(traces)
        if args.host == "claude":
            env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
            env.pop("PLUGIN_DATA", None)
        else:
            env["PLUGIN_DATA"] = str(plugin_data)
            env.pop("CLAUDE_PLUGIN_DATA", None)

        _seed_stale_lock(plugin_data, host=args.host)
        command = _launcher(plugin_root, args.host)
        payload = {
            "cwd": str(plugin_root.parents[1]),
            "hook_event_name": "SessionStart",
            "transport_probe": "AdaMAST café Δ",
            (
                "session_id" if args.host == "claude" else "thread_id"
            ): f"{args.host}-native-plugin-smoke",
        }
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"hook exited {completed.returncode}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )

        python = _managed_python(plugin_data)
        version = subprocess.check_output(
            [
                str(python),
                "-c",
                (
                    "import importlib.metadata; "
                    "print(importlib.metadata.version('adamast'))"
                ),
            ],
            text=True,
            encoding="utf-8",
        ).strip()
        document = json.loads(config.read_text(encoding="utf-8"))
        adapter = "claude_code" if args.host == "claude" else "codex"
        backend = f"{args.host}_subagent"
        if args.host == "claude":
            backend = "claude_subagent"
        assert version == "0.2.2.1"
        assert Path(document["trace_output"]) == traces
        assert document[adapter]["project_scope"] == "auto"
        assert document[adapter]["session_selector"] == "prompt"
        assert document[adapter]["selector_surface"] == "browser"
        assert document[adapter]["learning_backend"] == backend
        print(
            json.dumps(
                {
                    "host": args.host,
                    "platform": sys.platform,
                    "runtime": version,
                    "config": str(config),
                    "trace_output": document["trace_output"],
                }
            )
        )
    return 0


def _wheel(directory: Path) -> Path:
    wheels = sorted(directory.glob("adamast-0.2.2.1-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"expected one AdaMAST 0.2.2.1 wheel in {directory}, found {wheels}"
        )
    return wheels[0]


def _launcher(plugin_root: Path, host: str) -> list[str]:
    if os.name == "nt" and host == "codex":
        launcher = plugin_root / "bin" / "adamast-hook.cmd"
        return [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            "call",
            str(launcher),
            host,
            "SessionStart",
        ]
    shell = _posix_shell()
    return [
        shell,
        str(plugin_root / "bin" / "adamast-hook"),
        "--host",
        host,
        "--event",
        "SessionStart",
    ]


def _posix_shell() -> str:
    if os.name != "nt":
        return shutil.which("sh") or "sh"
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        git_bash = Path(program_files) / "Git" / "bin" / "bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    shell = shutil.which("bash")
    if shell:
        return shell
    raise RuntimeError("Claude Code's Git-Bash runtime was not found")


def _managed_python(plugin_data: Path) -> Path:
    candidates = (
        plugin_data / "uv-tools" / "adamast" / "Scripts" / "python.exe",
        plugin_data / "uv-tools" / "adamast" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"managed AdaMAST interpreter not found under {plugin_data}")


def _seed_stale_lock(plugin_data: Path, *, host: str) -> None:
    """Prove an interrupted first install cannot brick later sessions."""
    state = plugin_data / "state"
    state.mkdir(parents=True)
    lock = state / "bootstrap.lock"
    impossible_pid = "2147483647"
    if os.name == "nt" and host == "codex":
        lock.write_text(impossible_pid, encoding="utf-8")
    else:
        lock.mkdir()
        (lock / "pid").write_text(impossible_pid, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
