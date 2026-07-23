"""Regression coverage for corrected learning-model boundaries."""

import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from adamast.learning.generation import run_generation_job
from adamast.llm.learning_calls import (
    ANTHROPIC_OPENAI_MAX_TOKENS,
    GEMINI_MAX_OUTPUT_TOKENS,
    build_refinement_prompt,
    format_refinement_traces,
    format_support_trace,
    refine_json,
    refinement_model_call,
    support_model_call,
)
from adamast.core.program import ProgramWorkspace
from adamast.core.traces import GenerationTrace
from adamast.core import store


def _code(category: str) -> dict:
    if category == "A":
        return {
            "code": "A.1",
            "name": "Execution_Crash",
            "definition": "Execution terminates unexpectedly before usable output.",
            "when_to_use": "Use when execution visibly crashes.",
            "when_not_to_use": "Do not use for a reasoning-only error.",
            "detection_heuristics": ["The trace contains an explicit crash report."],
            "severity": "major",
            "evidence": "observed",
        }
    if category == "B":
        return {
            "code": "B.1",
            "name": "Solver_Incomplete_Reasoning",
            "definition": "The solver leaves a required reasoning step unsupported.",
            "when_to_use": "Use when a solver omits a necessary justification.",
            "when_not_to_use": "Do not use for infrastructure failures.",
            "detection_heuristics": ["A conclusion appears without its required premise."],
            "severity": "major",
            "applies_to_role": "solver",
        }
    return {
        "code": "C.1",
        "name": "Invalid_Boundary_Reasoning",
        "definition": "The reasoning applies an invalid inclusive boundary.",
        "when_to_use": "Use when an inclusive limit is handled as exclusive.",
        "when_not_to_use": "Do not use when the boundary reasoning is valid.",
        "detection_heuristics": ["The trace drops the endpoint from an inclusive range."],
        "severity": "major",
    }


class FakeVendoredModel:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt, system=""):
        self.prompts.append(prompt)
        if "Analyze these system traces to understand the DOMAIN" in prompt:
            return json.dumps(
                {
                    "domain": {
                        "name": "Code Repair",
                        "content_type": "code patches",
                        "task_complexity": "repairing concrete defects",
                    },
                    "subdomains": [],
                    "domain_terminology": [],
                    "common_error_patterns": [],
                    "correctness_criteria": [],
                }
            )
        if "Classify these agents into functional roles" in prompt:
            return json.dumps(
                {
                    "agent_roles": {
                        "Agent_Solver": {
                            "role": "solver",
                            "definition": "Produces the candidate repair.",
                            "purpose": "Produce a correct repair",
                        }
                    }
                }
            )
        if "extract TRACE FORMAT and ARCHITECTURE" in prompt:
            return json.dumps(
                {
                    "trace_format": {
                        "agent_markers": ["Agent_Solver"],
                        "key_fields": [
                            {
                                "field_name": "raw_trajectory",
                                "description": "Agent execution text",
                                "location": "trace body",
                            }
                        ],
                        "output_structure": "plain text",
                        "example_patterns": ["Agent_Solver"],
                    },
                    "architecture": {
                        "topology": "single-agent",
                        "topology_details": "One solver performs the repair.",
                        "verification_pattern": "self-verify",
                        "verification_details": "The solver runs tests.",
                        "termination_owner": "solver",
                        "critical_handoffs": [],
                    },
                    "agent_role_corrections": {},
                }
            )
        if "Deduplicate these Category" in prompt:
            category = (
                "A" if "Category A" in prompt
                else "B" if "Category B" in prompt
                else "C"
            )
            return json.dumps(
                {
                    "kept_codes": [
                        {
                            "name": _code(category)["name"],
                            "definition": _code(category)["definition"],
                        }
                    ],
                    "removed": [],
                }
            )
        if "Review codes across all three categories" in prompt:
            return '{"duplicates_found":[]}'
        if "Validate codes against strict category rules" in prompt:
            return '{"violations_fixed":[]}'
        if "for semantic overlaps" in prompt:
            return '{"overlaps":[]}'
        if "Fix Category A codes" in prompt:
            return json.dumps({"codes": [_code("A")], "changes_made": []})
        if "Fix Category B codes" in prompt:
            return json.dumps({"codes": [_code("B")], "changes_made": []})
        if "Fix Category C" in prompt:
            return json.dumps({"codes": [_code("C")]})
        if "generating Category A codes" in prompt:
            return json.dumps({"codes": [_code("A")]})
        if "generating Category B codes" in prompt:
            return json.dumps({"codes": [_code("B")]})
        if "generating Category C codes" in prompt:
            return json.dumps({"codes": [_code("C")]})
        return "{}"


