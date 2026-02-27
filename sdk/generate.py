"""Code-generation script for the Nanos SDK.

Fetches the OpenAPI JSON schema from the gateway and generates:
    - ``nanos_sdk/models.py``  — Pydantic model classes for every component schema.
    - ``nanos_sdk/client.py``  — A typed client class with one method per endpoint.

Usage::

    python sdk/generate.py
    python sdk/generate.py --url http://localhost:8000/openapi.json
    python sdk/generate.py --output ./nanos_sdk
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import click
import httpx


# ------------------------------------------------------------------ #
# OpenAPI type -> Python type mapping
# ------------------------------------------------------------------ #

_OPENAPI_TYPE_MAP: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict[str, Any]",
}


def _python_type(schema: dict[str, Any], required: bool = True, prefix_models: bool = False) -> str:
    """Convert an OpenAPI property schema to a Python type hint string.

    When *prefix_models* is True, ``$ref`` types get a ``models.`` prefix
    (used for return types in the generated client).
    """
    type_str: str
    if "$ref" in schema:
        ref_name: str = schema["$ref"].rsplit("/", 1)[-1]
        type_str = f"models.{ref_name}" if prefix_models else ref_name
    elif schema.get("type") == "array":
        items = schema.get("items", {})
        inner = _python_type(items, prefix_models=prefix_models)
        type_str = f"list[{inner}]"
    elif "allOf" in schema:
        # Take the first $ref in allOf
        for sub in schema["allOf"]:
            if "$ref" in sub:
                return _python_type(sub, required, prefix_models=prefix_models)
        type_str = "Any"
    elif "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf", [])
        types = [_python_type(v, prefix_models=prefix_models) for v in variants if v.get("type") != "null"]
        if len(types) == 1:
            type_str = types[0]
        else:
            type_str = " | ".join(types)
    else:
        type_str = _OPENAPI_TYPE_MAP.get(schema.get("type", ""), "Any")

    if not required:
        type_str = f"{type_str} | None"
    return type_str


def _snake_case(name: str) -> str:
    """Convert a CamelCase or kebab-case name to snake_case."""
    import re

    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s2 = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s1)
    return s2.replace("-", "_").lower()


def _method_name_from_path(method: str, path: str) -> str:
    """Derive a Python method name from the HTTP method and path.

    Example: ``POST /gmail/messages/send`` -> ``gmail_messages_send``
             ``GET  /calendar/events``      -> ``calendar_list_events``
    """
    parts = [p for p in path.strip("/").split("/") if not p.startswith("{")]
    if method.lower() == "get" and parts:
        parts.insert(-1 if len(parts) > 1 else 0, "list")
    name = "_".join(_snake_case(p) for p in parts)
    return name or "root"


# ------------------------------------------------------------------ #
# Model generation
# ------------------------------------------------------------------ #

_MODELS_HEADER = textwrap.dedent(
    '''\
    """Auto-generated Pydantic models — regenerate with: python sdk/generate.py"""
    # This file is auto-generated from the gateway\'s OpenAPI schema.
    # Do not edit manually. Run `python sdk/generate.py` to regenerate.

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, Field


    '''
)


def _generate_model(name: str, schema: dict[str, Any]) -> str:
    """Generate a single Pydantic model class."""
    required_fields = set(schema.get("required", []))
    properties: dict[str, dict[str, Any]] = schema.get("properties", {})

    lines: list[str] = []
    description = schema.get("description", f"Auto-generated model for {name}.")
    lines.append(f"class {name}(BaseModel):")
    lines.append(f'    """{description}"""')
    lines.append("")

    if not properties:
        lines.append("    pass")
        lines.append("")
        return "\n".join(lines)

    import keyword
    needs_model_config = False
    for prop_name, prop_schema in properties.items():
        is_required = prop_name in required_fields
        py_type = _python_type(prop_schema, required=is_required)
        prop_desc = prop_schema.get("description", "")
        comment = f"  # {prop_desc}" if prop_desc else ""
        # Escape Python keywords (e.g. "from" -> "from_" with Field(alias="from"))
        field_name = prop_name
        if keyword.iskeyword(prop_name):
            field_name = f"{prop_name}_"
            needs_model_config = True
            if is_required:
                default = f" = Field(alias=\"{prop_name}\")"
            else:
                default = f" = Field(default=None, alias=\"{prop_name}\")"
        else:
            default = "" if is_required else " = None"
        lines.append(f"    {field_name}: {py_type}{default}{comment}")
    if needs_model_config:
        lines.insert(2, "    model_config = {'populate_by_name': True}")
        lines.insert(3, "")

    lines.append("")
    return "\n".join(lines)


def generate_models(schemas: dict[str, dict[str, Any]]) -> str:
    """Generate the full models.py source."""
    parts = [_MODELS_HEADER]
    for name, schema in schemas.items():
        if schema.get("type") == "object" or "properties" in schema:
            parts.append(_generate_model(name, schema))
            parts.append("")
    return "\n".join(parts)


# ------------------------------------------------------------------ #
# Client generation
# ------------------------------------------------------------------ #

_CLIENT_HEADER = textwrap.dedent(
    '''\
    """Auto-generated typed client — regenerate with: python sdk/generate.py"""
    # This file is auto-generated from the gateway\'s OpenAPI schema.
    # Do not edit manually. Run `python sdk/generate.py` to regenerate.

    from __future__ import annotations

    from typing import Any

    from nanos_sdk._base import NanosClient as _BaseClient
    from nanos_sdk import models


    class NanosTypedClient(_BaseClient):
        """Typed client auto-generated from the gateway OpenAPI schema."""

    '''
)



# Headers that the base client handles internally — never expose as method params
_INTERNAL_HEADERS = {"X-Nano-Key", "X-Draft-Mode", "X-Nano-Run-Log-Id", "X-Admin-Key"}

# Body fields injected by the approval middleware — not user-facing
_INTERNAL_BODY_FIELDS = {"explanation", "reasoning", "wait_until_date"}


def _clean_operation_id(raw_id: str) -> str:
    """Extract the clean function name from a FastAPI operationId.

    FastAPI generates operationIds like ``gmail_messages_list_api_gmail_messages_get``.
    The clean name is everything before the ``_api_`` suffix.
    """
    if "_api_" in raw_id:
        return raw_id.split("_api_")[0]
    return raw_id


def _generate_method(
    method: str,
    path: str,
    operation: dict[str, Any],
    schemas: dict[str, dict[str, Any]],
) -> str:
    """Generate a single client method from an OpenAPI operation."""
    import re

    raw_id = operation.get("operationId") or _method_name_from_path(method, path)
    func_name = _snake_case(_clean_operation_id(raw_id))
    summary = operation.get("summary", "")
    description = operation.get("description", summary)

    # Identify path parameters from URL template (e.g. {message_id})
    path_param_names = set(re.findall(r"\{(\w+)\}", path))

    # Build the resolved path expression.
    # If path has params like "/api/gmail/messages/{message_id}", produce
    # an f-string: f"/api/gmail/messages/{message_id}"
    if path_param_names:
        path_expr = f'f"{path}"'
    else:
        path_expr = f'"{path}"'

    # Collect parameters — skip internal headers, separate path vs query
    path_params: list[tuple[str, str, str | None]] = []
    query_params: list[tuple[str, str, str | None]] = []
    for param in operation.get("parameters", []):
        pname = param["name"]
        if pname in _INTERNAL_HEADERS:
            continue
        pschema = param.get("schema", {})
        required = param.get("required", False)
        py_type = _python_type(pschema, required=required)
        default = None if required else repr(pschema.get("default"))
        if default == "None" or (not required and default is None):
            default = "None"
        entry = (pname, py_type, default)
        if pname in path_param_names or param.get("in") == "path":
            path_params.append(entry)
        else:
            query_params.append(entry)

    # Collect request body fields
    body_fields: list[tuple[str, str, str | None]] = []
    body_ref: str | None = None
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {})
    json_content = content.get("application/json", {})
    body_schema = json_content.get("schema", {})

    if "$ref" in body_schema:
        body_ref = body_schema["$ref"].rsplit("/", 1)[-1]
        resolved = schemas.get(body_ref, {})
        required_body = set(resolved.get("required", []))
        for fname, fschema in resolved.get("properties", {}).items():
            if fname in _INTERNAL_BODY_FIELDS:
                continue
            is_req = fname in required_body
            ftype = _python_type(fschema, required=is_req)
            fdefault = None if is_req else "None"
            body_fields.append((fname, ftype, fdefault))
    elif body_schema.get("properties"):
        required_body = set(body_schema.get("required", []))
        for fname, fschema in body_schema["properties"].items():
            if fname in _INTERNAL_BODY_FIELDS:
                continue
            is_req = fname in required_body
            ftype = _python_type(fschema, required=is_req)
            fdefault = None if is_req else "None"
            body_fields.append((fname, ftype, fdefault))

    # Determine return type — detect if response is a Pydantic model ($ref)
    responses = operation.get("responses", {})
    success = responses.get("200") or responses.get("201") or responses.get("202")
    is_approval = "202" in responses and "200" not in responses and "201" not in responses
    return_type = "dict[str, Any]"
    model_name: str | None = None       # single $ref model
    list_model_name: str | None = None  # array of $ref models
    is_list_response = False            # any array response (for _get_list)
    if is_approval:
        # 202-only endpoints return ApprovalCreatedResponse (approval_id + status)
        model_name = "ApprovalCreatedResponse"
        return_type = "models.ApprovalCreatedResponse"
    elif success:
        resp_content = success.get("content", {}).get("application/json", {})
        resp_schema = resp_content.get("schema", {})
        if "$ref" in resp_schema:
            model_name = resp_schema["$ref"].rsplit("/", 1)[-1]
            return_type = f"models.{model_name}"
        elif resp_schema.get("type") == "array":
            is_list_response = True
            if "$ref" in resp_schema.get("items", {}):
                list_model_name = resp_schema["items"]["$ref"].rsplit("/", 1)[-1]
                return_type = f"list[models.{list_model_name}]"
            else:
                return_type = _python_type(resp_schema, prefix_models=True)
        elif resp_schema:
            return_type = _python_type(resp_schema, prefix_models=True)

    # Build the method signature
    sig_parts: list[str] = ["self"]
    all_params: list[tuple[str, str, str | None]] = []
    all_params.extend(path_params)
    all_params.extend(query_params)
    all_params.extend(body_fields)

    # Sort: required first, optional last
    required_p = [(n, t, d) for n, t, d in all_params if d is None]
    optional_p = [(n, t, d) for n, t, d in all_params if d is not None]

    for pname, ptype, _ in required_p:
        sig_parts.append(f"{pname}: {ptype}")
    for pname, ptype, pdefault in optional_p:
        sig_parts.append(f"{pname}: {ptype} = {pdefault}")

    signature = ", ".join(sig_parts)

    # Build method body
    lines: list[str] = []
    lines.append(f"    def {func_name}({signature}) -> {return_type}:")
    if description:
        lines.append(f'        """{description}"""')

    http_method = method.upper()

    # Choose _get vs _get_list based on whether response is an array
    get_method = "_get_list" if is_list_response else "_get"

    # Helper: wrap a raw API call expression with model construction if needed
    def _wrap(raw_expr: str) -> str:
        if model_name:
            return f"models.{model_name}(**{raw_expr})"
        if list_model_name:
            return f"[models.{list_model_name}(**item) for item in {raw_expr}]"
        return raw_expr

    if body_fields:
        field_names = [f[0] for f in body_fields]
        body_items = ", ".join(f'"{fn}": {fn}' for fn in field_names)
        lines.append(f"        payload = {{{body_items}}}")
        lines.append("        payload = {k: v for k, v in payload.items() if v is not None}")
        raw = f"self._post({path_expr}, payload)"
        lines.append(f"        return {_wrap(raw)}")
    elif query_params:
        qp_items = ", ".join(f"{pn}={pn}" for pn in [p[0] for p in query_params])
        raw = f"self.{get_method}({path_expr}, {qp_items})"
        lines.append(f"        return {_wrap(raw)}")
    else:
        if http_method == "GET":
            raw = f"self.{get_method}({path_expr})"
        elif http_method == "DELETE":
            raw = f"self._delete({path_expr})"
        else:
            raw = f"self._post({path_expr})"
        lines.append(f"        return {_wrap(raw)}")

    lines.append("")
    return "\n".join(lines)


# Paths that should not become SDK methods (admin, auth, internal)
_SKIP_PATH_PREFIXES = ("/api/admin", "/api/test", "/api/health", "/api/nano-key",
                        "/api/whatsapp/auth", "/api/whatsapp/sync")


def generate_client(
    paths: dict[str, dict[str, Any]],
    schemas: dict[str, dict[str, Any]],
    base_methods: set[str] | None = None,
) -> str:
    """Generate the full client.py source.

    If *base_methods* is provided, skip generating methods that already
    exist in the hand-written _base.py (avoids duplicates).
    """
    parts = [_CLIENT_HEADER]
    base_methods = base_methods or set()

    for path, methods in paths.items():
        if any(path.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            if method not in methods:
                continue
            operation = methods[method]
            raw_id = operation.get("operationId") or _method_name_from_path(method, path)
            func_name = _snake_case(_clean_operation_id(raw_id))
            if func_name in base_methods:
                continue  # already hand-written in _base.py
            parts.append(_generate_method(method, path, operation, schemas))
            parts.append("")

    # Alias so `from nanos_sdk.client import NanosClient` works
    parts.append("")
    parts.append("# Public alias — __init__.py imports NanosClient from here")
    parts.append("NanosClient = NanosTypedClient")
    parts.append("")

    return "\n".join(parts)


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #

@click.command()
@click.option(
    "--url",
    default="http://localhost:8000/openapi.json",
    show_default=True,
    help="URL of the gateway's OpenAPI JSON schema.",
)
@click.option(
    "--output",
    default=None,
    type=click.Path(),
    help="Output directory for generated files (default: sdk/nanos_sdk/).",
)
def main(url: str, output: str | None) -> None:
    """Fetch the OpenAPI schema and generate typed SDK files."""
    click.echo(f"Fetching OpenAPI schema from {url} ...")
    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()
    spec = response.json()

    schemas: dict[str, dict[str, Any]] = (
        spec.get("components", {}).get("schemas", {})
    )
    paths: dict[str, dict[str, Any]] = spec.get("paths", {})

    click.echo(f"Found {len(schemas)} schemas and {len(paths)} paths.")

    # Determine output directory
    if output:
        out_dir = Path(output)
    else:
        out_dir = Path(__file__).resolve().parent / "nanos_sdk"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Generate models
    models_src = generate_models(schemas)
    models_path = out_dir / "models.py"
    models_path.write_text(models_src, encoding="utf-8")
    click.echo(f"Wrote {models_path}")

    # Discover base client methods to avoid generating duplicates
    import inspect as _inspect
    import importlib.util as _ilu
    base_path = out_dir / "_base.py"
    base_methods: set[str] = set()
    if base_path.exists():
        spec_mod = _ilu.spec_from_file_location("_base", base_path)
        if spec_mod and spec_mod.loader:
            mod = _ilu.module_from_spec(spec_mod)
            spec_mod.loader.exec_module(mod)
            base_cls = getattr(mod, "NanosClient", None)
            if base_cls:
                base_methods = {
                    name for name in dir(base_cls)
                    if not name.startswith("_") and callable(getattr(base_cls, name))
                }
                click.echo(f"Found {len(base_methods)} existing base methods — skipping duplicates.")

    # Generate client
    client_src = generate_client(paths, schemas, base_methods=base_methods)
    client_path = out_dir / "client.py"
    client_path.write_text(client_src, encoding="utf-8")
    click.echo(f"Wrote {client_path}")

    click.echo("Done.")


if __name__ == "__main__":
    main()
