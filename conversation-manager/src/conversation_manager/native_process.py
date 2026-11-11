"""Native process owner. Keep the lease outside provider helper processes."""
import argparse
import os
import signal
import subprocess
import sys
import time
from aixand_local_runtime import process_start

CREATE_SUSPENDED = 0x00000004


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner', type=int, required=True)
    parser.add_argument('--owner-start', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    command = options.command[1:] if options.command[:1] == ['--'] else options.command
    if not command or process_start(options.owner) != options.owner_start:
        return 1
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    job = None
    if os.name != 'nt':
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
    process = subprocess.Popen(command, stdin=sys.stdin.buffer,
        stdout=sys.stdout.buffer, stderr=sys.stderr.buffer,
        close_fds=True, **({'creationflags': subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED}
                          if os.name == 'nt' else {'start_new_session': True}))
    if os.name == 'nt':
        from .windows_job import ProcessJob, resume_process
        try:
            job = ProcessJob(process.pid)
            resume_process(process.pid)
        except BaseException:
            if job is not None:
                job.close()
            process.kill()
            process.wait()
            raise

    def signal_group(value):
        try:
            os.killpg(process.pid, value)
        except ProcessLookupError:
            pass

    try:
        while process.poll() is None and not stopping and process_start(options.owner) == options.owner_start:
            time.sleep(.02)
    finally:
        if job is not None:
            # Retain the lease until every process has left the job.
            job.terminate()
            process.wait()
            job.close()
        else:
            signal_group(signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                signal_group(signal.SIGKILL)
                process.wait()
            # Helpers must not outlive the provider or retain its stdio streams.
            signal_group(signal.SIGKILL)
    return process.returncode


if __name__ == '__main__':
    raise SystemExit(main())
