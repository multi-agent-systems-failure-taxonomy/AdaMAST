# Landing page and blog

The static pages served at the root of the project site, in front of the
MkDocs documentation (`/docs/`). Everything here is copied as-is by
[`scripts/build_site.py`](../scripts/build_site.py), which the `docs`
workflow runs on every docs change; the MkDocs build lands beside it.

| Path | Serves |
|---|---|
| `index.html` + `assets/landing.css` | The landing page at `/AdaMAST/` |
| `blogs/index.html` + the five post directories + `assets/blog.css` | The blog index and posts at `/AdaMAST/blogs/` (figures live next to each post) |
| `blog/index.html` | A redirect stub kept so old `/AdaMAST/blog/` links still resolve |

Edit the HTML directly; there is no build step for these pages. Keep them
dependency-free (no external scripts or fonts) so they load instantly and
work offline.
