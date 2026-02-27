"""Utilities for discovering nano types from disk.

A nano type is a directory in /nanos/ with a config.yaml file.
Types live in git, not the database.
"""
from __future__ import annotations

import os
import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)

NANOS_DIR = os.environ.get("NANOS_DIR", "/nanos")


def safe_resolve(base_dir: str, untrusted_path: str) -> str:
    """Resolve *untrusted_path* under *base_dir*, preventing path traversal.

    Raises ``ValueError`` if the resolved path escapes *base_dir*.
    """
    resolved = os.path.realpath(os.path.join(base_dir, untrusted_path))
    base = os.path.realpath(base_dir)
    if not resolved.startswith(base + os.sep) and resolved != base:
        raise ValueError(f"Path traversal blocked: {untrusted_path!r}")
    return resolved


def load_type(type_name: str) -> dict[str, Any] | None:
    """Read config.yaml for a type from /nanos/{type_name}/config.yaml.

    Returns the parsed config dict, or None if not found.
    """
    try:
        type_dir = safe_resolve(NANOS_DIR, type_name)
    except ValueError:
        logger.warning("Blocked path traversal attempt in type_name: %s", type_name)
        return None
    config_path = os.path.join(type_dir, "config.yaml")
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        config["type_name"] = type_name
        return config  # type: ignore[no-any-return]
    except (OSError, yaml.YAMLError) as e:
        logger.debug("Could not load type '%s': %s", type_name, e)
        return None


def list_types() -> list[dict[str, Any]]:
    """Scan /nanos/*/config.yaml and return all types."""
    types: list[dict[str, Any]] = []
    try:
        entries = sorted(os.listdir(NANOS_DIR))
    except OSError:
        return types

    for entry in entries:
        config_path = os.path.join(NANOS_DIR, entry, "config.yaml")
        if os.path.isfile(config_path):
            config = load_type(entry)
            if config:
                types.append(config)
    return types
