"""The bundled examples ship inside the package and materialize locally.

Guards the documented quickstart: ``python -m adamast.examples`` followed by
``adamast validate adamast-examples/traces.jsonl`` must work from a pip
install, where no repository checkout exists.
"""

from __future__ import annotations

import json
from importlib.resources import files

from adamast import foundation_cli
from adamast.examples import __main__ as materializer


def test_example_data_ships_inside_the_package():
    package = files("adamast.examples")
    assert (package / "traces.jsonl").is_file()
    assert (package / "taxonomy.sample.json").is_file()
    assert (package / "dashboard_demo.py").is_file()


def test_materializer_copies_files_for_the_quickstart(tmp_path, capsys):
    target = tmp_path / "adamast-examples"
    code = materializer.main([str(target)])
    assert code == 0
    copied = (target / "traces.jsonl").read_text(encoding="utf-8")
    packaged = (files("adamast.examples") / "traces.jsonl").read_text(encoding="utf-8")
    assert copied == packaged
    assert (target / "taxonomy.sample.json").is_file()
    assert str(target.resolve()) in capsys.readouterr().out


def test_quickstart_validate_runs_on_materialized_traces(tmp_path, capsys):
    target = tmp_path / "adamast-examples"
    assert materializer.main([str(target)]) == 0
    capsys.readouterr()
    code = foundation_cli.main(["validate", str(target / "traces.jsonl")])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["trace_count"] >= 1
