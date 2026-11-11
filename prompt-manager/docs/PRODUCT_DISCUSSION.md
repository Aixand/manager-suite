# What Prompt Manager Is

Status: Agreed product discussion and intended direction. This document defines
the product's purpose, use cases, and feature expectations; it does not claim that
every feature described here is already implemented.

## Product definition

Prompt Manager is a standalone, user-controlled library for creating, organizing,
versioning, composing, and using prompts across agents and applications.

Its central promise is:

> You control the instructions your tools use, and application updates preserve
> that control.

The broader purpose is to help people and applications author, assemble,
personalize, deliver, and improve reusable AI instructions, with clear control
over what actually gets used.

Anyone should be able to use Prompt Manager independently through its CLI or
library. An individual can maintain a personal prompt collection. A developer can
integrate it into an application. A team can distribute shared prompt content.
Agent tools, editors, and workflow applications can consume these APIs; none
defines the product's identity or is required to use it.

A simple saved text prompt should be a useful starting point. Templates, shared
sections, application registration, and agent integrations add capabilities as
the user's needs grow.

## What it means to manage prompts

Managing a prompt means controlling its content, identity, versions, assignments,
and the effective instructions a consumer receives.

That includes answering practical questions:

- What prompts do I have, and what is each one for?
- Which instructions should a particular agent or application use?
- What did the application originally supply, and what have I customized?
- What changed in an incoming update?
- Which version is active, and why?
- What complete instructions will be delivered for these inputs?
- Can I compare a change, keep my version, or restore earlier content?
- Does a revised prompt produce more useful results on the tasks I care about?

Prompt Manager should support the whole lifecycle: create or import, organize,
compose, preview, assign or select, render, evaluate, revise, and adopt or restore.

## Who it serves

| User | Need |
| --- | --- |
| Individual | Save and reuse prompts for writing, coding, research, and other recurring tasks. |
| Agent user | Select persistent instructions for specific agents or profiles. |
| Application developer | Supply maintained prompt defaults while allowing users to customize them. |
| Team | Maintain shared instructions and distribute reviewable updates. |
| Prompt author | Compare revisions, test examples, and improve prompts based on evidence. |

## Main use cases

### 1. Maintain a personal prompt library

A user creates prompts, gives them meaningful names and descriptions, organizes
them, and retrieves them when needed. They can copy a prompt for manual use,
render a template through the CLI, or export it to another tool.

This workflow must be useful without registering an application, configuring an
agent, or running a service.

### 2. Assign instructions to an agent

A user selects one or more prompts for an agent or profile. For example, a review
agent might use general working instructions, project conventions, and review
criteria.

Once assigned, the integration includes the resolved content when preparing the
agent's instructions. The user should not have to paste it into every new
conversation. Preview should show the instructions that will be included.

Prompt Manager resolves the selected content and its version. An integration
such as Agent Manager owns its profile assignments and delivers that content
through the agent's supported instruction mechanism. Selected instructions remain
distinguishable from individual task messages and supporting reference material.

### 3. Customize application-supplied prompts

An application registers its supplied prompts under stable identities. Users can
inspect and customize the managed copies. The application requests a logical
prompt, and Prompt Manager returns the effective content.

For example, an application supplies `publisher/prompt_x`. The user edits it to
follow their team's conventions. When the application later supplies an update,
the user's version continues to be used until the user explicitly decides how to
handle the update.

The application should not have to implement its own customization detection,
update comparison, or reconciliation logic.

### 4. Build prompts from reusable sections

Users maintain general instructions, project guidance, task instructions, and
reference documents separately, then combine them into prompt recipes.

Several agents can share project conventions while retaining their own
specialist instructions. Section order, inclusion conditions, and selected
versions should be explicit and inspectable. Released recipes retain the shared
content they captured until an update is deliberately adopted.

### 5. Reuse templates with structured inputs

A prompt can declare inputs such as a task, audience, programming language, or
supporting context. Prompt Manager validates required inputs and types, applies
declared defaults, and produces the complete text or structured messages.

The application supplies the actual project context. Prompt Manager assembles it
according to the selected recipe.

### 6. Understand exactly what gets used

Users can inspect the effective prompt before use and trace its parts to their
sources. They can see the selected revision, substituted inputs, included or
omitted sections, dependencies, and final rendered content.

Applications can record this information with an execution so later investigation
can identify the instructions that were actually supplied. Reproducing a render
requires the inputs and rendering identity as well as the prompt revision.

### 7. Test and improve prompts

Users save representative inputs, check rendering expectations, and compare
revisions using the same examples.

A broader improvement workflow can also compare actual AI responses against
user-defined criteria. Prompt Manager can retain test cases, baseline and
candidate identities, feedback, and evaluation records. An external integration
can execute the model calls and return results for comparison.

Rendering checks establish that instructions assemble correctly. Behavioral
evaluations provide evidence about whether a change helps a particular task.
Adopting an evaluated candidate remains an explicit decision.

### 8. Share and move prompt libraries

