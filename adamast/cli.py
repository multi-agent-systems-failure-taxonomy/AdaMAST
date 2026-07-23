"""Single ``adamast`` entry point dispatching to the per-surface CLIs.

Every historical ``adamast-*`` console script keeps working; this command
is a thin, lazily-importing router over the same ``main(argv)`` functions,
so ``adamast doctor --json`` and ``adamast-doctor --json`` are identical.
It also matches the public package's interface, which ships one ``adamast``
command.
"""

from __future__ import annotations

import sys
from importlib import import_module, metadata

# command -> (module path, attribute, one-line description)
_COMMANDS: dict[str, tuple[str, str, str]] = {
    "generate": (
        "adamast.foundation_cli",
        "main_generate",
        "generate an agreement-gated taxonomy from trace files",
    ),
    "judge": (
        "adamast.foundation_cli",
        "main_judge",
        "apply an existing taxonomy to one or more traces",
    ),
    "validate": (
        "adamast.foundation_cli",
        "main_validate",
        "validate accepted trace formats",
    ),
    "normalize": (
        "adamast.foundation_cli",
        "main_normalize",
        "write canonical AdaMAST JSONL from any accepted format",
    ),
    "view": (
        "adamast.foundation_cli",
        "main_view",
        "open one taxonomy as a read-only browser field guide",
    ),
    "doctor": (
        "adamast.doctor",
        "main",
        "check installation, storage, model, and host integrations",
    ),
    "dashboard": (
        "adamast.dashboard.server",
        "main",
        "live local view of recorded checkpoints and fired codes",
    ),
    "status": (
        "adamast.dashboard.status",
        "main",
        "summarize one trace-output program directory",
    ),
    "traces": (
        "adamast.core.traces_cli",
        "main",
        "inspect, export, and conservatively prune trace files",
    ),
    "find": (
        "adamast.core.finding_cli",
        "main",
        "list and resolve stored taxonomies",
    ),
    "register-taxonomy": (
        "adamast.learning.register_taxonomy",
        "main",
        "register a taxonomy file into the local store",
    ),
    "import-traces": (
        "adamast.learning.import_generation",
        "main",
        "generate a taxonomy from existing trace files",
    ),
    "single-run": (
        "adamast.hosts.single_llm.cli",
        "main",
        "run one no-harness single-model task under AdaMAST",
    ),
}

_HOST_COMMANDS: dict[str, dict[str, tuple[str, str, str]]] = {
    "claude": {
        "install": (
            "adamast.hosts.claude_code.install",
            "main",
            "install the Claude Code hooks",
        ),
        "uninstall": (
            "adamast.hosts.claude_code.uninstall",
            "main",
            "remove the Claude Code hooks",
        ),
        "checkpoint": (
            "adamast.hosts.claude_code.checkpoint",
            "main",
            "record a compact checkpoint outside chat",
        ),
        "add-hook": (
            "adamast.hosts.claude_code.manage_hooks",
            "add_main",
            "add a custom hook",
        ),
        "remove-hook": (
            "adamast.hosts.claude_code.manage_hooks",
            "remove_main",
            "remove a custom hook",
        ),
        "list-hooks": (
            "adamast.hosts.claude_code.manage_hooks",
            "list_main",
            "list installed hooks",
        ),
    },
    "codex": {
        "install": (
            "adamast.hosts.codex.install",
            "main",
            "install the Codex hooks",
        ),
        "uninstall": (
            "adamast.hosts.codex.uninstall",
            "main",
            "remove the Codex hooks",
        ),
        "checkpoint": (
            "adamast.hosts.codex.checkpoint",
            "main",
            "record a compact checkpoint outside chat",
        ),
    },
}


def _version() -> str:
    try:
        return metadata.version("adamast")
    except metadata.PackageNotFoundError:  # pragma: no cover
        return "unknown"


def _overview() -> str:
    lines = [
        "usage: adamast <command> [args]",
        "",
        "AdaMAST: adaptive failure-mode taxonomies for agents.",
        "",
        "setup and everyday commands:",
    ]
    for name, (_, _, blurb) in _COMMANDS.items():
        lines.append(f"  adamast {name:<22} {blurb}")
    for host, commands in _HOST_COMMANDS.items():
        lines.append("")
        lines.append(f"{host} host commands:")
        for name, (_, _, blurb) in commands.items():
            lines.append(f"  adamast {host} {name:<15} {blurb}")
    lines += [
        "",
        "Run `adamast <command> --help` for that command's options.",
        "The historical `adamast-<command>` scripts remain available.",
    ]
    return "\n".join(lines)


def _dispatch(module_path: str, attribute: str, prog: str, rest: list[str]) -> int:
    target = getattr(import_module(module_path), attribute)
    previous = sys.argv
    # Keep argparse usage/error text on the umbrella spelling.
    sys.argv = [prog, *rest]
    try:
        return int(target(rest) or 0)
    finally:
        sys.argv = previous


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(_overview())
        return 0
    if args[0] in ("-V", "--version", "version"):
        print(f"adamast {_version()}")
        return 0

    head, rest = args[0], args[1:]
    if head in _HOST_COMMANDS:
        host_commands = _HOST_COMMANDS[head]
        if not rest or rest[0] in ("-h", "--help", "help"):
            print(f"usage: adamast {head} <command> [args]\n")
            for name, (_, _, blurb) in host_commands.items():
                print(f"  adamast {head} {name:<15} {blurb}")
            return 0 if rest else 2
        sub, sub_rest = rest[0], rest[1:]
        if sub not in host_commands:
            known = ", ".join(host_commands)
            print(
                f"adamast {head}: unknown command {sub!r} (known: {known})",
                file=sys.stderr,
            )
            return 2
        module_path, attribute, _ = host_commands[sub]
        return _dispatch(module_path, attribute, f"adamast {head} {sub}", sub_rest)

    if head in _COMMANDS:
        module_path, attribute, _ = _COMMANDS[head]
        return _dispatch(module_path, attribute, f"adamast {head}", rest)

    known = ", ".join([*_COMMANDS, *_HOST_COMMANDS])
    print(f"adamast: unknown command {head!r} (known: {known})", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
