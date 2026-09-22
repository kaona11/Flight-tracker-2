"""Provider registry."""

from __future__ import annotations

from typing import Type

from .base import BlockedError, Provider, ProviderContext, ProviderError
from .aa import AmericanProvider
from .alaska import AlaskaProvider
from .ba_avios import BritishAirwaysProvider
from .fixture import FixtureProvider
from .qantas import QantasProvider
from .qatar import QatarProvider

REGISTRY: dict[str, Type[Provider]] = {
    p.name: p for p in (
        BritishAirwaysProvider,
        QantasProvider,
        AlaskaProvider,
        AmericanProvider,
        QatarProvider,
        FixtureProvider,
    )
}

#: Sensible default sweep: the month-view engines that carry the least risk,
#: cheapest and safest first. Qatar is deliberately excluded — bring it in
#: explicitly, and preferably only to confirm dates these already flagged.
DEFAULT_PROVIDERS = ["alaska", "ba", "qantas"]


def build(name: str, ctx: ProviderContext, **kw) -> Provider:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown provider {name!r}; known: {', '.join(sorted(REGISTRY))}") from None
    return cls(ctx, **kw)


def names() -> list[str]:
    return sorted(REGISTRY)


__all__ = ["REGISTRY", "DEFAULT_PROVIDERS", "build", "names", "Provider",
           "ProviderContext", "ProviderError", "BlockedError"]
