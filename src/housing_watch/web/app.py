"""Web UI: a JSON API over queries.py and views.py, plus the React + Mantine app that uses it.

The app lives in `frontend/` and is built into `web/dist/` by the image (`npm run build`); every
non-API path serves its index.html, and the app keeps all list state (filters, search, sort, columns,
view, open listing) in its own URL. Filters cross the API in the URL grammar of filters.py
(`f=<field>.<op>.<values>`, repeated). The data is read-only from here; the one table the API writes
is `saved_views`, and only from same-origin requests (tailnet-only and single-user, so no login).
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine

from .. import applications, digest, queries, views
from .. import filters as F
from ..criteria import PROFILE, Profile

DIST = Path(__file__).parent / "dist"
SORTS = {
    "listings": {"verdict": "Verdict", "changed": "Last changed", "first_seen": "First seen",
                 "rent": "Lowest rent that fits", "commute": "Commute", "deadline": "Deadline", "title": "Title",
                 "source": "Source", "borough": "Borough"},
    "units": {"verdict": "Verdict", "rent": "Rent", "commute": "Commute", "ami": "AMI", "size": "Size",
              "income_min": "Income min", "income_max": "Income max", "changed": "Last changed",
              "first_seen": "First seen", "deadline": "Deadline", "title": "Building", "borough": "Borough",
              "source": "Source"},
}  # fmt: skip
Filters = Annotated[list[str] | None, Query()]  # repeated ?f=<field>.<op>.<values>
_PRIVATE = ("unit_sizes", "unit_rents", "haystack")  # ListingSummary's internal fields


class Problem(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def _summary(x: queries.ListingSummary) -> dict:
    d = asdict(x)
    for k in _PRIVATE:
        d.pop(k, None)
    return d


def _chip(i: int, c: F.Cond) -> dict:
    return {"i": i, "f": c.url(), "label": c.label(), "field": c.field.name, "op": c.op, "values": list(c.raw)}


def _field(f: F.Field) -> dict:
    return {"name": f.name, "label": f.label, "group": f.group, "type": f.type, "ops": list(F.OPS[f.type]),
            "money": f.money, "choices": list(f.choices)}  # fmt: skip


def _parse(page: str, raw: list[str] | None) -> tuple[list[tuple[int, F.Cond]], list[str]]:
    """Parse every filter, keeping its index in the request; one that does not parse is reported, not fatal."""
    out, problems = [], []
    for i, s in enumerate(raw or []):
        try:
            out.append((i, F.parse_one(s, views.FIELDS[page])))
        except F.FilterError as e:
            problems.append(str(e))
    return out, problems


def _conds(page: str, raw: list[str] | None) -> tuple[list[F.Cond], list[str]]:
    parsed, problems = _parse(page, raw)
    return [c for _, c in parsed], problems


def _chips(page: str, raw: list[str] | None) -> list[dict]:
    return [_chip(i, c) for i, c in _parse(page, raw)[0]]


def _view(v: views.View) -> dict:
    st = v.state
    return {
        "id": v.id, "page": v.page, "name": v.name, "position": v.position, "is_default": v.is_default,
        "warnings": v.warnings, "count": v.count, "updated": v.updated,
        "state": {"f": [c.url() for c in st.filters], "q": st.q, "sort": st.sort, "columns": list(st.columns)},
    }  # fmt: skip


def _state(page: str, body: dict | None) -> views.State:
    if page not in views.FIELDS:
        raise Problem(400, f"Unknown page “{page}”")
    st = body or {}
    conds, problems = _conds(page, list(st.get("f") or []))
    if problems:
        raise Problem(400, "; ".join(problems))
    return views.State(page, conds, str(st.get("q") or ""), str(st.get("sort") or ""), tuple(st.get("columns") or ()))


def _same_origin(req: Request) -> None:
    src = req.headers.get("origin") or req.headers.get("referer") or ""
    host = src.split("://", 1)[-1].split("/", 1)[0]
    if not src or host != req.headers.get("host") or req.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        raise Problem(403, "cross-origin request refused")


def create_app(engine: Engine, profile: Profile = PROFILE, dist: Path = DIST) -> FastAPI:
    app = FastAPI(title="housing-watch", docs_url=None, redoc_url=None, openapi_url=None)
    api = APIRouter(prefix="/api")

    @app.exception_handler(Problem)
    async def problem(req: Request, exc: Problem):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def boom(req: Request, exc: Exception):  # the store is down or a query broke: say so, as JSON
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    # --- read ---------------------------------------------------------------------------------------------

    @api.get("/meta")
    def meta():
        p = profile
        return {
            "profile": {"household": p.household, "income": p.income, "sizes": sorted(p.sizes),
                        "excluded_boroughs": sorted(p.excluded_boroughs)},
            "statuses": list(queries.STATUSES), "verdicts": list(queries.VERDICTS), "op_labels": F.OP_LABEL,
            "pages": {
                page: {"fields": [_field(f) for f in views.FIELDS[page].values()],
                       "columns": views.COLUMNS[page], "default_columns": list(views.DEFAULT_COLUMNS[page]),
                       "default_sort": views.DEFAULT_SORT[page], "sorts": SORTS.get(page, {})}
                for page in views.FIELDS
            },
        }  # fmt: skip

    @api.get("/listings")
    def listings(f: Filters = None, q: str = "", sort: str = "", offset: int = 0, limit: int = 200):
        conds, problems = _conds("listings", f)
        lq = queries.ListingQuery(
            conds, q, sort or views.DEFAULT_SORT["listings"], max(offset, 0), min(max(limit, 1), 500)
        )
        res = queries.search_listings(engine, lq, profile)
        return {
            "items": [_summary(x) for x in res.items], "total": res.total, "next_offset": res.next_offset,
            "verdict_counts": res.verdict_counts, "filters": _chips("listings", f), "problems": problems,
            "hidden_gone": queries.hides_gone(conds),
        }  # fmt: skip

    @api.get("/units")
    def units(f: Filters = None, q: str = "", sort: str = "", offset: int = 0, limit: int = 200):
        conds, problems = _conds("units", f)
        uq = queries.ListingQuery(
            conds, q, sort or views.DEFAULT_SORT["units"], max(offset, 0), min(max(limit, 1), 500)
        )
        res = queries.search_units(engine, uq, profile)
        return {
            "items": [{k: v for k, v in asdict(u).items() if k != "haystack"} for u in res.items],
            "total": res.total, "next_offset": res.next_offset, "verdict_counts": res.verdict_counts,
            "filters": _chips("units", f), "problems": problems, "hidden_gone": queries.hides_gone(conds),
        }  # fmt: skip

    @api.get("/listing")
    def listing(key: str):
        d = queries.listing_detail(engine, key, profile)
        if d is None:
            raise Problem(404, "This listing is not in the store.")
        return asdict(d) | {"summary": _summary(d.summary), "pdfs": applications.pdf_urls(d.notes)}

    @api.get("/listing/applications")
    def listing_applications(key: str):
        """Each linked application PDF, fetched to see whether it is a form we can fill."""
        d = queries.listing_detail(engine, key, profile)
        out = []
        for n, url in enumerate(applications.pdf_urls(d.notes) if d else []):
            try:
                form = applications.detect(applications.blank(url))
                out.append({"n": n, "url": url, "form": form.name if form else None, "error": None})
            except Exception as e:  # noqa: BLE001 — a dead link is shown, not raised
                out.append({"n": n, "url": url, "form": None, "error": str(e)})
        return {"pdfs": out, "ready": applications.applicant() is not None}

    @api.get("/listing/application")
    def listing_application(key: str, n: int = 0):
        """The listing's n-th application PDF, filled from the applicant file. Built per request, never stored."""
        a = applications.applicant()
        if a is None:
            raise Problem(404, "No applicant file is configured (HW_APPLICANT_FILE).")
        d = queries.listing_detail(engine, key, profile)
        urls = applications.pdf_urls(d.notes) if d else []
        if not 0 <= n < len(urls):
            raise Problem(404, "This listing has no such application PDF.")
        filled = applications.fill(applications.blank(urls[n]), a)
        if filled is None:
            raise Problem(422, f"{urls[n]} is not a form housing-watch knows how to fill.")
        name = re.sub(r"[^\w.-]+", "-", key.split(":", 1)[-1]).strip("-")[:60] or "listing"
        headers = {"Content-Disposition": f'attachment; filename="application-{name}.pdf"', "Cache-Control": "no-store"}
        return Response(filled, media_type="application/pdf", headers=headers)

    @api.get("/values")
    def values(page: str, field: str, f: Filters = None, q: str = ""):
        """Distinct values of a field with counts under the other filters (send them without the one being
        edited); `enum` says whether the field has few enough values to pick from."""
        if page not in views.FIELDS or field not in views.FIELDS[page]:
            raise Problem(404, "Unknown field")
        fld = views.FIELDS[page][field]
        conds, _ = _conds(page, f)
        enum = queries.is_enum(engine, fld, profile)
        counts = queries.value_counts(engine, page, fld, conds, q, profile) if enum else []
        return {"enum": enum, "counts": [{"value": v, "count": n} for v, n in counts]}

    @api.get("/events")
    def events(f: Filters = None, before: int | None = None):
        conds, problems = _conds("events", f)
        page = queries.events(engine, conds, before=before, p=profile)
        return {"items": page.items, "total": page.total, "next_before": page.next_before,
                "filters": _chips("events", f), "problems": problems}  # fmt: skip

    @api.get("/overview")
    def overview():
        o = queries.overview(engine, profile)
        built = digest.build(engine, profile)
        return asdict(o) | {
            "eligible_now": [_summary(x) for x in o.eligible_now],
            "digest": {"subject": built[0], "text": built[1], "events": len(built[3])} if built else None,
        }

    @api.get("/sources")
    def sources():
        return queries.sources(engine, profile)

    @api.get("/fleet")
    def fleet():
        return queries.fleet(engine)

    # --- saved views: the UI's own config ---------------------------------------------------------------

    @api.get("/views")
    def list_views():
        return [_view(v) for v in views.with_counts(engine, profile)]

    @api.post("/views")
    async def create_view(req: Request):
        _same_origin(req)
        body = await req.json()
        st = _state(str(body.get("page", "")), body.get("state"))
        try:
            return _view(views.create(engine, st, str(body.get("name", "")), bool(body.get("is_default"))))
        except views.ViewError as e:
            raise Problem(400, str(e)) from None

    @api.patch("/views/{view_id}")
    async def change_view(req: Request, view_id: int):
        _same_origin(req)
        body = await req.json()
        v = views.get(engine, view_id)
        if v is None:
            raise Problem(404, "No such view")
        kw: dict = {}
        if "name" in body:
            kw["name"] = str(body["name"])
        if "state" in body:
            kw["state"] = _state(v.page, body["state"])
        if "is_default" in body:
            kw["is_default"] = bool(body["is_default"])
        if body.get("move") in (-1, 1):
            kw["move"] = body["move"]
        try:
            return _view(views.change(engine, view_id, **kw))
        except views.ViewError as e:
            raise Problem(400, str(e)) from None

    @api.post("/views/{view_id}/duplicate")
    def duplicate_view(req: Request, view_id: int):
        _same_origin(req)
        v = views.duplicate(engine, view_id)
        if v is None:
            raise Problem(404, "No such view")
        return _view(v)

    @api.delete("/views/{view_id}")
    def delete_view(req: Request, view_id: int):
        _same_origin(req)
        if not views.delete(engine, view_id):
            raise Problem(404, "No such view")
        return Response(status_code=204)

    @api.get("/{rest:path}")
    def no_route(rest: str):
        raise Problem(404, f"No API route /api/{rest}")

    app.include_router(api)

    # --- the app ------------------------------------------------------------------------------------------

    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{rest:path}", include_in_schema=False)
    def spa(rest: str):
        index = dist / "index.html"
        if not index.is_file():
            return PlainTextResponse("The web app is not built (cd frontend && npm run build).", 503)
        file = (dist / rest).resolve()
        if rest and file.is_file() and dist.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
