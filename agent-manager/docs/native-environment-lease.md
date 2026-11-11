# Native environment lease

`Manager.lease_native_environment(environment_id, expected_revision=None)` returns
a synchronous context manager for independently owned local native processes.
It supports local Codex and OpenCode and requires POSIX and an immutable environment UUID; it does not launch a process,
start a runtime, authenticate, or depend on another manager.

```python
with manager.lease_native_environment(environment_id) as lease:
    descriptor = lease.descriptor
    # Process owners use lease.env and pass_fds=lease.pass_fds with subprocess.
```

`descriptor` uses schema `agent-manager/native-environment-1`. It contains provider,
the public target, a revision covering environment preferences and provider
configuration, the resolved executable, saved model preference, configuration
path, and registry root. Alias labels do not affect revision identity. Descriptors
are local references, not portable credentials or proof of account entitlement.

`env` is transient: Codex credential/home overrides are filtered and
`CODEX_HOME` is set to the selected native home. OpenCode receives its selected
XDG/config/database paths with competing ambient credentials, inline configuration,
and endpoint overrides removed. No authentication file is read or
copied. Never serialize the lease or its environment. `pass_fds` lets a POSIX child
retain the shared environment guard after its owner's handle closes. A process
owner must keep the lease for its native child's lifetime and stop/reap the child
before releasing it normally. A child that closes inherited guard descriptors no
longer provides orphan protection.

An expected revision is checked while holding the environment guard. Missing or
retired environments, unavailable homes/executables, stale revisions, incompatible
providers/platforms, and conflicting administrative locks fail explicitly. No
fallback to an alias or another environment occurs.

Leases share the existing environment lock namespace. Multiple independent Codex
conversation owners may hold shared leases. OpenCode uses an exclusive lease and
rejects active or unconfirmed Agent Manager native runs in that environment, so an
external owner must reuse its retained server for multiple chats. Unmanaged native
processes outside this contract are not coordinated. Administrative operations requiring
exclusive ownership wait or report busy according to their existing policy.
Holding a lease can therefore delay login, environment edits, and retirement until
the owning process stops. It is not a native-conversation writer lock, a provider
quota reservation, or an execution reference managed by Undercurrent.
