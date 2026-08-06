"""Interactive project and task-group identity tests."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adamast.core.project_scope import (
    canonical_project_root,
    project_key,
    project_program_path,
)


class ProjectScopeTests(unittest.TestCase):
    def test_git_subdirectories_share_one_project_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            nested = root / "src" / "feature"
            nested.mkdir(parents=True)
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=(str(root), False),
            ):
                self.assertEqual(
                    project_key(root),
                    project_key(nested),
                )
                self.assertEqual(canonical_project_root(nested), root)

    def test_distinct_workspace_paths_do_not_share_programs(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first = base / "first"
            second = base / "second"
            first.mkdir()
            second.mkdir()
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=("", False),
            ):
                first_program = project_program_path(
                    base / "adamast",
                    cwd=first,
                )
                second_program = project_program_path(
                    base / "adamast",
                    cwd=second,
                )
            self.assertNotEqual(first_program, second_program)

    def test_task_groups_share_project_key_but_not_program(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            default = project_program_path(base / "adamast", cwd=project)
            billing = project_program_path(
                base / "adamast",
                cwd=project,
                task_group="billing",
            )
            self.assertNotEqual(default, billing)
            self.assertEqual(default.parents[2], billing.parents[2])

    def test_resolved_root_is_pinned_against_transient_git_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            nested = root / "src"
            nested.mkdir()
            # First resolve succeeds via git and pins nested -> root.
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=(str(root), False),
            ):
                self.assertEqual(canonical_project_root(nested), root)
                pinned_key = project_key(nested)
            # A later transient git failure must NOT repartition history.
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=("", True),
            ):
                self.assertEqual(canonical_project_root(nested), root)
                self.assertEqual(project_key(nested), pinned_key)

    def test_transient_failure_without_a_pin_is_not_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp).resolve()
            child = workspace / "repo"
            child.mkdir()
            # Transient failure, no prior pin: fall back to the workspace but do
            # not pin it, so a later successful resolve can still win.
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=("", True),
            ):
                self.assertEqual(canonical_project_root(child), child)
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=(str(workspace), False),
            ):
                self.assertEqual(canonical_project_root(child), workspace)

    def test_late_git_init_cannot_repartition_a_pinned_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp).resolve()
            # Established as a clean non-repo: pinned to itself.
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=("", False),
            ):
                self.assertEqual(canonical_project_root(workspace), workspace)
                original_key = project_key(workspace)
            # A parent later gets `git init`; git now reports a new toplevel.
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=(str(workspace.parent), False),
            ):
                self.assertEqual(canonical_project_root(workspace), workspace)
                self.assertEqual(project_key(workspace), original_key)

    def test_pin_is_dropped_when_the_target_no_longer_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp).resolve()
            gone = workspace / "gone"
            gone.mkdir()
            here = workspace / "here"
            here.mkdir()
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=(str(gone), False),
            ):
                self.assertEqual(canonical_project_root(here), gone)
            shutil.rmtree(gone)
            # A pinned target that vanished must not be returned as a dead path.
            with patch(
                "adamast.core.project_scope._git_top_level",
                return_value=(str(here), False),
            ):
                self.assertEqual(canonical_project_root(here), here)

    def test_explicit_project_id_is_stable_and_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(
                project_key(temp, project_id="company-tools"),
                "company-tools",
            )
            with self.assertRaises(ValueError):
                project_key(temp, project_id="../escape")


if __name__ == "__main__":
    unittest.main()
