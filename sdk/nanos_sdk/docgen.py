"""Auto-generate SDK API reference by introspecting NanosClient and models.

Usage:
    from nanos_sdk.docgen import SDK_REFERENCE
    # or
    from nanos_sdk.docgen import generate_sdk_reference
    ref = generate_sdk_reference()

The output is a markdown string suitable for inclusion in an LLM system prompt.
"""
from __future__ import annotations

import inspect
import typing
from typing import Any, get_type_hints


def _format_type(tp: Any) -> str:
    """Convert a type annotation to a readable string."""
    # Resolve ForwardRef (from `from __future__ import annotations`)
    if isinstance(tp, typing.ForwardRef):
        s = tp.__forward_arg__
        for prefix in ("nanos_sdk.models.", "nanos_sdk._base.", "nanos_sdk.client."):
            s = s.replace(prefix, "")
        return s
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)

    if tp is type(None):
        return "None"
    if tp is inspect.Parameter.empty or tp is Any:
        return "Any"
    if origin is list:
        if args:
            return f"list[{_format_type(args[0])}]"
        return "list"
    if origin is dict:
        if args:
            return f"dict[{_format_type(args[0])}, {_format_type(args[1])}]"
        return "dict"
    if origin is typing.Union:
        parts = [_format_type(a) for a in args]
        # Handle Optional[X] = Union[X, None]
        if len(parts) == 2 and "None" in parts:
            non_none = [p for p in parts if p != "None"][0]
            return f"{non_none} | None"
        return " | ".join(parts)
    if hasattr(tp, "__name__"):
        return str(tp.__name__)
    # Strip module paths from string representations (e.g. nanos_sdk.models.Foo -> Foo)
    s = str(tp).replace("typing.", "")
    # Remove known module prefixes
    for prefix in ("nanos_sdk.models.", "nanos_sdk._base.", "nanos_sdk.client."):
        s = s.replace(prefix, "")
    return s


def _resolve_forward_ref(tp: Any) -> Any:
    """Resolve a ForwardRef to its actual type if possible."""
    if isinstance(tp, typing.ForwardRef):
        from nanos_sdk import models
        name = tp.__forward_arg__
        # Strip module prefix
        for prefix in ("nanos_sdk.models.", "models."):
            if name.startswith(prefix):
                name = name[len(prefix):]
        resolved = getattr(models, name, None)
        if resolved is not None:
            return resolved
    return tp


def _is_pydantic_model(tp: Any) -> bool:
    """Check if tp is a Pydantic BaseModel subclass."""
    tp = _resolve_forward_ref(tp)
    try:
        from pydantic import BaseModel
        return isinstance(tp, type) and issubclass(tp, BaseModel) and tp is not BaseModel
    except ImportError:
        return False


def _get_inner_type(tp: Any) -> Any | None:
    """If tp is list[X], return X. Otherwise None."""
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is list and args:
        return _resolve_forward_ref(args[0])
    return None


def _describe_model_fields(model_cls: Any, indent: str = "  ") -> str:
    """Describe a Pydantic model's fields as markdown lines."""
    lines = []
    for name, field_info in model_cls.model_fields.items():
        ft = _format_type(field_info.annotation) if field_info.annotation else "Any"
        alias_note = ""
        if field_info.alias and field_info.alias != name:
            alias_note = f" (alias: \"{field_info.alias}\")"
        lines.append(f"{indent}- `{name}`: `{ft}`{alias_note}")

        # If this field itself is a Pydantic model or list of models, expand one level
        inner = _get_inner_type(field_info.annotation) if field_info.annotation else None
        resolved_annotation = _resolve_forward_ref(field_info.annotation) if field_info.annotation else None
        if inner and _is_pydantic_model(inner):
            for sub_name, sub_fi in inner.model_fields.items():
                sft = _format_type(sub_fi.annotation) if sub_fi.annotation else "Any"
                lines.append(f"{indent}  - `{sub_name}`: `{sft}`")
        elif resolved_annotation and _is_pydantic_model(resolved_annotation):
            for sub_name, sub_fi in resolved_annotation.model_fields.items():
                sft = _format_type(sub_fi.annotation) if sub_fi.annotation else "Any"
                lines.append(f"{indent}  - `{sub_name}`: `{sft}`")

    return "\n".join(lines)


# Maps method name prefixes to API group headers
_API_GROUPS = [
    ("openai_", "OpenAI"),
    ("calendar_", "Google Calendar"),
    ("gmail_", "Gmail"),
    ("slack_", "Slack"),
    ("hubspot_", "HubSpot CRM"),
    ("whatsapp_", "WhatsApp"),
    ("notion_", "Notion"),
    ("linear_", "Linear"),
    ("state_", "State Store"),
    ("approval_status", "Approvals"),
    ("wait_for_approval", "Approvals"),
]

# Exact method name -> group overrides for auto-generated methods
# that lack an API prefix (e.g. list_events instead of calendar_list_events)
_METHOD_GROUP_OVERRIDES: dict[str, str] = {
    "list_events": "Google Calendar",
    "create_event": "Google Calendar",
    "update_event": "Google Calendar",
    "delete_event": "Google Calendar",
    "send_message": "Slack",
}

# Methods to skip — internal transport helpers and dunder
_SKIP_PREFIXES = ("_", "close")

# Special properties that should be documented
_PROPERTIES = ("parameters",)


