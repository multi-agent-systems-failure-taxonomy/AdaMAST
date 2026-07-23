# Landing page and blog

The static pages served at the root of the project site, in front of the
MkDocs documentation (`/docs/`). Everything here is copied as-is by
[`scripts/build_site.py`](../scripts/build_site.py), which the `docs`
workflow runs on every docs change; the MkDocs build lands beside it.

| Path | Serves |
|---|---|
| `index.html` + `assets/landing.css` | The landing page at `/AdaMAST/` |
| `blog/index.html` + `assets/blog.css` | The research blog post at `/AdaMAST/blog/` (figures live next to the post) |

Edit the HTML directly — there is no build step for these pages. Keep them
dependency-free (no external scripts or fonts) so they load instantly and
work offline.