Users can export, back up, and import prompts or complete collections. Teams and
application publishers can distribute reusable packs whose recipients can
customize independently.

File-based distribution is sufficient for this use case. A marketplace, hosted
registry, or cloud account is a separate product decision.

## Versioning and user ownership

Prompt Manager must distinguish these concepts:

| Concept | Meaning |
| --- | --- |
| Prompt identity | The stable name consumers request, distinct from its version. |
| Publisher baseline | The application-supplied content against which user changes are tracked. |
| Active content | The accepted content used by default for new resolutions. |
| Incoming update | New publisher content awaiting adoption or reconciliation. |
| Immutable revision | A preserved snapshot that can be inspected, selected, or restored. |
| Effective prompt | The selected content, composed and rendered with the supplied inputs. |

Publisher identity must distinguish unrelated sources with the same prompt name.
Human-readable version labels and content identities have different jobs: labels
describe releases, while content identities establish exactly what was supplied.
An update policy also needs a reliable way to determine which publisher revision
is newer.

### Registration and update rules

| Situation | Required behavior |
| --- | --- |
| Prompt does not exist | Import and activate the supplied content. |
| Same revision and content already exist | Do nothing; registration is idempotent. |
| A newer publisher revision arrives and active content is unmodified | Activate the update for consumers following the managed default. |
| A newer publisher revision arrives and active content is customized | Retain the user's content and mark the update for manual review. |
| An older publisher revision is registered | Preserve the current selection; do not silently downgrade. |
| The same publisher revision is supplied with different content | Report a conflict. |
| Different content arrives without reliable revision ordering | Keep the active content and request review. |
| A consumer explicitly pins an immutable revision | Continue using that revision until its selection changes. |

Registration imports content into managed storage. Subsequent application use
resolves the accepted managed copy, so changing or removing the original
publisher files does not silently change the user's accepted instructions.

### Manual reconciliation

When user customizations and publisher updates coexist, the user chooses among:

1. Accept the incoming publisher version.
2. Keep the current user version.
3. Combine selected changes into a new accepted version.

**Until that decision is applied, the user version remains active for consumers
following the managed default.** Discovering an update, displaying a notification,
or previewing a proposed merge does not change the active content.

Where a known baseline exists, comparison should show the baseline, user changes,
and publisher changes. A proposed combination can assist the review, but applying
it is a manual decision. Reviews must detect intervening edits or newer updates
so a stale decision cannot overwrite more recent work.

Choosing to keep the user version completes reconciliation for that particular
update. The same update should not repeatedly require a decision. Later publisher
updates can be offered under the same rules.

## Feature expectations

| Area | Intended capabilities |
| --- | --- |
| Authoring | Create, edit, describe, duplicate, and inspect prompts; support simple text without mandatory templating. |
| Organization | Search, tags, grouping, and visibility into where prompts and shared sections are used. |
| Composition | Reusable sections and documents, explicit order, message roles, dependencies, and conditional inclusion. |
| Inputs and rendering | Typed inputs, required values, defaults, previews, and text or structured-message output. |
| Context selection | Explicit personal, project, application, and agent selections with understandable precedence and combination rules. |
| Agent use | Selectable instruction content, previews, and APIs that integrations use to include it automatically. |
| Application use | Stable registration and lookup, managed copies, idempotent updates, and effective-content resolution. |
| Version control | Immutable revisions, history, comparisons, releases, pins, and restoration. |
| Customization | Separate publisher and user changes, protect active edits, and expose pending updates. |
| Reconciliation | Compare alternatives, accept publisher content, retain user content, or apply a reviewed combination. |
| Traceability | Explain selected content, sources, revisions, dependencies, and the final render. |
| Testing and improvement | Saved rendering cases, revision comparisons, feedback, and integration points for behavioral evaluation. |
| Portability | Import, export, backup, and reusable collections without a required hosted service. |
| Independent access | Standalone CLI and public library interfaces; optional user interfaces and integrations. |

These capabilities serve different levels of use. Someone saving a personal
prompt should encounter a small workflow. Someone maintaining application
defaults can use the additional registration, versioning, and review controls.

## Responsibility boundaries

Prompt Manager owns prompt content, its input contracts, composition, accepted
versions, update handling, and effective-content resolution.

Applications own their project data, business operations, context selection, and
acceptance of generated results. Agent integrations own agent startup and delivery
through supported instruction channels. Evaluation integrations can run models
while Prompt Manager retains prompt-related cases and comparison records.

Saving an edit, publishing a release, and changing a consumer's selection are
separate actions. Existing execution or conversation records retain the
instructions they captured; changes govern future eligible resolutions.

## Product priorities

The first priority is reliable ownership: independent use, personal authoring,
application registration, agent instruction integration, version history, and
customizations that survive publisher updates.

The next priority is understandable reuse: composition, templates, organization,
effective-prompt previews, usage visibility, and portable collections.

Testing, feedback, and behavioral comparisons extend that foundation into a
prompt improvement workflow. The product can grow across these layers while
keeping its simplest interaction clear: save a prompt, use it, customize it, and
control which changes take effect.
