"""Interactive project and task-group identity tests."""

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
                return_value=str(root),
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
                return_value="",
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
