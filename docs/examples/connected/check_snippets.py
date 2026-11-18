"""Check documentation Python excerpts against the maintained runnable sources.

Run from Manager Suite: python docs/examples/connected/check_snippets.py
No services, provider credentials or third-party packages are needed.
--source selects another checkout; --overlay adds staged repo-relative files for review.
Only the explicitly mapped SDK excerpts are checked; shell commands, output blocks
and hypothetical examples outside these guides are not treated as executable source.
"""
import argparse
from pathlib import Path
import re
import textwrap

# This mapping records source ownership, not another copy of displayed code.
# A marker restricts matching to that source's documented excerpt region.
SOURCES = {'docs/examples/render-prompt.md': [('docs/examples/scripts/render-prompt.py', None)],
 'docs/examples/connect-skill.md': [('docs/examples/scripts/connect-skill.py', None)],
 'docs/examples/resolve-mcp.md': [('docs/examples/scripts/resolve-mcp.py', None)],
 'docs/examples/retained-conversation.md': [('docs/examples/scripts/retained-conversation.py', None)],
 'docs/examples/audit-to-inbox.md': [('docs/examples/scripts/audit-to-inbox.py', None)],
 'docs/examples/persistent-agents.md': [('docs/examples/scripts/managed_agent.py', None)],
 'docs/examples/parallel-reviews.md': [('docs/examples/scripts/managed_agent.py', None)],
 'agent-manager/docs/examples/profile-identity.md': [('agent-manager/examples/profile_identity.py', None)],
 'agent-manager/docs/examples/policy-snapshot.md': [('agent-manager/examples/policy_snapshot.py', None)],
 'conversation-manager/docs/examples/offline-controls.md': [('conversation-manager/examples/offline_controls.py',
                                                             None)],
 'conversation-manager/docs/examples/offline-lifecycle.md': [('conversation-manager/examples/offline_lifecycle.py',
                                                              None)],
 'conversation-manager/docs/examples/reference-chat.md': [('conversation-manager/examples/reference_chat/app.py',
                                                           None)],
 'mcp-manager/docs/examples/local-operations.md': [('mcp-manager/examples/local_operations.py', None)],
 'mcp-manager/docs/examples/resolve-without-launch.md': [('mcp-manager/examples/resolve_without_launch.py',
                                                          None)],
 'prompt-manager/docs/examples/compose-pack.md': [('prompt-manager/examples/compose_pack.py', None)],
 'prompt-manager/docs/examples/owned-workflow.md': [('prompt-manager/examples/owned_workflow.py', None)],
 'prompt-manager/docs/examples/publisher-review.md': [('prompt-manager/examples/publisher_review.py', None)],
 'skill-manager/docs/examples/project-connection.md': [('skill-manager/examples/project_connection.py',
                                                        None)],
 'skill-manager/docs/examples/publisher-update.md': [('skill-manager/examples/sdk_application.py', None)],
 'docs/examples/connected/prompt-worker.md': [('docs/examples/connected/prompt_worker.py', 'report-request')],
 'docs/examples/connected/frozen-prompt.md': [('docs/examples/connected/frozen_prompt.py',
                                               'frozen-delivery')],
 'docs/examples/connected/artifact-audit.md': [('docs/examples/connected/artifact_worker.py',
                                                'execute-audit')],
 'docs/examples/connected/skill-rollout.md': [('docs/examples/connected/skill_rollout.py',
                                               'reviewed-rollout')],
 'docs/examples/connected/batch-pipeline.md': [('docs/examples/connected/batch_pipeline.py', 'fanout'),
                                               ('docs/examples/connected/batch_worker.py', 'recover-worker')],
 'docs/examples/connected/mcp-job.md': [('docs/examples/connected/mcp_worker.py', 'invoke')],
 'docs/examples/connected/conversation-jobs.md': [('docs/examples/connected/conversation_jobs.py',
                                                   'retained')],
 'docs/examples/connected/agent-mailbox.md': [('docs/examples/connected/agent_mailbox.py',
                                               'recover-mailbox')],
 'docs/examples/connected/parallel-gate.md': [('docs/examples/connected/parallel_gate.py',
                                               'acceptance-gate')],
 'docs/examples/connected/live-review.md': [('docs/examples/connected/../scripts/managed_agent.py', None)]}


def normalize(value):
    return textwrap.dedent(value).strip("\n").rstrip()


def contains_excerpt(source, snippet):
    """Compare contiguous lines while removing only their shared indentation."""
    wanted = normalize(snippet)
    count = len(wanted.splitlines())
    lines = source.splitlines()
    return any(normalize("\n".join(lines[i:i + count])) == wanted
               for i in range(len(lines) - count + 1))


def marked_region(source, marker):
    if marker is None:
        return source
    start = re.search(r"(?m)^ *# docs:start " + re.escape(marker) + r" *$", source)
    end = re.search(r"(?m)^ *# docs:end " + re.escape(marker) + r" *$", source)
    if not start or not end or start.end() >= end.start():
        raise ValueError("Missing or misordered documentation marker: " + marker)
    return source[start.end():end.start()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--overlay', action='append', type=Path, default=[])
    args = parser.parse_args()
    # Earlier overlays take precedence, then the source checkout supplies unchanged files.
    roots = [p.resolve() for p in args.overlay] + [args.source.resolve()]

    def read(relative):
        for root in roots:
            file = (root / relative).resolve()
            if file.is_relative_to(root) and file.is_file():
                return file.read_text(encoding='utf-8')
        raise ValueError('Missing documentation or source file: ' + relative)

    total = 0
    for page, candidates in SOURCES.items():
        document = read(page)
        excerpts = re.findall(r'(?m)^```python[ \t]*\n(.*?)^```[ \t]*$', document, re.S)
        if not excerpts:
            raise ValueError('Mapped guide has no Python excerpt: ' + page)
        regions = [(path, marked_region(read(path), marker)) for path, marker in candidates]
        for index, excerpt in enumerate(excerpts, start=1):
            if not any(contains_excerpt(source, excerpt) for _, source in regions):
                raise ValueError(f'{page}: Python block {index} differs from its maintained source')
            total += 1
        print(f'PASS {page}: {len(excerpts)} source excerpt(s)')
    print(f'PASS {total} Python excerpts across {len(SOURCES)} guides')


if __name__ == '__main__':
    main()
