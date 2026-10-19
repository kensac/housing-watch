"""Web UI: FastAPI + Jinja2, HTMX for partial updates (vendored, static/htmx.min.js, 2.0.4).

Every page is server-rendered. List state (filters, search, sort, columns) lives in the query string,
so any view is a shareable URL; an HTMX request for the same URL gets only the fragment that changed.
The data is read-only from here; the one table the UI writes is its own `saved_views`, through
same-origin POST/PATCH/DELETE (tailnet-only and single-user, so there is no login).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.engine import Engine

from .. import digest, queries, views
from .. import filters as F
from ..criteria import PROFILE, Profile

HERE = Path(__file__).parent
NY = ZoneInfo("America/New_York")
RESULTS = {"listings": "results", "events": "ev-results"}  # HX-Target of a list refresh


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return "never"
    s = (datetime.now(UTC) - dt).total_seconds()
    fut, s = s < 0, abs(s)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            v = f"{int(s // n)}{unit}"
            break
    else:
        return "just now" if not fut else "now"
    return f"in {v}" if fut else f"{v} ago"


def _ny(dt: datetime | None, fmt: str = "%a %b %-d %Y, %-I:%M %p %Z") -> str:
    return dt.astimezone(NY).strftime(fmt) if dt else ""


def _money(v: float | None) -> str:
    return "" if v is None else f"${v:,.0f}"


def _duration(s: float | None) -> str:
    if s is None:
        return ""
    return f"{s:.1f}s" if s < 60 else f"{int(s // 60)}m {int(s % 60):02d}s"


def _profile_line(p: Profile) -> str:
    sizes = ", ".join(sorted(p.sizes))
    out = f"{p.household} person · ${p.income:,} · {sizes}"
    return out + (f" · not {', '.join(sorted(p.excluded_boroughs))}" if p.excluded_boroughs else "")


def _state(req: Request, page: str, engine: Engine):
    """The list state from the URL, or from a saved view: `?view=N` alone opens view N, and a bare page URL
    opens the page's default view. Returns (state, active view or None, problems to show)."""
    qp = req.query_params
    vid = qp.get("view", "")
    own = [k for k in qp if k not in ("view", "open", "offset", "before")]
    view = None
    if vid.isdigit():
        view = views.get(engine, int(vid))
    elif not qp:
        view = views.default(engine, page)
    if view and not own:
        return view.state, view, list(view.warnings)
    conds, errors = [], []
    for raw in qp.getlist("f"):
        try:
            conds.append(F.parse_one(raw, views.FIELDS[page]))
        except F.FilterError as e:
            errors.append(str(e))
    cols = tuple(c for c in qp.get("cols", "").split(",") if c)
    st = views.State(page, conds, qp.get("q", ""), qp.get("sort", ""), cols)
    return st, view, errors + (view.warnings if view else [])


