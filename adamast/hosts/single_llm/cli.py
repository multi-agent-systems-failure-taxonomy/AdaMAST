"""CLI for the single-model, no-harness AdaMAST integration."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from adamast.core.config import (
    add_config_argument,
    bool_config_value,
    config_value,
    load_adamast_config,
    require_config_value,
)
from adamast.llm.models import is_bedrock_model
from adamast.core import resolver, store
from adamast.dashboard import webview

from .runtime import SingleLLMConfig, run_single_llm


def provider_call(model: str):
    if is_bedrock_model(model) and not os.environ.get("OPENAI_BASE_URL"):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "Bedrock models with AWS_BEARER_TOKEN_BEDROCK require "
                "`pip install adamast[bedrock]`"
            ) from exc
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not region:
            raise RuntimeError(
                "Bedrock models require AWS_REGION or AWS_DEFAULT_REGION"
            )
        client = boto3.client("bedrock-runtime", region_name=region)
        model_id = model.split("/", 1)[1] if model.lower().startswith("bedrock/") else model

        def call(messages):
            system = [
                {"text": item["content"]}
                for item in messages
                if item["role"] == "system"
            ]
            turns = [
                {
                    "role": item["role"],
                    "content": [{"text": item["content"]}],
                }
                for item in messages
                if item["role"] in {"user", "assistant"}
            ]
            kwargs = {
                "modelId": model_id,
                "messages": turns,
                "inferenceConfig": {"maxTokens": 8192},
            }
            if system:
                kwargs["system"] = system
            response = client.converse(**kwargs)
            content = response["output"]["message"]["content"]
            return "".join(block.get("text", "") for block in content)

        return call

    if model.startswith(("claude", "anthropic")):
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "Anthropic models require `pip install adamast[anthropic]`"
            ) from exc
        client = Anthropic()

        def call(messages):
            system = "\n\n".join(
                item["content"] for item in messages if item["role"] == "system"
            )
            turns = [
                item for item in messages if item["role"] in {"user", "assistant"}
            ]
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system,
                messages=turns,
            )
            return "".join(
                block.text for block in response.content if hasattr(block, "text")
            )

        return call

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI-compatible models require `pip install adamast`"
        ) from exc
    client = OpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        api_key=os.environ.get("OPENAI_API_KEY") or None,
    )

    def call(messages):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return response.choices[0].message.content or ""

    return call


def main(argv=None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="Run one LLM agent task through AdaMAST without a harness."
    )
    add_config_argument(parser)
    parser.add_argument("--task")
    parser.add_argument("--task-file")
    parser.add_argument("--model")
    parser.add_argument("--adamast-model")
    parser.add_argument("--trace-output")
    parser.add_argument("--store-dir")
    parser.add_argument("--trace-root")
    parser.add_argument(
        "--inherit",
        nargs="?",
        const=resolver.NO_ID,
        help=(
            "taxonomy ID to inherit; the no-value picker form is deprecated, "
            "use --inherit-pick instead"
        ),
    )
    parser.add_argument(
        "--inherit-pick",
        action="store_true",
        help="open the local taxonomy picker before running the task",
    )
    parser.add_argument("--problem-id")
    parser.add_argument(
        "--gate-exhaustion-policy",
        choices=("raise", "release"),
    )
    parser.add_argument("--recent-activity-messages", type=int)
    parser.add_argument("--recent-activity-chars", type=int)
    parser.add_argument(
        "--freeze",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="record traces/evidence but skip generation and refinement",
    )
    parser.add_argument(
        "--evidence-export",
        type=Path,
        help="optional external evidence export path or directory sink",
    )
    parser.add_argument("--dashboard", dest="dashboard", action="store_true", default=None)
    parser.add_argument("--no-dashboard", dest="dashboard", action="store_false")
    args = parser.parse_args(argv)
    try:
        config = load_adamast_config(args.config)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if bool(args.task) == bool(args.task_file):
        parser.error("provide exactly one of --task or --task-file")
    try:
        model = str(require_config_value(args, config, "model", "--model"))
        trace_output = Path(
            require_config_value(args, config, "trace_output", "--trace-output")
        ).resolve()
    except ValueError as exc:
        parser.error(str(exc))
    if args.inherit_pick and args.inherit is not None:
        parser.error("--inherit-pick cannot be combined with --inherit")
    if args.task is not None:
        task = args.task
    else:
        try:
            task = Path(args.task_file).read_text(encoding="utf-8-sig")
        except OSError as exc:
            parser.error(f"cannot read --task-file {args.task_file!r}: {exc}")
    store_dir = config_value(args, config, "store_dir", store.DEFAULT_STORE_DIR)
    inherit = (
        resolver.NO_ID
        if args.inherit_pick
        else args.inherit if args.inherit is not None else config.get("inherit")
    )
    if inherit == resolver.NO_ID:
        if not args.inherit_pick:
            print(
                "warning: bare --inherit is deprecated; use --inherit-pick "
                "for the interactive picker.",
                file=sys.stderr,
            )
        selected = resolver.resolve(
            resolver.NO_ID,
            store_dir=store_dir,
            launcher=webview.run_webview,
        )
        inherit = None if selected == resolver.NONE else selected
    fields = {
        "trace_output": trace_output,
        "adamast_model": config_value(args, config, "adamast_model", model),
        "inherit": inherit,
        "dashboard": bool_config_value(args, config, "dashboard", True),
        "max_retries": config_value(args, config, "max_retries", 3),
        "format_retries": config_value(args, config, "format_retries", 2),
        "repair_rounds": config_value(args, config, "repair_rounds"),
        "generation_threshold": config_value(
            args, config, "generation_threshold", 5
        ),
        "k_init": config_value(args, config, "k_init", 10),
        "k": config_value(args, config, "k", 20),
        "generation_stops": bool_config_value(
            args, config, "generation_stops", False
        ),
        "skip_judge": bool_config_value(args, config, "skip_judge", False),
        "refinement_stops": bool_config_value(
            args, config, "refinement_stops", False
        ),
        "advanced_refinement": bool_config_value(
            args, config, "advanced_refinement", False
        ),
        "repo": config_value(args, config, "repo"),
        "repo_path": config_value(args, config, "repo_path"),
        "gate_exhaustion_policy": config_value(
            args, config, "gate_exhaustion_policy", "raise"
        ),
        "recent_activity_messages": config_value(
            args, config, "recent_activity_messages", 8
        ),
        "recent_activity_chars": config_value(
            args, config, "recent_activity_chars", 12000
        ),
        "freeze": bool_config_value(args, config, "freeze", False),
        "redact_traces": bool_config_value(args, config, "redact_traces", True),
        "evidence_export": (
            Path(config_value(args, config, "evidence_export"))
            if config_value(args, config, "evidence_export")
            else None
        ),
    }
    if store_dir:
        fields["store_dir"] = Path(store_dir).resolve()
    trace_root = config_value(args, config, "trace_root")
    if trace_root:
        fields["trace_root"] = Path(trace_root).resolve()
    try:
        result = run_single_llm(
            task,
            provider_call(model),
            SingleLLMConfig(**fields),
            problem_id=args.problem_id,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(result.answer)
    return 0


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
