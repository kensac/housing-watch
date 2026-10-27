"""What an adapter returns: structured listings, or a snapshot of a page that has no structure."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, get_args

# Closed value sets, as type aliases: the API (web/derive.py, the MCP models) turns each into an enum.
type Borough = Literal["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
type Size = Literal["studio", "sro", "1br", "2br", "3br", "4br", "5br", "6br", "unknown"]  # parse.size's values
type Kind = Literal["re-rental", "lottery", "waitlist", "homeownership"]
type PublishedStatus = Literal["open", "waitlist", "upcoming", "unknown", "closed"]  # "unknown": the source shows none
type EventType = Literal["new", "reopened", "changed", "closed", "gone", "page_changed", "watching", "source_failed",
                         "source_recovered"]  # fmt: skip
type Key = str  # an opaque identifier ("<source>:<ext_id>", a tracker row key): the API's ID
type Serial = int  # a database id: the API's ID too


def values(alias) -> tuple[str, ...]:
    """The strings a Literal alias allows, in order."""
    return get_args(alias.__value__)


class NotFound(LookupError):
    """A write names something the store does not have. The message says what; the API and MCP show it."""


@dataclass(frozen=True)
class Band:
    """Income range for one household size."""

    household: int
    income_min: int | None
    income_max: int | None


@dataclass
class Unit:
    """One rentable offering: a specific apartment, or a tier (size + AMI) when units are not named."""

    size: Size  # normalized by parse.size
    rent: float | None = None
    ami: int | None = None
    unit_no: str | None = None  # "8L", or None for a tier
    bands: list[Band] = field(default_factory=list)
    set_aside: str | None = None  # "mobility", "senior", "disability", ... when the unit is restricted
    available: bool | None = None  # False when the source marks it rented/closed
    chart_year: int | None = None  # HUD chart year the printed caps match (ami.py), when they match one

    def key(self) -> str:
        return f"{self.size}|{self.unit_no or ''}|{self.ami or ''}|{self.set_aside or ''}"


@dataclass
class Listing:
    """A building/opportunity on one source."""

    source: str
    ext_id: str  # stable id within the source (slug, numeric id, url path)
    title: str
    url: str
    address: str | None = None
    borough: Borough | None = None
    status: PublishedStatus = "open"
    kind: Kind = "re-rental"
    units: list[Unit] = field(default_factory=list)
    deadline: str | None = None  # ISO date when the source publishes one
    senior_only: bool = False
    notes: str | None = None  # free text kept for the digest (e.g. how to apply)

    def key(self) -> str:
        return f"{self.source}:{self.ext_id}"


@dataclass
class Snapshot:
    """A watched page with no parseable structure: change detection only."""

    source: str
    ext_id: str  # usually the url
    url: str
    text: str  # normalized visible text
    links: list[str] = field(default_factory=list)  # document/listing links found on the page

    def key(self) -> str:
        return f"{self.source}:{self.ext_id}"


@dataclass
class Result:
    listings: list[Listing] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)

    def to_json(self) -> dict:
        """Plain JSON, for passing a chunk's Result between workers through the database."""
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> Result:
        def unit(u: dict) -> Unit:
            return Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]})

        return cls(
            listings=[Listing(**{**x, "units": [unit(u) for u in x["units"]]}) for x in d.get("listings", [])],
            snapshots=[Snapshot(**x) for x in d.get("snapshots", [])],
        )

    def extend(self, other: Result) -> Result:
        self.listings += other.listings
        self.snapshots += other.snapshots
        return self
