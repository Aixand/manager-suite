# Locate Agent Manager state

Use these paths to select an existing store without moving provider-owned files.


The default product directory is `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/agent`.
Set `AIXAND_MANAGER_ROOT=/custom/managers` to use `/custom/managers/agent`
without changing the data location of unrelated applications. Unset, empty, or
relative `XDG_DATA_HOME` values fall back to `~/.local/share`.
Explicit CLI/SDK paths and product-specific environment overrides take precedence;
these paths are used directly. Existing configured storage paths remain supported.
Agent and Conversation config overrides remain separate from their state-home overrides.
Default path resolution creates no directories and does not move or adopt old stores.
To keep using an existing store, supply its original path explicitly. Native provider
homes connected by the user and operating-system temporary files retain their locations.


See [profile storage](profiles-and-environments.md) for registry layout and retirement guards.
