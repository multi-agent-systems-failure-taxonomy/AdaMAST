# Taxonomy-generation prompt assets

These Markdown files are the model-facing instructions for the vendored AdaMAST
taxonomy-generation pipeline.

Python code in `adamast.learning.vendor.pipeline` still owns orchestration, trace
sampling, JSON validation, fallback behavior, and storage. These assets own the
natural-language instructions sent to the taxonomy-generation model.

Use `adamast.learning.vendor.pipeline.prompts.render_prompt_asset(name, **context)` when
running through Python, or read these files directly from another harness that
wants to supply the variables itself.
