"""Local ephemeral derived-detector cache (spec §3.4).

Git-ignored, keyed by hash(prose + engine version). Warm runs are fast; a cold run
(cache dir absent, or a prose hash miss) re-derives. `check` writes only this
directory, never the repo tree — the repo-tree derived cache workshop/persist.py
writes is a separate, explicit, user-triggered mechanism (ADR D-0016).

`RuleDetector` (and the two dataclasses it nests, `CrossReference` and
`NumericThreshold`) has fields JSON cannot represent losslessly on its own:
tuple fields round-trip through `json.loads` as lists, and the nested
dataclasses round-trip as plain dicts. `_to_jsonable`/`_from_jsonable` fix
this generically, driven by `dataclasses.fields` and the fields' resolved
type hints, rather than hand-listing which fields happen to be tuples today.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import types
import typing
from pathlib import Path

from guideline_checker.loader import RuleDetector

if typing.TYPE_CHECKING:
    from _typeshed import DataclassInstance

_ENV_OVERRIDE = "GUIDELINE_CACHE_DIR"
_DEFAULT_DIRNAME = ".guideline-cache"

_UNION_ORIGINS = (typing.Union, types.UnionType)


def cache_path(root: Path) -> Path:
    """Where the cache lives: `GUIDELINE_CACHE_DIR` if set, else `root/.guideline-cache`."""
    override = os.environ.get(_ENV_OVERRIDE)
    return Path(override) if override else root / _DEFAULT_DIRNAME


def prose_hash(prose: str, engine_version: str) -> str:
    """A deterministic key over the exact prose and the engine version that derived it."""
    digest = hashlib.sha256()
    digest.update(prose.encode("utf-8"))
    digest.update(b"\0")
    digest.update(engine_version.encode("utf-8"))
    return digest.hexdigest()


def load(root: Path, key: str) -> RuleDetector | None:
    """Return the cached detector for `key`, or `None` on a miss."""
    entry = cache_path(root) / f"{key}.json"
    if not entry.exists():
        return None
    data = json.loads(entry.read_text(encoding="utf-8"))
    return _from_jsonable(data, RuleDetector)


def store(root: Path, key: str, detector: RuleDetector) -> None:
    """Cache `detector` under `key`, creating the cache directory if needed."""
    directory = cache_path(root)
    directory.mkdir(parents=True, exist_ok=True)
    entry = directory / f"{key}.json"
    entry.write_text(json.dumps(_to_jsonable(detector)), encoding="utf-8")


def _to_jsonable(value: object) -> object:
    """Recursively turn a dataclass instance into a JSON-safe structure.

    Dataclasses become dicts (field name -> jsonable value); tuples become
    lists (JSON's native sequence). Plain scalars (str, int, bool, None) pass
    through unchanged.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    return value


def _from_jsonable[T](data: dict[str, object] | None, cls: type[T]) -> T | None:
    """Reconstruct an instance of dataclass `cls` from `_to_jsonable`'s output.

    Uses `cls`'s resolved field type hints (not hand-listed field names) to
    decide, per field, whether the JSON value must become a tuple or a
    nested dataclass instance.
    """
    if data is None:
        return None
    hints = typing.get_type_hints(cls)
    dc = typing.cast("type[DataclassInstance]", cls)
    kwargs = {f.name: _coerce(data.get(f.name), hints[f.name]) for f in dataclasses.fields(dc)}
    return cls(**kwargs)


def _coerce(raw: object, hint: object) -> object:
    """Coerce one JSON-decoded field value back to what `hint` declares."""
    origin = typing.get_origin(hint)
    if origin is tuple:
        return tuple(typing.cast("typing.Iterable[object]", raw)) if raw is not None else ()
    if origin in _UNION_ORIGINS:
        for arg in typing.get_args(hint):
            if dataclasses.is_dataclass(arg):
                return _coerce_dataclass(raw, arg)
        return raw
    if dataclasses.is_dataclass(hint):
        return _coerce_dataclass(raw, hint)
    return raw


def _coerce_dataclass(raw: object, cls: object) -> object:
    """Reconstruct a nested dataclass from its JSON dict, narrowing the dynamic types."""
    data = typing.cast("dict[str, object] | None", raw)
    return _from_jsonable(data, typing.cast("type[object]", cls))
