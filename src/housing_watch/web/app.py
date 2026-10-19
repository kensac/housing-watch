"""Read-only web UI: FastAPI + Jinja2, HTMX for partial updates (vendored, static/htmx.min.js, 2.0.4).

Every page is server-rendered. Filter state lives in the query string, so any view is a shareable URL;
an HTMX request for the same URL gets only the fragment that changed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.engine import Engine

from .. import digest, queries
from ..criteria import PROFILE, Profile

HERE = Path(__file__).parent
NY = ZoneInfo("America/New_York")
LISTING_PARAMS = ("verdict", "status", "source", "borough", "size", "max_rent", "q", "since", "sort")


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


def create_app(engine: Engine, profile: Profile = PROFILE) -> FastAPI:
    app = FastAPI(title="housing-watch", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.filters.update(ago=_ago, ny=_ny, money=_money, duration=_duration)
    tpl.env.globals.update(profile_line=_profile_line(profile), statuses=queries.STATUSES)

    def render(req: Request, name: str, nav: str, partial: str | None = None, **ctx) -> HTMLResponse:
        """The full page, or just `partial` when HTMX asks for it."""
        h = req.headers
        hx = h.get("HX-Request") == "true" and "HX-Boosted" not in h and "HX-History-Restore-Request" not in h
        return tpl.TemplateResponse(req, partial if hx and partial else name, {"nav": nav, "hx": hx, **ctx})

    @app.get("/", response_class=HTMLResponse)
    def overview(req: Request):
        built = digest.build(engine, profile)
        return render(req, "overview.html", "overview", o=queries.overview(engine, profile), preview=built)

    @app.get("/listings", response_class=HTMLResponse)
    def listings(req: Request, offset: int = 0, open: str = ""):
        qp = {k: req.query_params.get(k, "") for k in LISTING_PARAMS}
        try:
            max_rent = float(qp["max_rent"]) if qp["max_rent"] else None
        except ValueError:
            max_rent = None
        f = queries.ListingFilters(**{**qp, "max_rent": max_rent, "sort": qp["sort"] or "verdict"}, offset=offset)
        res = queries.search_listings(engine, f, profile)
        state = {k: v for k, v in qp.items() if v}

        def url(**kw) -> str:
            q = {k: v for k, v in {**state, **kw}.items() if v not in ("", None)}
            return "/listings" + ("?" + urlencode(q) if q else "")

        detail = queries.listing_detail(engine, open, profile) if open and not offset else None
        partial = "_listing_rows.html" if offset else "_listings_results.html"  # next batch, or a new list
        return render(req, "listings.html", "listings", partial, res=res, f=f, qp=qp, url=url, detail=detail)

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
    def events(req: Request, type: str = "", source: str = "", baseline: str = "1", before: int | None = None):
        page = queries.events(engine, type, source, baseline=baseline != "0", before=before, p=profile)
        state = {"type": type, "source": source, "baseline": "" if baseline != "0" else "0"}
        more = "/events?" + urlencode({k: v for k, v in {**state, "before": page.next_before}.items() if v})
        srcs = sorted({x.source for x in queries.judged(engine, profile)})
        partial = "_event_rows.html" if before else "_events_table.html"
        return render(
            req, "events.html", "events", partial, page=page, state=state, more=more, sources=srcs, before=before
        )

    @app.exception_handler(Exception)
    async def boom(req: Request, exc: Exception):  # the store is down or a query broke: say so, in the layout
        return tpl.TemplateResponse(req, "error.html", {"nav": "", "hx": False, "error": exc}, status_code=500)

    return app
