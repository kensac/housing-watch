"""Postgres schema as SQLAlchemy 2.0 models. Alembic migrations (alembic/versions/) are generated from these.

Storage holds raw facts only: listings -> units -> bands as each source published them, page snapshots,
and change events. The task queue is Procrastinate's own tables (procrastinate_*), not modelled here.
No eligibility verdict is stored: criteria.py judges at read time, so a profile change re-judges all
history without re-scraping.
The domain dataclasses adapters return live in models.py; db.py maps between the two.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .models import Borough, EventType, Kind, PublishedStatus, Size

TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class ListingRow(Base):
    """A building/opportunity on one source; key = "<source>:<ext_id>"."""

    __tablename__ = "listings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, index=True)
    ext_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    # info["values"]: the column's closed value set, so filters.py offers it as an enum
    borough: Mapped[str | None] = mapped_column(Text, index=True, info={"values": Borough})
    status: Mapped[str] = mapped_column(Text, index=True, info={"values": PublishedStatus})
    kind: Mapped[str] = mapped_column(Text, info={"values": Kind})
    deadline: Mapped[str | None] = mapped_column(Text)
    senior_only: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)  # hash of the fields whose change is an event
    first_seen: Mapped[datetime] = mapped_column(TS)
    last_seen: Mapped[datetime] = mapped_column(TS)
    last_changed: Mapped[datetime] = mapped_column(TS)
    gone_since: Mapped[datetime | None] = mapped_column(TS)  # set when the source stopped listing it
    units: Mapped[list[UnitRow]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", order_by="UnitRow.pos"
    )


class UnitRow(Base):
    __tablename__ = "units"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_key: Mapped[str] = mapped_column(ForeignKey("listings.key", ondelete="CASCADE"), index=True)
    pos: Mapped[int] = mapped_column(Integer)
    size: Mapped[str] = mapped_column(Text, info={"values": Size})
    rent: Mapped[float | None] = mapped_column(Float)
    ami: Mapped[int | None] = mapped_column(Integer)
    unit_no: Mapped[str | None] = mapped_column(Text)
    set_aside: Mapped[str | None] = mapped_column(Text)
    available: Mapped[bool | None] = mapped_column(Boolean)
    chart_year: Mapped[int | None] = mapped_column(Integer)  # HUD chart the printed caps are on
    listing: Mapped[ListingRow] = relationship(back_populates="units")
    bands: Mapped[list[BandRow]] = relationship(
        back_populates="unit", cascade="all, delete-orphan", order_by="BandRow.household"
    )


class BandRow(Base):
    __tablename__ = "bands"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"), index=True)
    household: Mapped[int] = mapped_column(Integer)
    income_min: Mapped[int | None] = mapped_column(Integer)
    income_max: Mapped[int | None] = mapped_column(Integer)
    unit: Mapped[UnitRow] = relationship(back_populates="bands")


class SnapshotRow(Base):
    """A watched page with no parseable structure: change detection only."""

    __tablename__ = "snapshots"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, index=True)
    url: Mapped[str | None] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    links: Mapped[list[str]] = mapped_column(JSONB, default=list)
    first_seen: Mapped[datetime] = mapped_column(TS)
    last_seen: Mapped[datetime] = mapped_column(TS)
    changed: Mapped[datetime | None] = mapped_column(TS)


class EventRow(Base):
    """new | changed | closed | gone | reopened | page_changed | source_failed | source_recovered."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now(), index=True)
    source: Mapped[str] = mapped_column(Text, index=True)
    key: Mapped[str | None] = mapped_column(Text, index=True)  # listing/snapshot key, None for source events
    type: Mapped[str] = mapped_column(Text, info={"values": EventType})
    summary: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)  # e.g. {"before": ..., "after": ...} for changed
    notified: Mapped[datetime | None] = mapped_column(TS)  # set once a digest included it
    __table_args__ = (Index("events_pending", "id", postgresql_where=text("notified IS NULL")),)


class RunRow(Base):
    """One completed source run (all chunks of one cycle), for health and the UI."""

    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(BigInteger)  # procrastinate_jobs.id of the source_run
    started: Mapped[datetime] = mapped_column(TS)
    finished: Mapped[datetime | None] = mapped_column(TS)
    ok: Mapped[bool | None] = mapped_column(Boolean)
    listings: Mapped[int | None] = mapped_column(Integer)
    snapshots: Mapped[int | None] = mapped_column(Integer)
    events: Mapped[int | None] = mapped_column(Integer)
    workers: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # who ran its chunks
    error: Mapped[str | None] = mapped_column(Text)
    chunks: Mapped[int | None] = mapped_column(Integer)  # planned chunk count; None for a single-chunk run
    plan: Mapped[dict | None] = mapped_column(JSONB)  # {"keys": [...], "cache": {...}} every chunk starts from
    __table_args__ = (Index("runs_source_started", "source", "started"),)