def _get_group(method_name: str) -> str:
    """Determine which API group a method belongs to."""
    if method_name in _METHOD_GROUP_OVERRIDES:
        return _METHOD_GROUP_OVERRIDES[method_name]
    for prefix, group in _API_GROUPS:
        if method_name.startswith(prefix):
            return group
    return "Other"


def _format_signature(name: str, sig: inspect.Signature, hints: dict[str, Any]) -> str:
    """Format a method signature as a readable string."""
    params = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        hint = hints.get(pname)
        type_str = f": {_format_type(hint)}" if hint else ""
        if param.default is not inspect.Parameter.empty:
            default = repr(param.default)
            params.append(f"{pname}{type_str} = {default}")
        elif param.kind == inspect.Parameter.VAR_KEYWORD:
            params.append(f"**{pname}")
        else:
            params.append(f"{pname}{type_str}")

    ret = hints.get("return")
    ret_str = f" -> {_format_type(ret)}" if ret else ""
    return f"client.{name}({', '.join(params)}){ret_str}"


def generate_sdk_reference() -> str:
    """Generate a complete SDK API reference by introspecting the actual code.

    Returns a markdown string documenting all public methods, their signatures,
    return types, and Pydantic model field listings.

    Deduplication: when the auto-generated typed client has methods for an API
    group (e.g. gmail), the hand-written base methods for that group are
    skipped to avoid confusing the LLM with two methods that do the same
    thing but have different parameter names.
    """
    # Import here to avoid circular imports / side effects at module top level
    from nanos_sdk.client import NanosClient as TypedClient
    from nanos_sdk._base import NanosClient as BaseClient

    lines: list[str] = []
    lines.append("## Complete SDK API Reference")
    lines.append("")
    lines.append("All methods are on `client = NanosClient()`.")
    lines.append("")
    lines.append(
        "**IMPORTANT: The SDK returns Pydantic model objects, NOT plain dicts.** "
        "Use attribute access (e.g. `result.email_address`), NOT dict access "
        "(e.g. `result[\"email\"]` or `result.get(\"email\")`). "
        "The field names use snake_case. If you need a dict, call `.model_dump()` on the object."
    )

    # Methods defined DIRECTLY on the typed client (auto-generated)
    typed_direct: set[str] = {
        name for name in TypedClient.__dict__
        if not any(name.startswith(p) for p in _SKIP_PREFIXES)
        and callable(getattr(TypedClient, name, None))
    }

    # Methods defined DIRECTLY on the base client (hand-written)
    base_direct: set[str] = {
        name for name in BaseClient.__dict__
        if not any(name.startswith(p) for p in _SKIP_PREFIXES)
        and callable(getattr(BaseClient, name, None))
        and name not in ("get_parameter",)
    }

    # Skip these auto-generated methods — base has better versions
    _TYPED_SKIP = {"get_parameter", "state_get", "state_set"}

    # Build the final method list.
    # Include all typed methods (except skipped ones), then add base methods
    # that don't have a same-name counterpart in the typed client.
    all_methods: dict[str, tuple[Any, type]] = {}

    typed_included: set[str] = set()
    for name in sorted(typed_direct):
        if name in _TYPED_SKIP:
            continue
        all_methods[name] = (getattr(TypedClient, name), TypedClient)
        typed_included.add(name)

    for name in sorted(base_direct):
        if name in typed_included:
            continue
        all_methods[name] = (getattr(BaseClient, name), BaseClient)

    # Group methods
    groups: dict[str, list[str]] = {}
    for name in all_methods:
        group = _get_group(name)
        groups.setdefault(group, []).append(name)

    # Define group order
    group_order = [
        "OpenAI", "Google Calendar", "Gmail", "Slack",
        "HubSpot CRM", "WhatsApp", "Notion", "Linear",
        "State Store", "Approvals", "Other",
    ]

    for group in group_order:
        method_names = groups.get(group, [])
        if not method_names:
            continue

        lines.append("")
        lines.append(f"### {group}")
        lines.append("")

        for name in sorted(method_names):
            method, owner_cls = all_methods[name]
            try:
                sig = inspect.signature(method)
            except (ValueError, TypeError):
                continue

            try:
                hints = get_type_hints(method)
            except Exception:
                hints = {}

            sig_str = _format_signature(name, sig, hints)
            docstring = inspect.getdoc(method) or ""
            # Take first line of docstring as description
            desc = docstring.split("\n")[0] if docstring else ""

            lines.append(f"**`{sig_str}`**")
            if desc:
                lines.append(f"{desc}")

            # Document return type fields if it's a Pydantic model
            ret_type = hints.get("return")
            if ret_type:
                inner = _get_inner_type(ret_type)
                if inner and _is_pydantic_model(inner):
                    lines.append(f"Returns list of `{inner.__name__}` objects:")
                    lines.append(_describe_model_fields(inner))
                elif _is_pydantic_model(ret_type):
                    lines.append(f"`{ret_type.__name__}` fields:")
                    lines.append(_describe_model_fields(ret_type))

            lines.append("")

    # Add Parameters section (property + get_parameter)
    lines.append("### Parameters")
    lines.append("")
    lines.append("```python")
    lines.append("client.parameters -> dict            # All parameters as dict")
    lines.append("client.get_parameter(key: str, default=None) -> Any  # Single parameter by key")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# Pre-generate at import time — the SDK doesn't change at runtime
SDK_REFERENCE: str = generate_sdk_reference()


if __name__ == "__main__":
    print(SDK_REFERENCE)
