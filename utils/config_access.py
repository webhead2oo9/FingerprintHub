"""Small typed accessors for validated config dictionaries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, Tuple


EMPTY_MAPPING: Mapping[str, Any] = MappingProxyType({})


def mapping_at(node: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return a child mapping or an empty mapping for absent/invalid sections."""
    value = node.get(key)
    return value if isinstance(value, Mapping) else EMPTY_MAPPING


def tuple_at(node: Mapping[str, Any], key: str) -> Tuple[Any, ...]:
    """Return a child iterable as a tuple, excluding strings and mappings."""
    value = node.get(key)
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return ()
    if isinstance(value, Iterable):
        return tuple(value)
    return ()
