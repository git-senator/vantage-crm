"""A deterministic SDK generator driven by the public OpenAPI document.

Given the public API's own OpenAPI document (`app.developer.spec`), this emits a
complete, versioned client package for a target language. It is deliberately
*deterministic*: the same spec and version produce byte-for-byte the same files,
so a generated SDK can be committed, diffed, and regenerated in CI without noise.

The shape of the output is honest about what a generator can and cannot know:

  * **The runtime is hand-written and stable** — auth, the request pipeline,
    cursor pagination, idempotency keys, and typed errors. These do not vary
    per endpoint, so generating them per endpoint would only add ways to drift.
  * **The resource methods are generated from the operations** — one class per
    tag, one method per operation, with path parameters, query, and request
    bodies wired through to the runtime. Payload shapes are passed through as
    open records rather than invented as fragile typed models: the contract of
    record for a field is the OpenAPI document the SDK ships beside, not a
    second transcription of it that can disagree.

The generator reads operations the same way for every language, then each
renderer turns that neutral operation list into idiomatic source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Where a caller reaches the public API. A generated SDK defaults to this and
#: every constructor lets it be overridden, so the same package works against a
#: self-hosted instance. `app/api/public/v1/params.py` owns the real prefix.
DEFAULT_BASE_PATH = "/api/public/v1"
DEFAULT_BASE_URL = f"https://your-rossa-instance{DEFAULT_BASE_PATH}"

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_PATH_PARAM = re.compile(r"\{([^}]+)\}")


# --------------------------------------------------------------- operations


@dataclass(frozen=True)
class Operation:
    """One neutral, language-agnostic API operation, read from the spec."""

    resource: str
    #: The method name on the resource class (e.g. `list`, `create`, `retrieve`).
    name: str
    http_method: str  # upper-case
    path: str  # e.g. /leads/{lead_id}
    summary: str
    path_params: tuple[str, ...]  # snake_case, in path order
    has_query: bool
    has_body: bool

    @property
    def is_write(self) -> bool:
        return self.http_method in ("POST", "PUT", "PATCH")


@dataclass
class SdkPackage:
    """A rendered SDK: its language, the API version it was generated from, and
    the files that make up the package keyed by their path within it."""

    language: str
    version: str
    package_name: str
    files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SdkTarget:
    language: str
    package_name: str
    description: str


#: The languages the generator emits. Adding one is a new renderer plus a row.
SDK_TARGETS: tuple[SdkTarget, ...] = (
    SdkTarget("typescript", "@rossa/crm", "TypeScript / JavaScript (fetch)"),
    SdkTarget("python", "rossa-crm", "Python 3.11+ (httpx)"),
)
_TARGETS_BY_LANGUAGE = {target.language: target for target in SDK_TARGETS}


def _method_name(http_method: str, path: str) -> str:
    """Derive an idiomatic method name from the HTTP verb and path shape.

    Collection paths (`/leads`) get `list`/`create`; item paths (`/leads/{id}`)
    get `retrieve`/`update`/`delete`; an action segment on an item path
    (`/deals/{id}/stage`, `/webhooks/{id}/deliveries`) becomes a method named
    for the action, so the generated call reads the way the endpoint does.
    """
    segments = [s for s in path.split("/") if s]
    last = segments[-1] if segments else ""
    is_item = last.startswith("{")
    if is_item:
        return {
            "GET": "retrieve",
            "PUT": "update",
            "PATCH": "update",
            "DELETE": "delete",
            "POST": "create",
        }.get(http_method, http_method.lower())
    # A trailing literal segment behind a path parameter is an action/sub-list.
    if any(seg.startswith("{") for seg in segments[:-1]):
        action = re.sub(r"[^a-zA-Z0-9]+", "_", last).strip("_").lower()
        if http_method == "GET":
            return f"list_{action}" if action.endswith("s") else action
        return action
    # A bare collection path.
    return {"GET": "list", "POST": "create"}.get(http_method, http_method.lower())


def extract_operations(spec: dict[str, Any]) -> list[Operation]:
    """Read the spec's paths into a sorted, neutral operation list.

    Sorted by (resource, path, method) so generation is stable regardless of the
    dict order the spec happens to arrive in.
    """
    operations: list[Operation] = []
    paths: dict[str, dict[str, Any]] = spec.get("paths", {})
    for path, item in paths.items():
        for http_method in _HTTP_METHODS:
            operation = item.get(http_method)
            if operation is None:
                continue
            tags = operation.get("tags") or []
            resource = tags[0] if tags else (path.strip("/").split("/")[0] or "root")
            parameters = operation.get("parameters", [])
            has_query = any(p.get("in") == "query" for p in parameters)
            operations.append(
                Operation(
                    resource=_identifier(resource),
                    name=_method_name(http_method.upper(), path),
                    http_method=http_method.upper(),
                    path=path,
                    summary=operation.get("summary", "").strip(),
                    path_params=tuple(_PATH_PARAM.findall(path)),
                    has_query=has_query,
                    has_body="requestBody" in operation,
                )
            )
    operations.sort(key=lambda op: (op.resource, op.path, op.http_method))
    return operations


def _identifier(value: str) -> str:
    """A safe snake_case identifier from an arbitrary tag or segment."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return cleaned or "root"


