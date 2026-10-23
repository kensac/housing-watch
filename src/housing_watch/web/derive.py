"""GraphQL types derived from the read model's dataclasses, so the schema never restates a field.

`derive(Dataclass)` makes a Strawberry type with the dataclass's fields and their Python types: nested
dataclasses become their own derived types, `X | None` stays nullable, lists, tuples and sets become lists (sets
sorted), a `type X = Literal[...]` alias becomes the GraphQL enum `X` (one per alias, see enum_type), the
models.Key and models.Serial aliases become `ID`, and a field's trailing comment becomes its description. A field
made with `queries.internal()` is left out. `types` overrides a field's type (e.g. JSON), `props` adds
properties, `extra` adds resolved fields. A dict field must be given a type: free-form JSON is a choice, never a
default.

Resolvers return the dataclass instances themselves; the derived type only describes them.
"""

from __future__ import annotations

import dataclasses
import enum
import inspect
import re
import types as pytypes
import typing
from datetime import date, datetime

import strawberry

from ..models import Key, Serial

_SCALARS = (str, int, float, bool, datetime, date)
_DERIVED: dict[type, type] = {}  # dataclass -> its Strawberry type
_ENUMS: dict[object, type[enum.Enum]] = {}  # Literal alias -> its GraphQL enum


def gql_name(v: str) -> str:
    """A domain value as a GraphQL enum value name: "your move" is `your_move`, "1br" is `_1br`."""
    n = re.sub(r"\W+", "_", v)
    return f"_{n}" if n[:1].isdigit() else n


def enum_of(name: str, values, description: str | None = None):
    """A GraphQL enum over domain string constants. A value that is not a GraphQL name gets one (gql_name); the
    domain keeps its strings, and they serialize by value."""
    return strawberry.enum(enum.Enum(name, {gql_name(v): v for v in values}), description=description)


def enum_type(alias, description: str | None = None, name: str | None = None) -> type[enum.Enum]:
    """The GraphQL enum of a `type X = Literal[...]` alias, named after it; made once, so every field and input
    that uses the alias shares it. Call it first to give the enum a description or another name."""
    if alias not in _ENUMS:
        _ENUMS[alias] = enum_of(name or alias.__name__, typing.get_args(alias.__value__), description)
    return _ENUMS[alias]


def derive(dc: type, name: str | None = None, *, types: dict | None = None, props: tuple[str, ...] = (),
           exclude: tuple[str, ...] = (), extra: dict | None = None, description: str | None = None):  # fmt: skip
    """The Strawberry type of dataclass `dc` (see the module docstring)."""
    name = name or dc.__name__
    types = types or {}
    hints = typing.get_type_hints(dc)
    notes = _comments(dc)
    anns: dict = {}
    ns: dict = {"__module__": __name__, "__qualname__": name,
                "is_type_of": classmethod(lambda cls, obj, info: isinstance(obj, (dc, cls)))}  # fmt: skip
    for f in dataclasses.fields(dc):
        if f.name in exclude or f.metadata.get("internal"):
            continue
        t, hint = _map(hints[f.name], types.get(f.name), f"{name}.{f.name}"), hints[f.name]
        if _is_set(hint):
            ns[f.name] = strawberry.field(resolver=_sorted(f.name, t), description=notes.get(f.name))
        else:
            anns[f.name] = t
            ns[f.name] = strawberry.field(description=notes.get(f.name))
    for p in props:
        fget = getattr(dc, p).fget
        t = _map(typing.get_type_hints(fget)["return"], types.get(p), f"{name}.{p}")
        ns[p] = strawberry.field(resolver=_getter(p, t), description=inspect.getdoc(fget))
    ns.update(extra or {})
    ns["__annotations__"] = anns
    doc = dc.__doc__ if dc.__doc__ and not dc.__doc__.startswith(f"{dc.__name__}(") else None
    out = strawberry.type(type(name, (), ns), name=name, description=description or doc)
    _DERIVED.setdefault(dc, out)
    return out


def _map(t, leaf, where: str):
    """The Strawberry annotation for Python type `t`; `leaf` replaces its innermost scalar."""
    origin, args = typing.get_origin(t), typing.get_args(t)
    if origin in (typing.Union, pytypes.UnionType):
        inner = [a for a in args if a is not type(None)]
        if len(inner) != 1:
            raise TypeError(f"{where}: a union of several types needs an explicit GraphQL union")
        m = _map(inner[0], leaf, where)
        return m | None if type(None) in args else m
    if origin in (list, tuple, set, frozenset):
        if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
            raise TypeError(f"{where}: a fixed-length tuple has no GraphQL type; make it a dataclass")
        return list[_map(args[0], leaf, where)]
    if leaf is not None:
        return leaf
    if t in (Key, Serial):
        return strawberry.ID
    if isinstance(t, typing.TypeAliasType):
        if typing.get_origin(t.__value__) is typing.Literal:
            return enum_type(t)
        return _map(t.__value__, None, where)
    if t is dict or origin is dict:
        raise TypeError(f"{where}: give a dict field a type (an object type, or JSON when it is free-form)")
    if dataclasses.is_dataclass(t):
        return _DERIVED.get(t) or derive(t)
    if t in _SCALARS:
        return t
    raise TypeError(f"{where}: no GraphQL type for {t!r}")


def _is_set(t) -> bool:
    args = [a for a in typing.get_args(t) if a is not type(None)] if typing.get_origin(t) is pytypes.UnionType else [t]
    return any(typing.get_origin(a) in (set, frozenset) for a in args)


def _sorted(attr: str, t):
    def get(root):
        v = getattr(root, attr)
        return None if v is None else sorted(v)

    get.__annotations__ = {"root": strawberry.Parent[object], "return": t}
    return get


def _getter(attr: str, t):
    def get(root):
        return getattr(root, attr)

    get.__annotations__ = {"root": strawberry.Parent[object], "return": t}
    return get


def _comments(dc: type) -> dict[str, str]:
    """field name -> its trailing comment in the class body."""
    try:
        src = inspect.getsource(dc)
    except (OSError, TypeError):
        return {}
    out = {}
    for m in re.finditer(r"^ {4}(\w+): [^#\n]*#\s*(.+?)\s*$", src, re.M):
        note = re.sub(r"\s*# fmt: skip$", "", m.group(2))
        if note and not note.startswith(("fmt:", "noqa", "type:")):
            out[m.group(1)] = note[0].upper() + note[1:]
    return out
