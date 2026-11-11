"""Own one resumed native CLI, its sanitized environment and history lock."""
import json
from pathlib import Path
import signal
import subprocess
import sys

from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.storage.locks import locks
from agent_manager.runtime.native.file_continuation import HistoryView, require_version


def run(plan, arguments):
    manager = Manager(plan['provider'], config=plan['config'], root=plan['state_root'])
    view = HistoryView(plan['provider'], Path(plan['home']), Path(plan['source']),
        Path(plan['history_home']), plan['native_id'], plan['source_ref'], plan['environment_id'], plan['session'])
    require_version(manager)
    with locks(view.history_home / '.agent-manager-continuations', view.source_ref):
        view.validate(manager)
        state = manager.state(); source = state['sessions'].get(view.source_ref)
        if source is None or source['native_id'] != view.native_id or source['history_home'] != str(view.history_home):
            raise Error('The original native session reference changed')
        from agent_manager.runtime.native.file_continuation import check_claude_writers
        check_claude_writers(manager, state, source, view.home)
        if view.provider == 'claude':
            from agent_manager.runtime.native.claude_sessions import validate_arguments
            validate_arguments(arguments, view.native_id, resume_target=str(view.source))
        else:
            from agent_manager.runtime.native.copilot_sessions import validate_arguments
            validate_arguments(arguments, view.native_id)
        import shutil
        executable = shutil.which(arguments[0]) if arguments else None
        configured = shutil.which(manager.config['executable'])
        if not executable or not configured or Path(executable).resolve() != Path(configured).resolve():
            raise Error('The native continuation executable changed')
        variables = manager.launch_environment(view.home, cwd=source['cwd'])
        child = subprocess.Popen(arguments, cwd=source['cwd'], env=variables)
        prior = {}
        class Stop(Exception):
            def __init__(self, number): self.number = number
        try:
            def forward(number, frame):
                if child.poll() is None: child.send_signal(number)
                if number in (signal.SIGTERM, signal.SIGHUP): raise Stop(number)
            for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                prior[number] = signal.signal(number, forward)
            code = child.wait()
            return code if code >= 0 else 128 - code
        except Stop as stop:
            return 128 + stop.number
        finally:
            # Keep the history lock until the native child has actually exited.
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill(); child.wait()
            for number, handler in prior.items(): signal.signal(number, handler)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(argv) < 4 or argv[1] != '--': raise Error('Invalid native continuation command')
        path = Path(argv[0])
        if path.stat().st_size > 65536: raise Error('Invalid native continuation plan')
        plan = json.loads(path.read_text())
        if plan.get('schema') != 1 or plan.get('provider') not in ('claude', 'copilot'):
            raise Error('Invalid native continuation plan')
        return run(plan, argv[2:])
    except (Error, OSError, ValueError, KeyError, TypeError):
        print('Native continuation could not start; inspect the retained session and destination environment.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
