from __future__ import annotations

import yaml

from src.model.action_block import (
    ActionBlock,
    Assertion,
    Compute,
    GenerateNonces,
    Operation,
    Receive,
    Send,
)


END_TOKEN = "<END>"


class ActionBlockParseError(ValueError):
    """Raised when an ``action_blocks.yaml`` document is malformed."""


def parse_action_blocks(text: str) -> tuple[ActionBlock, ...]:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ActionBlockParseError(f"invalid YAML: {exc}") from exc
    if loaded is None:
        return ()
    if not isinstance(loaded, list):
        raise ActionBlockParseError(
            "top-level must be a list of role blocks, "
            f"got {type(loaded).__name__}"
        )
    return tuple(_parse_block(raw, index) for index, raw in enumerate(loaded))


def _parse_block(raw: object, index: int) -> ActionBlock:
    location = f"role block #{index}"
    if not isinstance(raw, dict):
        raise ActionBlockParseError(
            f"{location} must be a mapping, got {type(raw).__name__}"
        )
    missing = {"role", "operations"} - set(raw)
    if missing:
        raise ActionBlockParseError(
            f"{location} missing required keys: {sorted(missing)}"
        )
    extra = set(raw) - {"role", "operations"}
    if extra:
        raise ActionBlockParseError(
            f"{location} has unexpected keys: {sorted(extra)}"
        )
    role = raw["role"]
    if not isinstance(role, str) or not role.strip():
        raise ActionBlockParseError(
            f"{location} 'role' must be a non-empty string"
        )
    raw_ops = raw["operations"]
    if not isinstance(raw_ops, list):
        raise ActionBlockParseError(
            f"{location} 'operations' must be a list, "
            f"got {type(raw_ops).__name__}"
        )
    ops = tuple(
        _parse_operation(raw_op, index, op_index)
        for op_index, raw_op in enumerate(raw_ops)
    )
    return ActionBlock(role=role, operations=ops)


def _parse_operation(
    raw: object, block_index: int, op_index: int
) -> Operation:
    location = f"role block #{block_index} operation #{op_index}"
    if not isinstance(raw, dict):
        raise ActionBlockParseError(
            f"{location} must be a mapping, got {type(raw).__name__}"
        )
    if len(raw) != 1:
        raise ActionBlockParseError(
            f"{location} must have exactly one key, found {sorted(raw)}"
        )
    ((key, value),) = raw.items()
    if key == "receive":
        return Receive(items=_parse_string_list(value, location, key))
    if key == "generate_nonces":
        return GenerateNonces(nonces=_parse_string_list(value, location, key))
    if key == "compute":
        return Compute(equations=_parse_string_list(value, location, key))
    if key == "assert":
        return Assertion(equations=_parse_string_list(value, location, key))
    if key == "send":
        return _parse_send(value, location)
    raise ActionBlockParseError(
        f"{location} has unknown operation key '{key}'"
    )


def _parse_string_list(
    value: object, location: str, key: str
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ActionBlockParseError(
            f"{location} '{key}' must be a list of strings, "
            f"got {type(value).__name__}"
        )
    items: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ActionBlockParseError(
                f"{location} '{key}' item #{i} must be a string, "
                f"got {type(item).__name__}"
            )
        items.append(item)
    return tuple(items)


def _parse_send(value: object, location: str) -> Send:
    if isinstance(value, str):
        if value == END_TOKEN:
            return Send(items=(), end=True)
        raise ActionBlockParseError(
            f"{location} 'send' scalar must be '{END_TOKEN}', "
            f"got {value!r}"
        )
    if value is None:
        return Send(items=(), end=False)
    if isinstance(value, list):
        return Send(items=_parse_string_list(value, location, "send"), end=False)
    raise ActionBlockParseError(
        f"{location} 'send' must be a list, the scalar '{END_TOKEN}', "
        f"or null; got {type(value).__name__}"
    )
