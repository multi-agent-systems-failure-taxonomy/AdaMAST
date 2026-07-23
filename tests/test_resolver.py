"""Resolver tests: the three --inherit behaviors, plus none-selection."""

import unittest
from pathlib import Path

from adamast.core import resolver, store

STORE_DIR = Path(__file__).resolve().parent / "fixtures" / "taxonomies"


class ResolveTests(unittest.TestCase):
    def test_no_inherit_returns_none(self):
        # Form 1: --inherit absent -> "none", no launcher needed.
        self.assertEqual(resolver.resolve(resolver.ABSENT, STORE_DIR), "none")

    def test_explicit_existing_id_returns_id(self):
        # Form 2: --inherit <existing> -> that id.
        self.assertEqual(
            resolver.resolve("tax-requests-http-002", STORE_DIR),
            "tax-requests-http-002",
        )

    def test_explicit_missing_id_errors_not_silent_none(self):
        # Form 2: --inherit <missing> -> clear error, never "none".
        with self.assertRaises(store.TaxonomyNotFound):
            resolver.resolve("tax-missing-999", STORE_DIR)

    def test_explicit_mast_id_resolves_to_floor(self):
        # Form 2: --inherit mast -> the floor decision "none" (Render
        # resolves "none" to the built-in MAST constant). MAST is never a
        # store record, so this must not consult the store at all.
        self.assertEqual(resolver.resolve("mast", STORE_DIR), "none")
        self.assertEqual(
            resolver.resolve("mast", Path("does-not-exist")), "none"
        )

    def test_no_id_launches_webview_and_returns_choice(self):
        # Form 3: --inherit (no id) -> launcher result returned verbatim.
        calls = []

        def fake_launcher(store_dir):
            calls.append(store_dir)
            return "tax-flask-routing-004"

        result = resolver.resolve(resolver.NO_ID, STORE_DIR, launcher=fake_launcher)
        self.assertEqual(result, "tax-flask-routing-004")
        self.assertEqual(calls, [STORE_DIR])

    def test_no_id_none_selection_path(self):
        # Form 3: picker returns "none" -> resolver returns "none".
        result = resolver.resolve(
            resolver.NO_ID, STORE_DIR, launcher=lambda _sd: "none"
        )
        self.assertEqual(result, "none")

    def test_no_id_without_launcher_is_error(self):
        with self.assertRaises(RuntimeError):
            resolver.resolve(resolver.NO_ID, STORE_DIR, launcher=None)


if __name__ == "__main__":
    unittest.main()
