"""Web UI: GraphQL at /graphql (schema.py) over queries.py, views.py and tracker.py, plus the React + Mantine
app that uses it.

The app lives in `frontend/` and is built into `web/dist/` by the image (`npm run build`); every other path
serves its index.html, and the app keeps all list state (filters, search, sort, columns, view, open listing) in
its own URL. Filters cross the API in the URL grammar of filters.py (`f: ["<field>.<op>.<values>", ...]`). The
data is read-only from here; the API writes only `saved_views` and `tracker_links`, and only from same-origin
requests (tailnet-only and single-user, so no login). GET queries and GraphiQL stay on.

The one REST route left is `GET /api/listing/application?key=&n=`, a filled application PDF: a binary download
does not fit GraphQL, and the app links to it with `<a href download>`.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine
from strawberry.fastapi import GraphQLRouter

from .. import applications, queries
from ..criteria import PROFILE, Profile
from .schema import Problem, schema

DIST = Path(__file__).parent / "dist"


def create_app(engine: Engine, profile: Profile = PROFILE, dist: Path = DIST) -> FastAPI:
    app = FastAPI(title="housing-watch", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(Problem)
    async def problem(req: Request, exc: Problem):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def boom(req: Request, exc: Exception):  # the store is down or a query broke: say so, as JSON
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    async def context(request: Request) -> dict:
        return {"request": request, "engine": engine, "profile": profile}

    app.include_router(GraphQLRouter(schema, context_getter=context), prefix="/graphql")

    @app.get("/api/listing/application")
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

    # --- the app ------------------------------------------------------------------------------------------

    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{rest:path}", include_in_schema=False)
    def spa(rest: str):
        if rest == "api" or rest.startswith("api/"):
            raise Problem(404, f"No API route /{rest}; the API is /graphql")
        index = dist / "index.html"
        if not index.is_file():
            return PlainTextResponse("The web app is not built (cd frontend && npm run build).", 503)
        file = (dist / rest).resolve()
        if rest and file.is_file() and dist.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
