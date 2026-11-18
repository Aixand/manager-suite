# Configure state and defaults

Choose editable defaults without changing recorded runtime ownership. Run CLI commands from an environment with Conversation Manager installed.


Conversation Manager uses the shared manager-directory convention. With no
path overrides and the usual XDG default, the layout is:

```text
~/.local/share/aixand/manager/conversation/
├── config.toml
└── state/              # Managed SQLite history, receipts and runtime references
```

Initialize and inspect it without starting a worker or creating a database:

```sh
conversation-manager config init
conversation-manager config path
conversation-manager config show
# Select another config explicitly:
conversation-manager --config /path/to/config.toml config show
```

`config init` creates a private editable template and never overwrites an existing
file. Reading defaults does not create files. Missing default configuration uses
built-ins; an explicitly selected missing or malformed config reports an error.
The generated template keeps standalone ownership and comments out optional
paths and process attributes. The following is an example edited configuration,
not the literal `config init` output:

```toml
[storage]
home = "~/.local/share/aixand/manager/conversation/state"

[runtime]
kind = "standalone" # Or "undercurrent"

[runtime.undercurrent]
home = "~/.aixand/undercurrent"
# bridge = "/absolute/path/to/uc-python-bridge"

[process]
name_prefix = "conversation"

[process.labels]
app = "conversation-manager"

[process.metadata]
purpose = "development"
```

Use `Client(config='/path/to/config.toml')` or CLI `--config` to select the file.
Otherwise `CONVERSATION_MANAGER_CONFIG` selects it, followed by the default path.
Config-file paths expand `~`; relative paths are resolved against the directory
containing that config file. An alternate config without `[storage].home` keeps
the standard state default. An explicit SDK/CLI path resolves against the caller's
working directory as before.

Precedence for state/runtime settings is explicit SDK arguments or CLI flags,
then documented environment overrides, then file defaults, then built-ins:

- State: `Client(home=...)` / `--home`, `CONVERSATION_MANAGER_HOME`,
  `[storage].home`, then the resolved product directory plus `/state`.
- Runtime: `runtime=...` / `--runtime`, `CONVERSATION_MANAGER_RUNTIME`,
  `[runtime].kind`, then `standalone`.
- Daemon options merge per field. `runtime_options` / `--runtime-options` override
  `CONVERSATION_MANAGER_UNDERCURRENT_HOME` and
  `CONVERSATION_MANAGER_UNDERCURRENT_BRIDGE`, then the ordinary `UNDERCURRENT_HOME`
  and `UNDERCURRENT_PYTHON_BRIDGE`, then `[runtime.undercurrent]` defaults. Ordinary
  Undercurrent SDK defaults fill remaining fields. Daemon options in the file are
  dormant when standalone is selected.
- Process labels/metadata merge with per-launch `process` / `--process` attributes.
  Explicit keys win. A configured name prefix generates a stable per-start worker
  name; an explicit name wins. Config does not accept credentials or runtime
  environment-variable payloads.

`config show` reports defaults resolved against its config, flags and environment,
without connecting to a daemon. A client snapshots defaults when constructed;
create a new client after editing the file. Config changes apply to new worker
starts and never rewrite recorded runtime references or existing conversations.
Shared OpenCode chats reuse their worker's name and attributes; only explicit
per-start constraints require a matching shared worker.

Existing stores are **not moved or automatically adopted**.
To access an existing store, use `--home /path/to/old/store` or set `[storage].home` to that exact
path. Initialization does not modify existing data or switch active workers.
The initial editable-configuration rollout began in Conversation Manager.
Check each other manager's current guide for its own migration status.


## Default storage location


The default product directory is `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/conversation`.
Set `AIXAND_MANAGER_ROOT=/custom/managers` to use `/custom/managers/conversation`
without changing the data location of unrelated applications. Unset, empty, or
relative `XDG_DATA_HOME` values fall back to `~/.local/share`.
Explicit CLI/SDK paths and product-specific environment overrides take precedence;
these paths are used directly. Existing configured storage paths remain supported.
Agent and Conversation config overrides remain separate from their state-home overrides.
Default path resolution creates no directories and does not move or adopt old stores.
To keep using an existing store, supply its original path explicitly. Native provider
homes connected by the user and operating-system temporary files retain their locations.

## Owning implementation

[Configuration resolution](../src/conversation_manager/config.py),
[product paths](../src/conversation_manager/_paths.py), and
[configuration tests](../tests/test_config.py) define precedence and side effects.
