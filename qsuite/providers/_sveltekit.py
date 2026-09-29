"""Decode SvelteKit ``__data.json`` payloads.

Several airline sites are now SvelteKit apps, and their data endpoints do not
return ordinary JSON. They return a *flattened* graph produced by `devalue`:
one flat array where every nested value is replaced by an integer index into
that same array, so shared and cyclic structures survive the round trip.

    {"type":"data","nodes":[null,{"type":"data","data":[
        {"days":1}, [2,4], {"date":3,"seats":5}, "2026-10-01", ... ]}]}

Index 0 is the root. Inside an object or array, every integer is a pointer,
never a literal -- literals live in the flat array and are pointed at. A few
small negative indices are sentinels rather than pointers.

Walking this with the ordinary parser finds nothing, because the "dates" are
integers and the days are scattered across unrelated slots. So it is decoded
back into the nested structure it describes before any provider sees it.
"""

from __future__ import annotations

import math
from typing import Any

# devalue's sentinel indices.
UNDEFINED = -1
HOLE = -2
NAN = -3
POSITIVE_INFINITY = -4
NEGATIVE_INFINITY = -5
NEGATIVE_ZERO = -6

_SENTINELS = {
    UNDEFINED: None,
    HOLE: None,
    NAN: math.nan,
    POSITIVE_INFINITY: math.inf,
    NEGATIVE_INFINITY: -math.inf,
    NEGATIVE_ZERO: -0.0,
}

#: Type tags devalue uses for values JSON cannot express. We keep the payload
#: inside, since for our purposes a Date is just its ISO string.
_PASSTHROUGH_TAGS = {"Date", "BigInt", "RegExp", "URL"}


def looks_like_sveltekit(payload: Any) -> bool:
    """True if this is a SvelteKit ``__data.json`` envelope."""
    return (isinstance(payload, dict)
            and payload.get("type") == "data"
            and isinstance(payload.get("nodes"), list))


def unflatten(values: list[Any], index: int = 0) -> Any:
    """Rebuild the nested value that a devalue-flattened array describes."""
    if not isinstance(values, list) or not values:
        return None
    memo: dict[int, Any] = {}
    return _resolve(values, index, memo)


def _resolve(values: list[Any], index: Any, memo: dict[int, Any]) -> Any:
    if not isinstance(index, int) or isinstance(index, bool):
        # Already a literal (some encoders inline short strings).
        return index
    if index in _SENTINELS:
        return _SENTINELS[index]
    if index < 0 or index >= len(values):
        return None
    if index in memo:
        return memo[index]           # also breaks cycles

    value = values[index]

    if isinstance(value, list):
        if value and isinstance(value[0], str) and value[0] in _PASSTHROUGH_TAGS:
            return _resolve(values, value[1], memo) if len(value) > 1 else None
        if value and value[0] == "Set":
            out_set: list[Any] = []
            memo[index] = out_set
            out_set.extend(_resolve(values, i, memo) for i in value[1:])
            return out_set
        if value and value[0] == "Map":
            out_map: dict[Any, Any] = {}
            memo[index] = out_map
            rest = value[1:]
            for k, v in zip(rest[0::2], rest[1::2]):
                key = _resolve(values, k, memo)
                out_map[key if isinstance(key, (str, int, float)) else str(key)] = \
                    _resolve(values, v, memo)
            return out_map
        out_list: list[Any] = []
        memo[index] = out_list
        out_list.extend(_resolve(values, i, memo) for i in value)
        return out_list

    if isinstance(value, dict):
        out_dict: dict[str, Any] = {}
        memo[index] = out_dict
        for key, ptr in value.items():
            out_dict[key] = _resolve(values, ptr, memo)
        return out_dict

    memo[index] = value
    return value


def decode(payload: Any) -> Any:
    """Decode a SvelteKit envelope into ordinary nested JSON.

    Every node that carries data is decoded; the results are returned as a list
    under ``nodes`` so the shape-driven parsers can walk them exactly as they
    would any other payload.
    """
    if not looks_like_sveltekit(payload):
        return payload
    decoded_nodes = []
    for node in payload.get("nodes") or []:
        if isinstance(node, dict) and isinstance(node.get("data"), list):
            decoded_nodes.append(unflatten(node["data"]))
        elif node is not None:
            decoded_nodes.append(node)
    return {"nodes": decoded_nodes}
