# Optional GitHub sources

Install `aixand-skill-manager[github]` for GitHub acquisition. The extra adds
`aixand-lib-github-source>=0.1.0b0,<0.2`, located in the repository at
`../aixand-libraries/github-source`. It is independently installable and has no runtime
dependencies. Neither local Skill Manager operations nor Prompt Manager require it.

```sh
skill-manager managed discover https://github.com/example/skills --ref main
skill-manager managed import-github https://github.com/example/skills/tree/main/skills/review
skill-manager managed check-updates PUBLISHER SKILL
```

The shared client resolves commits and returns complete selected trees. Skill
Manager discovers SKILL.md packages, validates native content, preserves original
bytes/executable flags, and submits accepted content through Prompt Manager's
public lifecycle SDK. A SKILL.md URL selects its containing package in this adapter;
the generic GitHub client itself selects an individual file exactly as requested.

Skill Manager owns pinning/tracking, reference switches, due-check scheduling,
creation, local import, CLI commands, and retained jobs. The worker is
`python -m skill_manager.source_jobs HOME JOB_ID`. No job manager, daemon, Git
executable, or parent repository checkout is needed for installed operation.

## Existing content and jobs

The adapter retains existing source/discovery/tree/job wire schemas, IDs, signatures,
paths, repository IDs, candidate paths, native digests, and release references.
Schema strings containing `resource-sources` are retained for compatibility; they
do not imply a dependency. New shared snapshot/source schemas are translated at
the adapter boundary. Native source and accepted state records are not migrated.

Existing completed, failed, cancelled, and interrupted job records remain readable.
They are not replayed automatically. Jobs started before an upgrade keep their
original process identity; an absent/reused worker is marked interrupted without
signalling a different process. New jobs use Skill Manager's own worker. Public
job APIs and the CLI's `--background`/`--key` behavior are preserved. MCP installation
operations are not part of Skill Manager's job API.

Without the extra, local create/import and native package operations still work.
Stored GitHub skills, source settings, histories, and releases remain available
offline. Pinning and stopped tracking can be inspected without fetching. An actual
GitHub acquisition request fails with an `unsupported` diagnostic and installation
hint; due checks do not enqueue work while the integration is unavailable.

Publisher updates keep customized content active until explicit reconciliation.
Same native package content and unrelated repository commits do not create new
accepted-state revisions. Failed or stale fetches never replace customized content.

## Scope

Only public github.com sources are supported. The shared package provides bounded
timeouts/retries and safe in-memory acquisition; Skill Manager validates packages
again before accepting them. Persistent jobs and source policy intentionally remain
outside the shared package. Private authentication, general internet downloads,
and execution/installation of downloaded dependencies are not included.

`aixand-lib-filesystem` supplies store locking and Windows pinned-path protection.
Store operations use the filesystem utility directly.
