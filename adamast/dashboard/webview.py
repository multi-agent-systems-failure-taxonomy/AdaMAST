"""Local AdaMAST taxonomy library and blocking selection web view."""

from __future__ import annotations

import html
import json
from importlib import resources
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from adamast.core import mast, store

NONE_SENTINEL = "__none__"
_CODE_PRIMARY = ("id", "name", "description", "category")


def _text_asset(name: str) -> str:
    return (
        resources.files("adamast.dashboard")
        .joinpath("assets").joinpath(name)
        .read_text(encoding="utf-8")
    )


_PAGE = _text_asset("webview.html")


def _render_table(
    store_dir,
    *,
    allow_none: bool = True,
    choice_options: list[dict[str, Any]] | None = None,
    picker_context: dict[str, Any] | None = None,
    selected: str | None = None,
) -> str:
    """Render the full taxonomy workspace; retained as the public list helper."""
    options = _catalog_options(store_dir, allow_none, choice_options)
    selected_value = selected or _recommended_value(options)
    selected_option = next(
        (option for option in options if _choice_value(option) == selected_value),
        options[0] if options else None,
    )
    body = _render_workspace(options, selected_option, store_dir, picker_context)
    return _PAGE.format(title="AdaMAST taxonomy library", body=body)


def _render_detail(taxonomy_id, store_dir) -> str:
    """Render one stored taxonomy inside the library workspace."""
    return _render_table(store_dir, selected=str(taxonomy_id))