def create_app(engine: Engine, profile: Profile = PROFILE) -> FastAPI:
    app = FastAPI(title="housing-watch", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters.update(ago=_ago, ny=_ny, money=_money, duration=_duration)
    tpl.env.globals.update(profile_line=_profile_line(profile), statuses=queries.STATUSES, COLUMNS=views.COLUMNS)

    def render(req: Request, name: str, nav: str, partial: str | None = None, **ctx) -> HTMLResponse:
        """The full page, or just `partial` when HTMX asks for a fragment of it."""
        h = req.headers
        hx = h.get("HX-Request") == "true" and "HX-Boosted" not in h and "HX-History-Restore-Request" not in h
        ctx.setdefault("saved", views.with_counts(engine, profile))
        frag = bool(hx and partial)  # only a fragment carries out-of-band swaps (chips, view bar, nav)
        return tpl.TemplateResponse(req, partial if frag else name, {"nav": nav, "hx": frag, **ctx})

    def list_ctx(req: Request, page: str) -> dict:
        st, view, problems = _state(req, page, engine)
        # keep the view in the URL; "none" stops a bare URL from reopening the page's default view
        vparam = view.id if view else ("none" if views.default(engine, page) else "")

        def link(filters=None, sort=None, **extra) -> str:
            fs = st.filters if filters is None else filters
            return views.State(page, fs, st.q, sort or st.sort, st.columns).url(view=vparam, **extra)

        def verdict_url(v: str) -> str:
            fs = [c for c in st.filters if c.field.name != "verdict"]
            return link(fs + ([F.cond(views.FIELDS[page], "verdict", "in", [v])] if v else []))

        on = [c for c in st.filters if c.field.name == "verdict"]
        return dict(
            st=st, view=view, problems=problems, page=page, url=link, verdict_url=verdict_url,
            modified=bool(view) and st.key() != view.state.key(),
            drop=lambda i: link([c for j, c in enumerate(st.filters) if j != i]),
            clear_url=views.State(page, [], "", st.sort, st.columns).url(view="none" if vparam else ""),
            canonical=link(open=req.query_params.get("open", "")),
            verdict_on=on[0].raw[0] if len(on) == 1 and on[0].op == "in" and len(on[0].raw) == 1 else "",
            hidden_gone=page == "listings" and queries.hides_gone(st.filters),
        )  # fmt: skip

    def wants(req: Request, target: str) -> bool:
        return req.headers.get("HX-Target") == target

    @app.get("/", response_class=HTMLResponse)
    def overview(req: Request):
        built = digest.build(engine, profile)
        return render(req, "overview.html", "overview", o=queries.overview(engine, profile), preview=built)

    @app.get("/listings", response_class=HTMLResponse)
    def listings(req: Request, offset: int = 0, open: str = ""):
        c = list_ctx(req, "listings")
        st = c["st"]
        res = queries.search_listings(engine, queries.ListingQuery(st.filters, st.q, st.sort, offset), profile)
        detail = queries.listing_detail(engine, open, profile) if open and not offset else None
        partial = "_listing_rows.html" if offset else ("_listings_results.html" if wants(req, "results") else None)
        return render(req, "listings.html", "listings", partial, res=res, detail=detail, **c)

    @app.get("/listings/drawer", response_class=HTMLResponse)
    def drawer(req: Request, key: str):
        if req.headers.get("HX-Request") != "true":
            return RedirectResponse("/listings?" + urlencode({"open": key}))
        d = queries.listing_detail(engine, key, profile)
        return render(req, "_drawer.html", "listings", detail=d, key=key)

    @app.get("/sources", response_class=HTMLResponse)
    def sources(req: Request):
        return render(req, "sources.html", "sources", rows=queries.sources(engine, profile))

    @app.get("/fleet", response_class=HTMLResponse)
    def fleet(req: Request):
        return render(req, "fleet.html", "fleet", fl=queries.fleet(engine))

    @app.get("/events", response_class=HTMLResponse)
    def events(req: Request, before: int | None = None):
        c = list_ctx(req, "events")
        st = c["st"]
        page = queries.events(engine, st.filters, before=before, p=profile)
        partial = "_event_rows.html" if before else ("_events_table.html" if wants(req, "ev-results") else None)
        return render(req, "events.html", "events", partial, ev=page, before=before, **c)

    @app.get("/filters/builder", response_class=HTMLResponse)
    def builder(req: Request, page: str, field: str = "", edit: int = -1):
        """The "+ Filter" popover: pick a field, then its operator and value (with counts for enums)."""
        if page not in views.FIELDS:
            return PlainTextResponse("unknown page", 404)
        st, _, _ = _state(req, page, engine)
        fields = views.FIELDS[page]
        fld = fields.get(field)
        current = st.filters[edit] if 0 <= edit < len(st.filters) else None
        ctx = dict(page=page, fld=fld, current=current, edit=edit if current else -1, OPS=F.OPS, OP_LABEL=F.OP_LABEL)
        ctx["groups"] = [
            (g, [f for f in fields.values() if f.group == g]) for g in dict.fromkeys(f.group for f in fields.values())
        ]
        if fld:
            others = [c for i, c in enumerate(st.filters) if i != edit]
            ctx["enum"] = queries.is_enum(engine, fld, profile)
            if ctx["enum"]:
                ctx["counts"] = queries.value_counts(engine, page, fld, others, st.q, profile)
        return tpl.TemplateResponse(req, "_builder.html", ctx)

    # --- saved views: the UI's own config, written only from same-origin requests ---------------------------

    def same_origin(req: Request) -> bool:
        src = req.headers.get("origin") or req.headers.get("referer") or ""
        site = req.headers.get("sec-fetch-site", "same-origin")
        return bool(src) and urlsplit(src).netloc == req.headers.get("host") and site == "same-origin"

    def done(location: str) -> Response:
        return Response(status_code=204, headers={"HX-Location": location})

    def back(form, page: str) -> str:
        b = str(form.get("back") or "")
        return b if b.startswith("/") and not b.startswith("//") else f"/{page}"

    async def guarded(req: Request):
        """The form (urlencoded body or query string; htmx sends both shapes) of a same-origin request."""
        if not same_origin(req):
            return None, PlainTextResponse("cross-origin request refused", 403)
        body = (await req.body()).decode()
        return dict(req.query_params) | dict(parse_qsl(body, keep_blank_values=True)), None

    def form_state(form, page: str) -> views.State:
        """The state to save, from the page URL the request came from."""
        req_like = Request({"type": "http", "query_string": urlsplit(back(form, page)).query.encode(), "headers": []})
        return _state(req_like, page, engine)[0]

    @app.post("/views")
    async def create_view(req: Request):
        form, err = await guarded(req)
        if err:
            return err
        page = str(form.get("page"))
        if page not in views.FIELDS:
            return PlainTextResponse("unknown page", 400)
        try:
            v = views.create(engine, form_state(form, page), str(form.get("name", "")), form.get("default") == "1")
        except views.ViewError as e:
            return PlainTextResponse(str(e), 400)
        return done(v.url())

    @app.patch("/views/{view_id}")
    async def change_view(req: Request, view_id: int):
        form, err = await guarded(req)
        if err:
            return err
        v = views.get(engine, view_id)
        if v is None:
            return PlainTextResponse("no such view", 404)
        kw: dict = {}
        if "name" in form:
            kw["name"] = str(form["name"])
        if form.get("save") == "1":
            kw["state"] = form_state(form, v.page)
        if "default" in form:
            kw["is_default"] = form["default"] == "1"
        if form.get("move") in ("-1", "1"):
            kw["move"] = int(form["move"])
        try:
            v = views.change(engine, view_id, **kw)
        except views.ViewError as e:
            return PlainTextResponse(str(e), 400)
        return done(v.url() if "state" in kw else back(form, v.page))

    @app.post("/views/{view_id}/duplicate")
    async def duplicate_view(req: Request, view_id: int):
        _, err = await guarded(req)
        if err:
            return err
        v = views.duplicate(engine, view_id)
        return done(v.url()) if v else PlainTextResponse("no such view", 404)

    @app.delete("/views/{view_id}")
    async def delete_view(req: Request, view_id: int):
        form, err = await guarded(req)
        if err:
            return err
        v = views.get(engine, view_id)
        if v is None or not views.delete(engine, view_id):
            return PlainTextResponse("no such view", 404)
        return done(v.state.url())

    @app.exception_handler(Exception)
    async def boom(req: Request, exc: Exception):  # the store is down or a query broke: say so, in the layout
        ctx = {"nav": "", "hx": False, "error": exc, "saved": []}
        return tpl.TemplateResponse(req, "error.html", ctx, status_code=500)

    return app
