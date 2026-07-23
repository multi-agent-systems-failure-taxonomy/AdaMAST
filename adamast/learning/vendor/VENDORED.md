# Maintained AdaMAST research-pipeline fork

This directory began as the `atlas` Python package from:

- Source: https://github.com/multi-agent-systems-failure-taxonomy/ATLAS
- Commit: `85efae436daf5a4c8299ff7e9a46a6717cb0a3bd`
- Upstream package version: `1.0.0`
- License: Apache License 2.0; see `LICENSE` in this directory.

It is now a maintained in-tree fork, intentionally distinct from the
AdaMAST runtime. The source commit above is provenance, not a claim that
the current directory is byte-for-byte or behaviorally unchanged.

## Local changes

Changes since the source snapshot include:

- namespace changes from `atlas` to `adamast.learning.vendor`;
- model transport and token-budget handling used by the packaged runtime;
- classifier, trace-normalization, prompt-loading, and JSON-repair behavior;
- pipeline validation, deduplication, category generation, and support checks;
- package assets, tests, and compatibility fixes for supported Python versions.

These are semantic changes, not only mechanical import rewrites. Review the Git
history for the authoritative patch sequence. To compare with the source
snapshot, extract the `atlas/` tree from commit `85efae4` and run a recursive
diff against this directory after accounting for the namespace prefix.