def _catalog_options(
    store_dir,
    allow_none: bool,
    choice_options: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if choice_options is not None:
        return [dict(option) for option in choice_options]
    options = []
    for header in store.list_all(store_dir):
        taxonomy_id = str(header["taxonomy_id"])
        record = store.fetch_by_id(taxonomy_id, store_dir)
        options.append(
            {
                "kind": "taxonomy",
                "taxonomy_id": taxonomy_id,
                "label": store.display_name(record),
                "description": str(
                    record.get("summary")
                    or record.get("description")
                    or f"Failure modes for {record.get('domain') or 'stored work'}."
                ),
                "domain": str(record.get("domain") or "Stored taxonomy"),
                "origin": str(record.get("repo") or "Local store"),
                "recommended": False,
            }
        )
    if allow_none:
        options.append(
            {
                "kind": "disabled",
                "taxonomy_id": None,
                "label": "Start without a stored taxonomy",
                "description": "Return none so the calling workflow can start from zero.",
                "domain": "Unbound",
                "origin": "Session choice",
                "recommended": not options,
            }
        )
    return options


def _render_workspace(
    options: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    store_dir,
    picker_context: dict[str, Any] | None,
) -> str:
    context = picker_context or {}
    project = html.escape(str(context.get("project") or "Local library"))
    project_root = html.escape(str(context.get("project_root") or store_dir))
    session_id = str(context.get("session_id") or "")
    session_cell = ""
    if session_id:
        session_cell = (
            "<div><span>Session ID</span><strong>{session}</strong></div>".format(
                session=html.escape(session_id[:8])
            )
        )
        prompt_text = " ".join(str(context.get("session_prompt") or "").split())
        if len(prompt_text) > 120:
            prompt_text = prompt_text[:119].rstrip() + "…"
        if prompt_text:
            session_cell += (
                '<div class="scope-path"><span>First message</span>'
                "<strong>“{prompt}”</strong></div>".format(
                    prompt=html.escape(prompt_text)
                )
            )
    rows = []
    for option in options:
        value = _choice_value(option)
        is_selected = bool(selected and value == _choice_value(selected))
        label = html.escape(str(option.get("label") or value))
        domain = html.escape(str(option.get("domain") or "General"))
        origin = html.escape(str(option.get("origin") or "Local"))
        internal_id = html.escape(
            str(option.get("taxonomy_id") or option.get("kind") or value)
        )
        search = html.escape(
            " ".join(
                str(option.get(key) or "")
                for key in ("label", "description", "domain", "origin", "taxonomy_id")
            ).casefold(),
            quote=True,
        )
        badge = "Recommended" if option.get("recommended") else origin
        rows.append(
            '<a class="library-item{selected}" data-search="{search}" '
            'href="/?preview={value}" aria-current="{current}">'
            '<span class="item-top"><strong>{label}</strong>'
            '<span class="item-badge">{badge}</span></span>'
            '<span class="item-domain">{domain}</span>'
            '<span class="item-id">{internal_id}</span></a>'.format(
                selected=" is-selected" if is_selected else "",
                search=search,
                value=quote(value, safe=""),
                current="true" if is_selected else "false",
                label=label,
                badge=html.escape(badge),
                domain=domain,
                internal_id=internal_id,
            )
        )

    detail = _render_option_detail(selected, store_dir) if selected else _empty_detail()
    return (
        '<main class="app-shell">'
        '<header class="adamast-header">'
        '<div class="brand-line"><span class="brand">AdaMAST</span>'
        '<span class="header-divider"></span><span class="product">Taxonomy library</span></div>'
        '<div class="header-copy"><div><h1>Select the failure model for this conversation</h1>'
        '<p>Inspect the scope and failure modes before activating a taxonomy.</p></div>'
        '<span class="status"><span class="status-dot"></span>Selection required</span></div>'
        '<div class="scope-row"><div><span>Project</span><strong>{project}</strong></div>'
        '{session_cell}'
        '<div class="scope-path"><span>Scope</span><strong>{project_root}</strong></div>'
        '<div><span>Available</span><strong>{count} choices</strong></div></div>'
        '</header>'
        '<div class="workspace">'
        '<aside class="library-pane" aria-label="Taxonomy choices">'
        '<div class="pane-heading"><div><span class="section-label">Library</span>'
        '<h2>Taxonomies</h2></div><span class="result-count" id="result-count">{count}</span></div>'
        '<label class="search-wrap"><span>Search</span>'
        '<input id="catalog-search" type="search" placeholder="Name, domain, or project" '
        'oninput="filterCatalog(this.value)"></label>'
        '<div id="empty-state" class="empty" hidden>No matching taxonomies.</div>'
        '<nav class="library-list">{rows}</nav></aside>'
        '<section class="detail-pane">{detail}</section>'
        '</div></main>'
    ).format(project=project, session_cell=session_cell, project_root=project_root, count=len(options), rows="".join(rows), detail=detail)


def _render_option_detail(option: dict[str, Any], store_dir) -> str:
    kind = str(option.get("kind") or "taxonomy")
    taxonomy_id = str(option.get("taxonomy_id") or "")
    if kind == "taxonomy":
        record = store.fetch_by_id(taxonomy_id, store_dir)
    elif kind == "mast":
        record = dict(mast.MAST)
    else:
        record = {"taxonomy_id": "none", "codes": []}

    label = html.escape(str(option.get("label") or store.display_name(record)))
    description = html.escape(str(option.get("description") or ""))
    domain = html.escape(str(option.get("domain") or record.get("domain") or "General"))
    origin = html.escape(str(option.get("origin") or record.get("repo") or "Local"))
    if taxonomy_id:
        stable_id = f"Taxonomy UID: {html.escape(taxonomy_id)}"
    else:
        stable_id = html.escape(kind)
    codes = list(record.get("codes") or [])
    categories = len(
        {str(code.get("category") or "Uncategorized") for code in codes}
    )
    action = (
        "Disable AdaMAST for this conversation"
        if kind == "disabled"
        else f"Use {label}"
    )
    notice = ""
    if option.get("starts_fresh"):
        notice = (
            '<div class="mode-notice"><strong>Starts a new taxonomy branch.</strong> '
            "The project's existing shared taxonomy stays unchanged.</div>"
        )
    elif option.get("recommended"):
        notice = (
            '<div class="mode-notice recommended"><strong>Recommended for this scope.</strong> '
            "AdaMAST will use this as the conversation's failure model.</div>"
        )

    code_rows = "".join(_render_code(code) for code in codes)
    if not code_rows:
        code_rows = (
            '<div class="no-codes"><strong>No failure-mode gates will run.</strong>'
            " Trace learning and AdaMAST checkpoints are disabled only for this conversation.</div>"
        )
    code_tools = ""
    if codes:
        code_tools = (
            '<div class="code-toolbar"><div><span class="section-label">Failure modes</span>'
            '<h3>Taxonomy</h3></div><label><span>Filter codes</span>'
            '<input type="search" placeholder="ID, name, or category" '
            'oninput="filterCodes(this.value)"></label></div>'
        )
    return (
        '<div class="detail-header"><div class="detail-title">'
        '<span class="section-label">{kind_label}</span><h2>{label}</h2>'
        '<p class="stable-id">{stable_id}</p></div>'
        '<a class="primary-action" href="/choose?id={value}">{action}</a></div>'
        '<p class="detail-summary">{description}</p>{notice}'
        '<div class="facts"><div><span>Domain</span><strong>{domain}</strong></div>'
        '<div><span>Origin</span><strong>{origin}</strong></div>'
        '<div><span>Codes</span><strong>{code_count}</strong></div>'
        '<div><span>Categories</span><strong>{category_count}</strong></div></div>'
        '{code_tools}<div class="code-list">{code_rows}</div>'
    ).format(
        kind_label="Built-in baseline" if kind == "mast" else "Session setting" if kind == "disabled" else "Stored taxonomy",
        label=label,
        stable_id=stable_id,
        value=quote(_choice_value(option), safe=""),
        action=action,
        description=description,
        notice=notice,
        domain=domain,
        origin=origin,
        code_count=len(codes),
        category_count=categories,
        code_tools=code_tools,
        code_rows=code_rows,
    )


def _render_code(code: dict[str, Any]) -> str:
    code_id = html.escape(str(code.get("id") or "-"))
    name = html.escape(str(code.get("name") or "Unnamed failure mode"))
    description = html.escape(str(code.get("description") or ""))
    category = html.escape(str(code.get("category") or "Uncategorized"))
    extras = "".join(
        _render_extra(key, value)
        for key, value in code.items()
        if key not in _CODE_PRIMARY
    )
    search = html.escape(
        " ".join((code_id, name, description, category)).casefold(), quote=True
    )
    return (
        '<article class="code-row" data-code-search="{search}">'
        '<div class="code-id">{code_id}</div><div class="code-copy">'
        '<div class="code-heading"><h4>{name}</h4><span>{category}</span></div>'
        '<p>{description}</p>{extras}</div></article>'
    ).format(
        search=search,
        code_id=code_id,
        name=name,
        category=category,
        description=description,
        extras=extras,
    )


def _render_extra(key, value) -> str:
    if key == "evidence" and isinstance(value, dict):
        trace_ids = [str(item) for item in value.get("trace_ids") or []]
        rationale = html.escape(str(value.get("rationale") or ""))
        quotes = [
            item
            for item in (value.get("quotes") or [])
            if isinstance(item, dict) and str(item.get("quote") or "").strip()
        ]
        excerpts = "".join(
            "<blockquote>&ldquo;{quote}&rdquo;{source}</blockquote>".format(
                quote=html.escape(str(item["quote"]).strip()),
                source=(
                    f"<cite>{html.escape(label)}</cite>"
                    if (label := _trace_label(str(item.get("trace_id") or "")))
                    else ""
                ),
            )
            for item in quotes
        )
        count = len(trace_ids) or len(quotes)
        return (
            '<details class="evidence"><summary>Generation evidence '
            f"({count} {'trace' if count == 1 else 'traces'})</summary>"
            f"<p>{rationale}</p>{excerpts}</details>"
        )
    rendered = html.escape(
        json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    )
    return '<div class="extra"><strong>{key}</strong><span>{value}</span></div>'.format(
        key=html.escape(str(key)), value=rendered
    )


def _trace_label(trace_id: str) -> str:
    """Compact human label for a canonical host:conversation:episode:N id."""
    parts = trace_id.split(":")
    if len(parts) == 4 and parts[2] == "episode":
        host, conversation, _, episode = parts
        return f"{host} · {conversation[:8]} · episode {episode}"
    return trace_id


def _empty_detail() -> str:
    return '<div class="no-codes"><strong>No taxonomies are stored yet.</strong></div>'


def _choice_value(option: dict[str, Any]) -> str:
    if option.get("kind") == "disabled":
        return "none"
    return str(option.get("taxonomy_id") or "").strip()


def _recommended_value(options: list[dict[str, Any]]) -> str:
    option = next((item for item in options if item.get("recommended")), None)
    return _choice_value(option or (options[0] if options else {}))


def build_server(
    store_dir=store.DEFAULT_STORE_DIR,
    host="127.0.0.1",
    port=0,
    *,
    allow_none: bool = True,
    choice_options: list[dict[str, Any]] | None = None,
    picker_context: dict[str, Any] | None = None,
    on_choose: Callable[[str], Any] | None = None,
):
    """Build ``(server, result, done)`` without starting the server."""
    result: dict[str, Any] = {"value": None}
    done = threading.Event()
    options = _catalog_options(store_dir, allow_none, choice_options)
    valid_choices = {_choice_value(option): option for option in options}
    valid_taxonomy_ids = {
        value for value, option in valid_choices.items() if option.get("kind") == "taxonomy"
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body, status=200, content_type="text/html; charset=utf-8"):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                preview = (parse_qs(parsed.query).get("preview") or [None])[0]
                self._send(
                    _render_table(
                        store_dir,
                        allow_none=allow_none,
                        choice_options=options,
                        picker_context=picker_context,
                        selected=preview,
                    )
                )
                return

            if path.startswith("/taxonomy/"):
                taxonomy_id = path[len("/taxonomy/") :]
                if taxonomy_id not in valid_taxonomy_ids:
                    self._send(_error_page("Taxonomy not found", "No such taxonomy."), 404)
                    return
                self._send(
                    _render_table(
                        store_dir,
                        allow_none=allow_none,
                        choice_options=options,
                        picker_context=picker_context,
                        selected=taxonomy_id,
                    )
                )
                return

            if path == "/choose":
                chosen = (parse_qs(parsed.query).get("id") or [""])[0]
                if chosen == NONE_SENTINEL and allow_none:
                    chosen = "none"
                if chosen not in valid_choices:
                    self._send(_error_page("Unknown choice", "Return to the library and select an available option."), 400)
                    return
                try:
                    if on_choose is not None:
                        on_choose(chosen)
                except (OSError, TimeoutError, ValueError) as exc:
                    self._send(_error_page("Selection was not applied", str(exc)), 409)
                    return
                result["value"] = chosen
                option = valid_choices[chosen]
                label = html.escape(str(option.get("label") or chosen))
                self._send(_success_page(label, chosen, picker_context))
                done.set()
                return

            self._send(_error_page("Page not found", "Return to the taxonomy library."), 404)

    # Threading matters: a synchronous host hook probes this server for
    # liveness while the just-opened browser is still fetching the page. A
    # single-threaded server makes that probe queue behind the page render
    # and read as a dead picker.
    server = ThreadingHTTPServer((host, port), Handler)
    return server, result, done


def _success_page(
    label: str,
    choice: str,
    picker_context: dict[str, Any] | None,
) -> str:
    host_label = str((picker_context or {}).get("host_label") or "agent session")
    next_step = (
        f"You may close this tab. Return to {host_label}; "
        "your original task will continue automatically."
        if picker_context is not None
        else "You may close this tab and return to the terminal."
    )
    session_id = str((picker_context or {}).get("session_id") or "")
    session_line = (
        f'<p class="stable-id">for session {html.escape(session_id[:8])}</p>'
        if session_id
        else ""
    )
    body = (
        '<main class="result-page"><span class="result-state success">Activated</span>'
        f"<h1>{label}</h1><p>{html.escape(next_step)}</p>"
        f'<p class="stable-id">{html.escape(choice)}</p>{session_line}</main>'
    )
    return _PAGE.format(title="AdaMAST selection activated", body=body)


def _error_page(title: str, message: str) -> str:
    body = (
        '<main class="result-page"><span class="result-state error">Not applied</span>'
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>"
        '<a class="secondary-action" href="/">Return to library</a></main>'
    )
    return _PAGE.format(title=title, body=body)


def run_webview(
    store_dir=store.DEFAULT_STORE_DIR,
    host="127.0.0.1",
    port=0,
    open_browser=True,
    on_serving=None,
) -> str:
    """Launch the blocking web view and return a taxonomy id or ``none``."""
    server, result, done = build_server(store_dir, host, port)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://{host}:{actual_port}/"
        if on_serving is not None:
            on_serving(host, actual_port)
        print(f"Taxonomy picker open at {url}  (waiting for your choice...)")
        if open_browser:
            webbrowser.open(url)
        done.wait()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    return str(result["value"])
