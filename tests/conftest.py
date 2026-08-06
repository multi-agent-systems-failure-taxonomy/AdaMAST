"""Shared pytest fixtures for the AdaMAST test suite."""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_adamast_home():
    """Point ADAMAST_HOME at a throwaway dir for the whole test session.

    Keeps env-derived, call-time paths (e.g. the project-root pin cache in
    ``adamast.core.project_scope``) out of the developer's real ``~/.adamast``
    and makes runs hermetic. Tests that need their own home still override this
    locally; the previous value is restored afterwards.
    """
    previous = os.environ.get("ADAMAST_HOME")
    with tempfile.TemporaryDirectory(prefix="adamast-test-home-") as home:
        os.environ["ADAMAST_HOME"] = home
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("ADAMAST_HOME", None)
            else:
                os.environ["ADAMAST_HOME"] = previous