def _camel(value: str) -> str:
    parts = _identifier(value).split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


def _pascal(value: str) -> str:
    return "".join(word.capitalize() for word in _identifier(value).split("_"))


def _grouped(operations: list[Operation]) -> dict[str, list[Operation]]:
    groups: dict[str, list[Operation]] = {}
    for operation in operations:
        groups.setdefault(operation.resource, []).append(operation)
    return groups


# ------------------------------------------------------------- entry point


def generate_sdk(
    language: str,
    spec: dict[str, Any],
    *,
    version: str,
    base_url: str = DEFAULT_BASE_URL,
) -> SdkPackage:
    """Render a full SDK package for `language` from `spec`.

    Raises `ValueError` for an unknown language — the caller (the portal) turns
    that into a 404, so a request for `/sdks/cobol` is a clean not-found.
    """
    target = _TARGETS_BY_LANGUAGE.get(language)
    if target is None:
        raise ValueError(f"No SDK target for language '{language}'.")

    operations = extract_operations(spec)
    renderer = _RENDERERS[language]
    files = renderer(operations, version=version, base_url=base_url)
    return SdkPackage(
        language=language,
        version=version,
        package_name=target.package_name,
        files=files,
    )


# --------------------------------------------------------------- typescript


def _render_typescript(
    operations: list[Operation], *, version: str, base_url: str
) -> dict[str, str]:
    groups = _grouped(operations)

    resource_classes: list[str] = []
    client_fields: list[str] = []
    client_inits: list[str] = []
    for resource in sorted(groups):
        pascal = _pascal(resource)
        camel = _camel(resource)
        methods: list[str] = []
        for op in groups[resource]:
            methods.append(_ts_method(op))
        resource_classes.append(
            "export class {name}Resource extends Resource {{\n{body}\n}}".format(
                name=pascal, body="\n\n".join(methods)
            )
        )
        client_fields.append(f"  readonly {camel}: {pascal}Resource;")
        client_inits.append(f"    this.{camel} = new {pascal}Resource(this);")

    client = _TS_CLIENT_TEMPLATE.format(
        version=version,
        base_url=base_url,
        fields="\n".join(client_fields),
        inits="\n".join(client_inits),
        resources="\n\n".join(resource_classes),
    )
    return {
        "package.json": _TS_PACKAGE_JSON.format(version=version),
        "README.md": _TS_README.format(version=version, base_url=base_url),
        "src/index.ts": 'export * from "./client";\n',
        "src/client.ts": client,
    }


def _ts_method(op: Operation) -> str:
    args: list[str] = [f"{_camel(p)}: string" for p in op.path_params]
    if op.has_body:
        args.append("body: Record<string, unknown>")
    if op.has_query:
        args.append("query?: Record<string, unknown>")
    if op.is_write:
        args.append("idempotencyKey?: string")

    ts_path = _PATH_PARAM.sub(lambda m: "${" + _camel(m.group(1)) + "}", op.path)
    opts: list[str] = []
    if op.has_query:
        opts.append("query")
    if op.has_body:
        opts.append("body")
    if op.is_write:
        opts.append("idempotencyKey")
    options = f", {{ {', '.join(opts)} }}" if opts else ""

    summary = f"  /** {op.summary} */\n" if op.summary else ""
    return (
        f"{summary}"
        f"  {op.name}({', '.join(args)}): Promise<unknown> {{\n"
        f'    return this.client.request("{op.http_method}", `{ts_path}`{options});\n'
        f"  }}"
    )