class ChunkResultRow(Base):
    """One finished chunk of a multi-chunk run: Procrastinate keeps no return values, so results land here.

    Deleted once the run is merged.
    """

    __tablename__ = "chunk_results"
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    pos: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)
    result: Mapped[dict | None] = mapped_column(JSONB)  # models.Result.to_json()
    cache: Mapped[dict | None] = mapped_column(JSONB)  # top-level cache keys the chunk changed
    worker: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    finished: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class SourceStateRow(Base):
    __tablename__ = "source_state"
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    last_ok: Mapped[datetime | None] = mapped_column(TS)
    last_attempt: Mapped[datetime | None] = mapped_column(TS)
    failing_since: Mapped[datetime | None] = mapped_column(TS)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    next_due: Mapped[datetime | None] = mapped_column(TS)  # scheduler enqueues when now() >= next_due
    queue: Mapped[str | None] = mapped_column(Text)  # "byparr" once the source needed fetch.rendered()


class AdapterCacheRow(Base):
    """Per-source scratch space adapters persist between runs (e.g. Reside's url -> lastmod)."""

    __tablename__ = "adapter_cache"
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class SavedViewRow(Base):
    """A named, saved filter/sort/columns for one UI page. The UI's own config: the only table it writes."""

    __tablename__ = "saved_views"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    page: Mapped[str] = mapped_column(Text)  # listings | units | events
    name: Mapped[str] = mapped_column(Text)
    query: Mapped[dict] = mapped_column(
        JSONB
    )  # views.State.to_json(): {"filter": <filter tree>, "q", "sort", "columns"}
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    updated: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (
        Index("saved_views_one_default", "page", unique=True, postgresql_where=text("is_default")),
        Index("saved_views_page", "page", "position"),
    )


class FleetWorkerRow(Base):
    """One worker process, by HW_WORKER_NAME (the host): Procrastinate's own workers table has no name."""

    __tablename__ = "fleet_workers"
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    queues: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    concurrency: Mapped[int] = mapped_column(Integer)
    started: Mapped[datetime] = mapped_column(TS)
    last_seen: Mapped[datetime] = mapped_column(TS)  # heartbeat, every HEARTBEAT seconds while running


class CommuteRow(Base):
    """A transit route from one listing address to one destination, as the routing API returned it."""

    __tablename__ = "commutes"
    origin: Mapped[str] = mapped_column(Text, primary_key=True)  # commute.origin(address, borough)
    destination: Mapped[str] = mapped_column(Text, primary_key=True)  # HW_COMMUTE_TO
    minutes: Mapped[int | None] = mapped_column(Integer)  # None: no route found
    status: Mapped[str] = mapped_column(Text)  # ok | no_route | failed
    route: Mapped[dict | None] = mapped_column(JSONB)  # {"lines": [...], "walk_min", "transfers"}
    depart: Mapped[datetime | None] = mapped_column(TS)  # the departure it was routed for
    computed: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class TrackerRow(Base):
    """One row of the owner's application tracker sheet, as last synced (the sheet is the source of truth)."""

    __tablename__ = "tracker_rows"
    row_key: Mapped[str] = mapped_column(Text, primary_key=True)  # tracker.row_key(property)
    position: Mapped[int] = mapped_column(Integer)  # sheet row number
    property: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)  # the Apply / Source cell's link
    page_url: Mapped[str | None] = mapped_column(Text)
    cells: Mapped[dict] = mapped_column(JSONB, default=dict)  # every column by its header
    synced: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class TrackerEventRow(Base):
    """A tracker row's Status as first seen or changed between syncs (the sheet keeps only the current one)."""

    __tablename__ = "tracker_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    row_key: Mapped[str] = mapped_column(Text, index=True)
    status_from: Mapped[str | None] = mapped_column(Text)  # None on a row's first event
    status_to: Mapped[str | None] = mapped_column(Text)
    baseline: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # the row predates tracking: its real start is earlier
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class TrackerLinkRow(Base):
    """A manual match the owner set in the UI: this listing is (row_key) or is not (None) that sheet row."""

    __tablename__ = "tracker_links"
    listing_key: Mapped[str] = mapped_column(Text, primary_key=True)
    row_key: Mapped[str | None] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(TS, server_default=func.now())
