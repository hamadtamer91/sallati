"""Adapter registry with automatic discovery.

Adapters are found from three places, none of which requires editing core code:
  1. every module inside the `pricewatch.adapters` package
  2. every *.py file in folders listed in PRICEWATCH_PLUGIN_PATHS (os.pathsep separated)
  3. installed packages exposing the entry-point group "pricewatch.adapters"
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import pkgutil
import re
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import ValidationError

from .base import AdapterContext, StoreAdapter

log = logging.getLogger(__name__)
_REGISTRY: dict[str, type[StoreAdapter]] = {}
_LOADED = False
T = TypeVar("T", bound=type[StoreAdapter])


class AdapterError(RuntimeError):
    pass


def register_adapter(key: str) -> Callable[[T], T]:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", key):
        raise AdapterError(f"invalid adapter key {key!r}")

    def deco(cls: T) -> T:
        if not issubclass(cls, StoreAdapter):
            raise AdapterError(f"{cls} must subclass StoreAdapter")
        existing = _REGISTRY.get(key)
        if existing and existing is not cls and existing.__qualname__ != cls.__qualname__:
            raise AdapterError(f"adapter key {key!r} already registered by {existing.__module__}")
        cls.key = key
        _REGISTRY[key] = cls
        return cls

    return deco


def _load_file(path: Path) -> None:
    name = f"pricewatch_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)


def autoload(force: bool = False, plugin_paths: list[str] | None = None) -> None:
    global _LOADED
    if _LOADED and not force:
        return
    from .. import adapters as pkg

    for mod in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}."):
        if mod.name.rsplit(".", 1)[-1] not in {"base", "registry"}:
            importlib.import_module(mod.name)

    paths = plugin_paths if plugin_paths is not None else [
        p for p in os.environ.get("PRICEWATCH_PLUGIN_PATHS", "").split(os.pathsep) if p
    ]
    for folder in paths:
        for f in sorted(Path(folder).glob("*.py")):
            if not f.name.startswith("_"):
                try:
                    _load_file(f)
                except Exception:  # a broken plugin must not take the others down
                    log.exception("failed to load adapter plugin %s", f)

    for ep in entry_points(group="pricewatch.adapters"):
        try:
            ep.load()
        except Exception:
            log.exception("failed to load adapter entry point %s", ep.name)
    _LOADED = True


def available_adapters() -> dict[str, type[StoreAdapter]]:
    autoload()
    return dict(sorted(_REGISTRY.items()))


def resolve_env(value: Any) -> Any:
    """Replace ${VAR} placeholders with environment values (secrets never live in config)."""
    if isinstance(value, str):
        def sub(m: re.Match[str]) -> str:
            v = os.environ.get(m.group(1))
            if v is None:
                raise AdapterError(f"environment variable {m.group(1)} is not set")
            return v
        return re.sub(r"\$\{([A-Z0-9_]+)\}", sub, value)
    if isinstance(value, dict):
        return {k: resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env(v) for v in value]
    return value


def build_adapter(key: str, ctx: AdapterContext) -> StoreAdapter:
    cls = available_adapters().get(key)
    if cls is None:
        raise AdapterError(f"unknown adapter {key!r}; available: {', '.join(available_adapters())}")
    ctx.config = resolve_env(ctx.config)
    try:
        return cls(ctx)
    except ValidationError as e:
        raise AdapterError(f"invalid config for store {ctx.store_slug!r} ({key}): {e}") from e