_TS_PACKAGE_JSON = """{{
  "name": "@rossa/crm",
  "version": "{version}",
  "description": "TypeScript client for the ROSSA CRM public API.",
  "type": "module",
  "main": "src/index.ts",
  "types": "src/index.ts",
  "license": "UNLICENSED",
  "engines": {{ "node": ">=18" }}
}}
"""

_TS_README = """# @rossa/crm

Generated TypeScript client for the ROSSA CRM public API (v{version}).

```ts
import {{ RossaClient }} from "@rossa/crm";

const rossa = new RossaClient({{ apiKey: process.env.ROSSA_API_KEY! }});

const leads = await rossa.leads.list({{ limit: 25 }});
const lead = await rossa.leads.create(
  {{ first_name: "Ada", last_name: "Lovelace", email: "ada@example.com" }},
  crypto.randomUUID(), // idempotency key
);
```

Base URL defaults to `{base_url}`; override it with the `baseUrl` option for a
self-hosted instance. Every write accepts an idempotency key as its last
argument. This file is generated — regenerate it rather than editing by hand.
"""

_TS_CLIENT_TEMPLATE = '''// Generated by the ROSSA CRM SDK generator. Do not edit by hand.
// Public API version: {version}

export const SDK_VERSION = "{version}";
export const DEFAULT_BASE_URL = "{base_url}";

export interface ClientOptions {{
  /** A live or sandbox API key (`vk_...`). */
  apiKey: string;
  /** Override the API base URL for a self-hosted instance. */
  baseUrl?: string;
  /** Inject a fetch implementation (defaults to the global `fetch`). */
  fetch?: typeof fetch;
}}

export interface PageMeta {{
  next_cursor: string | null;
  has_more: boolean;
  limit: number;
}}

export interface Page<T> {{
  data: T[];
  meta: PageMeta;
}}

/** An RFC 9457 problem document, thrown for any non-2xx response. */
export class RossaError extends Error {{
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown) {{
    super(`ROSSA API error ${{status}}`);
    this.name = "RossaError";
    this.status = status;
    this.detail = detail;
  }}
}}

interface RequestOptions {{
  query?: Record<string, unknown>;
  body?: Record<string, unknown>;
  idempotencyKey?: string;
}}

export class RossaClient {{
{fields}

  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions) {{
    this.apiKey = options.apiKey;
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\\/$/, "");
    this.fetchImpl = options.fetch ?? fetch;
{inits}
  }}

  async request(
    method: string,
    path: string,
    options: RequestOptions = {{}},
  ): Promise<unknown> {{
    const url = new URL(this.baseUrl + path);
    for (const [key, value] of Object.entries(options.query ?? {{}})) {{
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }}
    const headers: Record<string, string> = {{
      "X-API-Key": this.apiKey,
      "Accept": "application/json",
      "X-Rossa-SDK": `typescript/${{SDK_VERSION}}`,
    }};
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

    const response = await this.fetchImpl(url.toString(), {{
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    }});

    if (response.status === 204) return null;
    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;
    if (!response.ok) throw new RossaError(response.status, payload);
    return payload;
  }}
}}

abstract class Resource {{
  constructor(protected readonly client: RossaClient) {{}}
}}

{resources}
'''


# ------------------------------------------------------------------- python


def _render_python(
    operations: list[Operation], *, version: str, base_url: str
) -> dict[str, str]:
    groups = _grouped(operations)

    resource_classes: list[str] = []
    client_fields: list[str] = []
    for resource in sorted(groups):
        pascal = _pascal(resource)
        snake = _identifier(resource)
        methods = [_py_method(op) for op in groups[resource]]
        resource_classes.append(
            "class {name}Resource(_Resource):\n{body}".format(
                name=pascal, body="\n\n".join(methods)
            )
        )
        client_fields.append(f"        self.{snake} = {pascal}Resource(self)")

    client = _PY_CLIENT_TEMPLATE.format(
        version=version,
        base_url=base_url,
        inits="\n".join(client_fields),
        resources="\n\n\n".join(resource_classes),
    )
    return {
        "pyproject.toml": _PY_PYPROJECT.format(version=version),
        "README.md": _PY_README.format(version=version, base_url=base_url),
        "rossa_crm/__init__.py": _PY_INIT.format(version=version),
        "rossa_crm/client.py": client,
    }


