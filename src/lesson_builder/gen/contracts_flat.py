"""Flat per-operation contracts: the minimal content the LLM must return for each
operation. Python constructs the typed *Payload from these. A structural problem here
(wrong count, answer not in options) is a FlatContractError -> bounded retry."""

from __future__ import annotations

from typing import Any, cast


class FlatContractError(ValueError):
    """The model's flat response is structurally unusable (pre-construction check)."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise FlatContractError(msg)


def _str(d: dict[str, Any], key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or v.strip() == "":
        raise FlatContractError(f"{key!r} must be a non-empty string")
    return v.strip()


def _str_list(d: dict[str, Any], key: str, *, min_len: int) -> list[str]:
    v = d.get(key)
    if not isinstance(v, list) or len(v) < min_len:
        raise FlatContractError(f"{key!r} must be a list of >= {min_len}")
    _require(all(isinstance(x, str) and x.strip() for x in v), f"{key!r} items must be non-empty strings")
    return [x.strip() for x in v]


def _dict_item(v: Any, label: str) -> dict[str, Any]:
    _require(isinstance(v, dict), f"{label} must be a dict, got {type(v).__name__!r}")
    return cast("dict[str, Any]", v)