class LearningCallTests(unittest.TestCase):
    def test_support_trace_samples_late_failure_and_final_evidence_within_cap(self):
        text = (
            "START " + "a" * 15000
            + " PostToolUseFailure concrete tool failure "
            + "b" * 15000
            + " AdaMAST reflection: instruction template "
            + "b" * 5000
            + " AdaMAST reflection: mapped C.2 with evidence "
            + "c" * 15000
            + " Final AdaMAST status: instruction template "
            + "c" * 5000
            + " Final AdaMAST status: READY_TO_SUBMIT "
            + "d" * 15000
            + " TRACE FINISH"
        )
        excerpt = format_support_trace({"raw_trajectory": text}, cap=12000)
        self.assertLessEqual(len(excerpt), 12000)
        self.assertIn("START", excerpt)
        self.assertIn("PostToolUseFailure concrete tool failure", excerpt)
        self.assertIn("AdaMAST reflection: mapped C.2 with evidence", excerpt)
        self.assertIn("Final AdaMAST status: READY_TO_SUBMIT", excerpt)
        self.assertNotIn("AdaMAST reflection: instruction template", excerpt)
        self.assertIn("TRACE FINISH", excerpt)

    def test_support_trace_preserves_short_trace(self):
        self.assertEqual(
            format_support_trace({"raw_trajectory": "short trace"}, cap=12000),
            "short trace",
        )

    def test_no_external_adamast_or_old_tree_imports(self):
        root = Path(__file__).resolve().parent.parent
        offenders = []
        old_paths = []
        for path in root.rglob("*.py"):
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if (
                "olympiad" + "-agents" in text
                or "adamast" + "-branch" in text
            ):
                old_paths.append(str(path.relative_to(root)))
            tree = ast.parse(text, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module == "atlas" or (
                        node.module and node.module.startswith("atlas.")
                    ):
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for name in node.names:
                        if name.name == "atlas" or name.name.startswith("atlas."):
                            offenders.append(
                                f"{path.relative_to(root)}:{node.lineno}"
                            )
        self.assertEqual(offenders, [])
        self.assertEqual(old_paths, [])

    def test_no_dead_model_dependencies(self):
        root = Path(__file__).resolve().parent.parent
        forbidden = (
            "durable_store",
            "task_type",
            "resolve_bucket",
            "facet_buckets",
            "bucket_dir",
        )
        offenders = []
        for directory in ("adamast", "finding", "vendor"):
            for path in (root / directory).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for term in forbidden:
                    if term in text:
                        offenders.append(f"{path.relative_to(root)}:{term}")
        self.assertEqual(offenders, [])

    def test_support_transport_caps_anthropic_and_openai(self):
        anthropic_create = unittest.mock.Mock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(text='{"per_unit":[]}')]
            )
        )
        anthropic_client = SimpleNamespace(
            messages=SimpleNamespace(create=anthropic_create)
        )
        with patch.dict(os.environ, {"OPENAI_BASE_URL": ""}, clear=False):
            with patch("anthropic.Anthropic", return_value=anthropic_client):
                support_model_call("prompt", "claude-sonnet-4-6")
        self.assertEqual(
            anthropic_create.call_args.kwargs["max_tokens"],
            ANTHROPIC_OPENAI_MAX_TOKENS,
        )

        openai_create = unittest.mock.Mock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content='{"per_unit":[]}'))
                ]
            )
        )
        openai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=openai_create)
            )
        )
        with patch("openai.OpenAI", return_value=openai_client):
            support_model_call("prompt", "gpt-5")
        self.assertEqual(
            openai_create.call_args.kwargs["max_tokens"],
            ANTHROPIC_OPENAI_MAX_TOKENS,
        )

    def test_support_transport_caps_gemini(self):
        class Response:
            def read(self):
                return b'{"candidates":[{"content":{"parts":[{"text":"{}"}]}}]}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        response = Response()
        captured = {}

        def open_request(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return response

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            with patch("urllib.request.urlopen", side_effect=open_request):
                support_model_call("prompt", "gemini/test-model")
        self.assertEqual(
            captured["body"]["generationConfig"]["maxOutputTokens"],
            GEMINI_MAX_OUTPUT_TOKENS,
        )

    def test_support_transport_uses_boto3_for_bedrock_bearer_token(self):
        captured = {}

        class Client:
            def converse(self, **kwargs):
                captured.update(kwargs)
                return {
                    "output": {
                        "message": {
                            "content": [{"text": '{"bedrock": true}'}],
                        }
                    }
                }

        fake_boto3 = SimpleNamespace(
            client=lambda service, region_name=None, config=None: captured.update(
                {"service": service, "region_name": region_name, "config": config}
            ) or Client()
        )

        with patch.dict(
            os.environ,
            {
                "AWS_BEARER_TOKEN_BEDROCK": "token",
                "AWS_REGION": "us-east-1",
                "OPENAI_BASE_URL": "",
            },
            clear=False,
        ):
            with patch.dict(sys.modules, {"boto3": fake_boto3}):
                result = support_model_call(
                    "prompt",
                    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                )

        self.assertEqual(result, '{"bedrock": true}')
        self.assertEqual(captured["service"], "bedrock-runtime")
        self.assertEqual(captured["region_name"], "us-east-1")
        self.assertEqual(
            captured["inferenceConfig"]["maxTokens"],
            ANTHROPIC_OPENAI_MAX_TOKENS,
        )
        self.assertEqual(captured["system"][0]["text"], "Output ONLY valid JSON. No markdown.")

    def test_refinement_transport_caps_all_providers(self):
        anthropic_create = unittest.mock.Mock(
            return_value=SimpleNamespace(content=[SimpleNamespace(text="{}")])
        )
        anthropic_client = SimpleNamespace(
            messages=SimpleNamespace(create=anthropic_create)
        )
        with patch.dict(os.environ, {"OPENAI_BASE_URL": ""}, clear=False):
            with patch("anthropic.Anthropic", return_value=anthropic_client):
                refinement_model_call("prompt", "claude-sonnet-4-6")
        self.assertEqual(
            anthropic_create.call_args.kwargs["max_tokens"],
            ANTHROPIC_OPENAI_MAX_TOKENS,
        )

        openai_create = unittest.mock.Mock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )
        )
        openai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=openai_create)
            )
        )
        with patch("openai.OpenAI", return_value=openai_client):
            refinement_model_call("prompt", "gpt-5")
        self.assertEqual(
            openai_create.call_args.kwargs["max_tokens"],
            ANTHROPIC_OPENAI_MAX_TOKENS,
        )

        class Response:
            def read(self):
                return b'{"candidates":[{"content":{"parts":[{"text":"{}"}]}}]}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        captured = {}

        def open_request(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return Response()

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            with patch("urllib.request.urlopen", side_effect=open_request):
                refinement_model_call("prompt", "gemini/test-model")
        self.assertEqual(
            captured["body"]["generationConfig"]["maxOutputTokens"],
            GEMINI_MAX_OUTPUT_TOKENS,
        )

    def test_explicit_openai_endpoint_routes_claude_learning_calls_to_proxy(self):
        create = unittest.mock.Mock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with patch.dict(
            os.environ,
            {"OPENAI_BASE_URL": "http://127.0.0.1:8742/v1"},
            clear=False,
        ):
            with patch("openai.OpenAI", return_value=client) as openai:
                support_model_call("support", "claude-sonnet-4-5")
                refinement_model_call("refine", "claude-sonnet-4-5")

        self.assertEqual(openai.call_count, 2)
        self.assertEqual(create.call_count, 2)
        self.assertEqual(
            [call.kwargs["model"] for call in create.call_args_list],
            ["claude-sonnet-4-5", "claude-sonnet-4-5"],
        )

    def test_vendored_client_uses_boto3_for_bedrock_bearer_token(self):
        from adamast.learning.vendor.llm import LLMClient

        captured = {}

        class Client:
            def converse(self, **kwargs):
                captured.update(kwargs)
                return {
                    "output": {
                        "message": {
                            "content": [{"text": '{"generated": true}'}],
                        }
                    }
                }

        fake_boto3 = SimpleNamespace(
            client=lambda service, region_name=None, config=None: captured.update(
                {"service": service, "region_name": region_name, "config": config}
            ) or Client()
        )

        class Config:
            def __init__(self, **kwargs):
                self.connect_timeout = kwargs["connect_timeout"]
                self.read_timeout = kwargs["read_timeout"]
                self.retries = kwargs["retries"]

        fake_botocore_config = SimpleNamespace(Config=Config)
        with patch.dict(
            os.environ,
            {
                "AWS_BEARER_TOKEN_BEDROCK": "token",
                "AWS_REGION": "us-east-1",
                "OPENAI_BASE_URL": "",
            },
            clear=False,
        ):
            with patch.dict(
                sys.modules,
                {
                    "boto3": fake_boto3,
                    "botocore.config": fake_botocore_config,
                },
            ):
                result = LLMClient(
                    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    timeout=321,
                ).chat("prompt", system="system prompt")

        self.assertEqual(result, '{"generated": true}')
        self.assertEqual(captured["service"], "bedrock-runtime")
        self.assertEqual(captured["region_name"], "us-east-1")
        self.assertEqual(captured["config"].connect_timeout, 10)
        self.assertEqual(captured["config"].read_timeout, 321)
        self.assertEqual(
            captured["config"].retries,
            {"max_attempts": 3, "mode": "adaptive"},
        )
        self.assertEqual(captured["system"][0]["text"], "system prompt")

    def test_vendored_client_preserves_claude_id_through_proxy(self):
        from adamast.learning.vendor.llm import LLMClient

        create = unittest.mock.Mock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with patch.dict(
            os.environ,
            {"OPENAI_BASE_URL": "http://127.0.0.1:8742/v1"},
            clear=False,
        ):
            with patch("openai.OpenAI", return_value=client):
                result = LLMClient("claude-sonnet-4-5").chat("prompt")

        self.assertEqual(result, "{}")
        self.assertEqual(create.call_args.kwargs["model"], "claude-sonnet-4-5")

    def test_refiner_retries_once_after_invalid_json(self):
        replies = iter(["not json", '{"repo":"","domain":"d","codes":[]}'])
        prompts = []

        def call(prompt, _model):
            prompts.append(prompt)
            return next(replies)

        parsed = refine_json("original", model="test", max_retries=1, call=call)
        self.assertEqual(parsed["domain"], "d")
        self.assertEqual(len(prompts), 2)
        self.assertIn("previous reply was NOT valid JSON", prompts[1])

    def test_refinement_formatter_is_outcome_blind(self):
        records = [
            {
                "problem_id": "p1",
                "task": "repair",
                "raw_trajectory": "Agent_Solver repaired the code.",
                "metadata": {
                    "outcome": "SECRET_OUTCOME",
                    "final_gate_status": "SECRET_GATE",
                },
            }
        ]
        text = format_refinement_traces(records)
        self.assertNotIn("SECRET_OUTCOME", text)
        self.assertNotIn("SECRET_GATE", text)
        prompt = build_refinement_prompt(
            {
                "repo": "",
                "domain": "Code Repair",
                "codes": [
                    {
                        "id": "A.1",
                        "name": "Failure",
                        "description": "A concrete failure.",
                        "category": "System",
                    }
                ],
            },
            records,
        )
        self.assertIn("PRESERVE code IDs", prompt)
        self.assertIn("ADD codes only from concrete evidence", prompt)
        self.assertNotIn("SECRET_OUTCOME", prompt)

    def test_real_vendored_pipeline_commits_canonical_record(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = ProgramWorkspace(root / "program")
            workspace.pending.append_many(
                [
                    GenerationTrace(
                        problem_id="vendor-e2e",
                        task="repair the code",
                        raw_trajectory=(
                            "Agent_Solver inspected a failing test and repaired "
                            "an inclusive boundary."
                        ),
                        metadata={
                            "_format": "adamast-unified",
                            "outcome": "SECRET_OUTCOME",
                            "final_gate_status": "SECRET_GATE",
                        },
                    )
                ]
            )
            fake = FakeVendoredModel()
            output_dir = root / "vendored-output"
            output_dir.mkdir()
            with patch(
                "adamast.learning.vendor.pipeline.pipeline.LLMClient",
                return_value=SimpleNamespace(chat=fake),
            ), patch(
                "adamast.learning.vendor.pipeline.pipeline.resolve_output_dir",
                return_value=output_dir,
            ):
                result = run_generation_job(
                    workspace,
                    store_dir=root / "taxonomies",
                    trace_root=root / "traces",
                    adamast_model="test-model",
                    skip_judge=True,
                )

            self.assertEqual(result.action, "activated")
            record = store.fetch_by_id(result.taxonomy_id, root / "taxonomies")
            self.assertEqual(record["domain"], "Code Repair")
            self.assertEqual(record["repo"], workspace.repo)
            self.assertTrue(record["codes"])
            for code in record["codes"]:
                self.assertEqual(
                    set(("id", "name", "description", "category")) - set(code),
                    set(),
                )
            all_prompts = "\n".join(fake.prompts)
            self.assertNotIn("SECRET_OUTCOME", all_prompts)
            self.assertNotIn("SECRET_GATE", all_prompts)


class GenerationOutputDirTests(unittest.TestCase):
    """The vendored pipeline must write under the program root, never CWD."""

    def _seed(self, workspace: ProgramWorkspace) -> None:
        workspace.pending.append_many(
            [
                GenerationTrace(
                    problem_id="outdir-1",
                    task="repair the code",
                    raw_trajectory="Agent_Solver repaired an inclusive boundary.",
                    metadata={"_format": "adamast-unified"},
                )
            ]
        )

    def test_explicit_output_dir_is_under_program_root_not_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = ProgramWorkspace(root / "program")
            self._seed(workspace)

            captured: dict = {}

            def fake_generate(*, traces, output_dir, model, save_intermediate, verbose):
                captured["output_dir"] = Path(output_dir)
                return {
                    "annotation_layer": {
                        "category_a": [
                            {"code": "A.1", "name": "Crash", "definition": "It crashes."},
                            {"code": "A.2", "name": "Loop", "definition": "It loops."},
                        ]
                    },
                    "category_definitions": {"A": "System"},
                }

            with patch("adamast.learning.vendor.generate_taxonomy", fake_generate):
                result = run_generation_job(
                    workspace,
                    store_dir=root / "taxonomies",
                    trace_root=root / "traces",
                    adamast_model="test-model",
                    skip_judge=True,
                )

            self.assertEqual(result.action, "activated")
            out = captured["output_dir"]
            # Under the program's own root, never the vendored cwd fallback.
            self.assertEqual(out, workspace.root / "generation")
            self.assertEqual(out.relative_to(workspace.root), Path("generation"))

    def test_real_pipeline_writes_under_program_root_and_not_cwd(self):
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as clean_cwd:
            root = Path(td)
            workspace = ProgramWorkspace(root / "program")
            self._seed(workspace)
            fake = FakeVendoredModel()

            previous_cwd = os.getcwd()
            os.chdir(clean_cwd)
            try:
                with patch(
                    "adamast.learning.vendor.pipeline.pipeline.LLMClient",
                    return_value=SimpleNamespace(chat=fake),
                ):
                    result = run_generation_job(
                        workspace,
                        store_dir=root / "taxonomies",
                        trace_root=root / "traces",
                        adamast_model="test-model",
                        skip_judge=True,
                    )
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result.action, "activated")
            generation_dir = workspace.root / "generation"
            # The vendored pipeline's mandatory outputs land under the program root.
            self.assertTrue((generation_dir / "taxonomy.json").is_file())
            self.assertTrue(list(generation_dir.glob("taxonomy_*.json")))
            # And nothing leaked into the worker's CWD.
            self.assertFalse((Path(clean_cwd) / "adamast_output").exists())


if __name__ == "__main__":
    unittest.main()