def _py_method(op: Operation) -> str:
    params: list[str] = ["self"]
    params.extend(f"{p}: str" for p in op.path_params)
    if op.has_body:
        params.append("body: dict[str, object]")
    if op.has_query:
        params.append("query: dict[str, object] | None = None")
    if op.is_write:
        params.append("idempotency_key: str | None = None")

    py_path = _PATH_PARAM.sub(lambda m: "{" + m.group(1) + "}", op.path)
    path_expr = f'f"{py_path}"' if op.path_params else f'"{py_path}"'

    call_args = [f'"{op.http_method}"', path_expr]
    if op.has_query:
        call_args.append("query=query")
    if op.has_body:
        call_args.append("body=body")
    if op.is_write:
        call_args.append("idempotency_key=idempotency_key")

    doc = f'        """{op.summary}"""\n' if op.summary else ""
    signature = f"    def {op.name}(" + ", ".join(params) + ") -> object:"
    body = f"        return self._client.request({', '.join(call_args)})"
    return f"{signature}\n{doc}{body}"


_PY_PYPROJECT = '''[project]
name = "rossa-crm"
version = "{version}"
description = "Python client for the ROSSA CRM public API."
requires-python = ">=3.11"
dependencies = ["httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
'''

_PY_README = '''# rossa-crm

Generated Python client for the ROSSA CRM public API (v{version}).

```python
from rossa_crm import RossaClient

rossa = RossaClient(api_key="vk_...")

leads = rossa.leads.list(query={{"limit": 25}})
lead = rossa.leads.create(
    {{"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com"}},
    idempotency_key="a-unique-value",
)
```

Base URL defaults to `{base_url}`; pass `base_url=` for a self-hosted instance.
Every write accepts an `idempotency_key`. This package is generated — regenerate
it rather than editing by hand.
'''

_PY_INIT = '''"""ROSSA CRM Python client (generated, API v{version})."""

from rossa_crm.client import Page, RossaClient, RossaError

__all__ = ["Page", "RossaClient", "RossaError"]
'''

_PY_CLIENT_TEMPLATE = '''"""Generated by the ROSSA CRM SDK generator. Do not edit by hand.

Public API version: {version}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

SDK_VERSION = "{version}"
DEFAULT_BASE_URL = "{base_url}"


@dataclass
class Page:
    """A cursor-paginated response envelope."""

    data: list[dict[str, Any]]
    meta: dict[str, Any]


class RossaError(Exception):
    """Raised for any non-2xx response; carries the RFC 9457 problem body."""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"ROSSA API error {{status}}")
        self.status = status
        self.detail = detail


class RossaClient:
    """A synchronous client for the ROSSA CRM public API."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        http: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client()
{inits}

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> object:
        headers = {{
            "X-API-Key": self._api_key,
            "Accept": "application/json",
            "X-Rossa-SDK": f"python/{{SDK_VERSION}}",
        }}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = self._http.request(
            method,
            self._base_url + path,
            params={{k: v for k, v in (query or {{}}).items() if v is not None}},
            json=body,
            headers=headers,
        )
        if response.status_code == 204:
            return None
        payload = response.json() if response.content else None
        if response.status_code >= 400:
            raise RossaError(response.status_code, payload)
        return payload


class _Resource:
    def __init__(self, client: RossaClient) -> None:
        self._client = client


{resources}
'''


_RENDERERS = {
    "typescript": _render_typescript,
    "python": _render_python,
}


__all__ = [
    "DEFAULT_BASE_PATH",
    "DEFAULT_BASE_URL",
    "SDK_TARGETS",
    "Operation",
    "SdkPackage",
    "SdkTarget",
    "extract_operations",
    "generate_sdk",
]
