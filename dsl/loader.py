"""Strict YAML loading for strategy files.

Rejects anchors, aliases, explicit tags and duplicate keys (DSL rule 1).
"""
from __future__ import annotations

import hashlib

import yaml


class DSLError(ValueError):
    pass


class _StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise DSLError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_text(text: str) -> dict:
    for tok in yaml.scan(text):
        if isinstance(tok, yaml.AnchorToken):
            raise DSLError(f"YAML anchors are not allowed (line {tok.start_mark.line + 1})")
        if isinstance(tok, yaml.AliasToken):
            raise DSLError(f"YAML aliases are not allowed (line {tok.start_mark.line + 1})")
        if isinstance(tok, yaml.TagToken):
            raise DSLError(f"YAML tags are not allowed (line {tok.start_mark.line + 1})")
    try:
        data = yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - strict SafeLoader subclass
    except yaml.YAMLError as e:
        raise DSLError(f"invalid YAML: {e}") from None
    if not isinstance(data, dict):
        raise DSLError("strategy file must be a mapping")
    return data


def file_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
