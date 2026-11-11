"""Public JSON records, errors, and structural integration interfaces."""
from __future__ import annotations

from . import __version__

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
import hashlib
import json


class ConversationError(Exception):
    def __init__(self, code, message, *, uncertain=False, operation_id=None, recovery=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.uncertain, self.operation_id = uncertain, operation_id
        self.recovery = recovery or {}

    def to_dict(self):
        return dict(code=self.code, message=self.message, uncertain=self.uncertain,
                    operation_id=self.operation_id, recovery=self.recovery)


def canonical(value):
    def check_keys(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ConversationError("validation", "JSON object keys must be strings")
            for child in item.values():
                check_keys(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check_keys(child)
    check_keys(value)
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConversationError("validation", "Expected finite UTF-8 JSON") from exc


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def decode(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ConversationError("validation", "Duplicate JSON key")
            out[key] = value
        return out
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        canonical(value)
        return value
    except (ValueError, UnicodeError) as exc:
        raise ConversationError("validation", "Invalid finite UTF-8 JSON") from exc


SECRET_KEYS = {"env", "environment", "authorization", "api_key", "apikey", "api-key",
               "token", "access_token", "refresh_token", "password", "secret", "credentials"}


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: "[redacted]" if k.lower() in SECRET_KEYS else redact(v, secrets)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
    return value


def reject_credentials(value):
    if isinstance(value, dict):
        for k, v in value.items():
            if k.lower() in SECRET_KEYS and v not in ({}, [], None):
                raise ConversationError("validation", "Use transient env and credential references for credentials")
            reject_credentials(v)
    elif isinstance(value, list):
        for v in value:
            reject_credentials(v)


class Record(dict):
    def to_dict(self):
        return dict(self)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class ConnectionRef(Record):
    pass


class SessionRef(Record):
    pass


class OperationReceipt(Record):
    pass


@dataclass
class PreparedLaunch:
    backend_id: str
    adapter_id: str
    argv: list[str]
    cwd: str
    protocol: str = "codex-app-server"
    connection_id: str | None = None
    transport: dict = field(default_factory=lambda: {"kind": "stdio"})
    retention: str = "retained"
    client_info: dict = field(default_factory=lambda: {"name": "conversation_manager", "version": __version__})
    settings: dict = field(default_factory=dict)
    resource_snapshot_ref: Any = None
    resource_policy: Any = None
    credential_refs: list = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict, repr=False)
    services: Any = field(default=None, repr=False)

    def to_dict(self):
        if self.protocol not in ("codex-app-server", "claude-agent-sdk", "antigravity-sdk", "copilot-sdk", "opencode-v2") or self.retention != "retained":
            raise ConversationError("unsupported", "Unknown retained backend protocol")
        if not isinstance(self.backend_id, str) or not self.backend_id or not isinstance(self.adapter_id, str) or not self.adapter_id or not isinstance(self.argv, list):
            raise ConversationError("validation", "Backend, adapter and argv are required")
        if not isinstance(self.cwd, str) or not self.cwd or not isinstance(self.transport, dict) or not isinstance(self.settings, dict) or not isinstance(self.client_info, dict):
            raise ConversationError("validation", "cwd, transport, settings and client_info have invalid types")
        if self.connection_id is not None and (not isinstance(self.connection_id, str) or not self.connection_id):
            raise ConversationError("validation", "connection_id must be an opaque nonempty string")
        if not isinstance(self.env, dict) or any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k+v or "=" in k for k, v in self.env.items()):
            raise ConversationError("validation", "Transient env must map valid variable names to strings")
        if any(not isinstance(x, str) or "\0" in x for x in self.argv):
            raise ConversationError("validation", "argv must contain strings without NUL")
        if self.transport.get('kind') == 'managed-codex':
            target = self.transport.get('agent_environment_ref')
            if (self.protocol != 'codex-app-server' or self.backend_id != 'codex'
                    or self.adapter_id != 'native-codex' or self.argv
                    or not isinstance(target, dict)
                    or target.get('schema') != 'agent-manager/native-environment-1'
                    or target.get('provider') != 'codex'):
                raise ConversationError('validation', 'Managed Codex requires a public Agent Manager native environment reference')
        if self.protocol == 'opencode-v2' and (self.backend_id != 'opencode' or self.adapter_id != 'opencode-native' or len(self.argv) != 1 or self.transport.get('kind') != 'opencode-http'):
            raise ConversationError('validation', 'Native OpenCode requires matching provider, adapter and managed private transport')
        if self.protocol == 'opencode-v2' and 'agent_environment_ref' in self.transport:
            target = self.transport['agent_environment_ref']
            if (not isinstance(target, dict) or target.get('schema') != 'agent-manager/native-environment-1'
                    or target.get('provider') != 'opencode'):
                raise ConversationError('validation', 'OpenCode requires its exact Agent Manager native environment reference')
        if self.protocol in ("claude-agent-sdk", "antigravity-sdk", "copilot-sdk"):
            expected = {"claude-agent-sdk": "claude-sdk", "antigravity-sdk": "antigravity-sdk", "copilot-sdk": "copilot-sdk"}[self.protocol]
            provider = {"claude-agent-sdk": "claude", "antigravity-sdk": "antigravity", "copilot-sdk": "copilot"}[self.protocol]
            if self.argv or self.transport.get("kind") != "sdk" or self.adapter_id != expected or self.backend_id != provider:
                raise ConversationError("validation", "SDK launch requires matching provider/adapter, empty argv, and sdk transport")
        row = dict(schema="conversation-manager/prepared-launch-1", connection_id=self.connection_id,
                   backend_id=self.backend_id, adapter_id=self.adapter_id, protocol=self.protocol,
                   argv=self.argv, cwd=str(Path(self.cwd).expanduser().resolve()),
                   transport=self.transport, retention=self.retention, client_info=self.client_info,
                   settings=self.settings, resource_snapshot_ref=self.resource_snapshot_ref,
                   credential_refs=self.credential_refs)
        if self.resource_policy is not None:
            if not isinstance(self.resource_policy, dict) or self.resource_policy.get("schema") != "conversation-manager/resource-policy-1":
                raise ConversationError("validation", "Invalid captured resource policy")
            row["resource_policy"] = self.resource_policy
        reject_credentials(row)
        canonical(row)
        return row

    @classmethod
    def from_dict(cls, row, **transient):
        if not isinstance(row, dict):
            raise ConversationError("validation", "Prepared launch must be a JSON object")
        row = dict(row)
        if row.pop("schema", "conversation-manager/prepared-launch-1") != "conversation-manager/prepared-launch-1":
            raise ConversationError("validation", "Invalid prepared launch schema")
        if "env" in row or "services" in row:
            raise ConversationError("validation", "Portable descriptors cannot contain env or services")
        try:
            return cls(**row, **transient)
        except TypeError as exc:
            raise ConversationError("validation", "Invalid prepared launch fields") from exc


class TransportHandle(Protocol):
    async def call(self, method: str, params: dict) -> Any: ...
    async def close(self) -> None: ...


class Connector(Protocol):
    async def attach(self, connection_ref: dict) -> TransportHandle: ...


class Launcher(Protocol):
    async def launch(self, prepared: PreparedLaunch, *, operation_id: str) -> dict: ...
    async def inspect(self, runtime_ref: dict) -> dict: ...
    async def terminate(self, runtime_ref: dict, *, operation_id: str) -> dict: ...


def receipt(operation_id, status, *, result=None, error=None, recovery=None):
    return OperationReceipt(schema="conversation-manager/operation-1", operation_id=operation_id,
                            status=status, result=result, error=error, recovery=recovery or {})


def operation_key(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ConversationError("validation", "operation_id must be a nonempty string of at most 128 characters")
    canonical(value)
    return value
