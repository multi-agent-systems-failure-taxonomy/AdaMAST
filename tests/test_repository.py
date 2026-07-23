"""Display-only repository metadata tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adamast.core.program import ProgramConflict, ProgramWorkspace
from adamast.core.repository import _discover_from_path, discover_repo


class RepositoryMetadataTests(unittest.TestCase):
    def setUp(self):
        _discover_from_path.cache_clear()

    def test_explicit_repo_wins(self):
        self.assertEqual(discover_repo("owner/project", Path.cwd()), "owner/project")

    def test_remote_is_normalized_to_owner_project(self):
        with patch(
            "adamast.core.repository._git",
            side_effect=["git@github.com:owner/project.git", ""],
        ):
            self.assertEqual(discover_repo(repo_path=Path.cwd()), "owner/project")

    def test_path_name_is_final_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "plain-project"
            path.mkdir()
            with patch("adamast.core.repository._git", return_value=""):
                self.assertEqual(discover_repo(repo_path=path), "plain-project")

    def test_program_persists_repo_and_rejects_explicit_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = ProgramWorkspace(td, repo="owner/project")
            self.assertEqual(workspace.repo, "owner/project")
            self.assertEqual(workspace.load()["repo"], "owner/project")
            with self.assertRaises(ProgramConflict):
                ProgramWorkspace(td, repo="other/project")


if __name__ == "__main__":
    unittest.main()
