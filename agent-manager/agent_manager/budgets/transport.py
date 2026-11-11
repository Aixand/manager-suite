"""Bounded native subprocess reads; no prompts, credential files or retained raw output."""
from contextlib import contextmanager
import json
import os
import queue
import signal
import subprocess
import threading
import time

from .models import BudgetError, failure

MAX_OUTPUT = 4 * 1024 * 1024


def deadline(timeout):
    return time.monotonic() + timeout


def remaining(end):
    value = end - time.monotonic()
    if value <= 0:
        raise BudgetError('timeout')
    return value


@contextmanager
def process(manager, home, arguments, *, stdin=subprocess.DEVNULL, cwd=None):
    cwd = home if cwd is None else cwd
    environment = manager.launch_environment(home, cwd=cwd)
    environment.update(AGY_CLI_DISABLE_AUTO_UPDATE='true', DISABLE_AUTOUPDATER='1')
    command = manager.launch_command(home, [manager.config['executable'], *arguments])
    try:
        child = subprocess.Popen(command, env=environment, cwd=cwd, stdin=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=os.name != 'nt')
    except OSError:
        raise BudgetError('missing-cli') from None
    try:
        yield child
    finally:
        if os.name == 'posix':
            # Also stop descendants whose parent exited while leaving stdout open.
            try: os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        if child.poll() is None:
            if os.name != 'posix':
                child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if os.name == 'posix':
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                else:
                    child.kill()
                child.wait(timeout=2)
        if os.name == 'posix':
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                stream.close()


class Reader:
    def __init__(self, stream, read):
        self.items = queue.Queue(maxsize=16)
        self.stopped = threading.Event()

        def produce():
            try:
                while not self.stopped.is_set():
                    value = read(stream)
                    self.put(value)
                    if value is None: break
            except (ValueError, OSError, UnicodeError, BudgetError):
                self.put(BudgetError('invalid-response'))

        self.thread = threading.Thread(target=produce, daemon=True)
        self.thread.start()

    def put(self, value):
        while not self.stopped.is_set():
            try:
                self.items.put(value, timeout=.05)
                break
            except queue.Full: pass

    def get(self, end):
        try: value = self.items.get(timeout=remaining(end))
        except queue.Empty: raise BudgetError('timeout') from None
        if isinstance(value, BudgetError): raise value
        return value

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=1)


def run(manager, home, arguments, end, *, cwd=None):
    reader = None
    try:
        with process(manager, home, arguments, cwd=cwd) as child:
            reader = Reader(child.stdout, lambda stream: stream.read1(65536) or None)
            output, size = [], 0
            while True:
                chunk = reader.get(end)
                if chunk is None: break
                size += len(chunk)
                if size > MAX_OUTPUT: raise BudgetError('invalid-response')
                output.append(chunk)
            try: code = child.wait(timeout=remaining(end))
            except subprocess.TimeoutExpired: raise BudgetError('timeout') from None
            return b''.join(output), code
    finally:
        if reader: reader.close()


def message(stream, framed):
    if framed:
        size, header_size = None, 0
        while True:
            line = stream.readline(8193)
            if not line: return None
            header_size += len(line)
            if header_size > 8192: raise BudgetError('invalid-response')
            if line in (b'\r\n', b'\n'): break
            key, separator, value = line.partition(b':')
            if not separator: raise BudgetError('invalid-response')
            if key.lower() == b'content-length': size = int(value)
        if size is None or not 0 < size <= MAX_OUTPUT: raise BudgetError('invalid-response')
        data = stream.read(size)
        if len(data) != size: raise BudgetError('invalid-response')
    else:
        data = stream.readline(MAX_OUTPUT + 1)
        if not data: return None
        if len(data) > MAX_OUTPUT: raise BudgetError('invalid-response')
    value = json.loads(data)
    if not isinstance(value, dict): raise BudgetError('invalid-response')
    return value


@contextmanager
def rpc(manager, home, arguments, end, *, framed=False, cwd=None):
    reader = None
    try:
        with process(manager, home, arguments, stdin=subprocess.PIPE, cwd=cwd) as child:
            reader = Reader(child.stdout, lambda stream: message(stream, framed))
            counter, received = 0, 0

            def request(method, params=None, *, notification=False):
                nonlocal counter, received
                counter += 1
                payload = dict(jsonrpc='2.0', method=method, params=params or {})
                if not notification: payload['id'] = counter
                data = json.dumps(payload).encode()
                if framed: data = f'Content-Length: {len(data)}\r\n\r\n'.encode() + data
                else: data += b'\n'
                try:
                    child.stdin.write(data); child.stdin.flush()
                except (OSError, ValueError): raise BudgetError('unavailable') from None
                if notification: return None
                while True:
                    item = reader.get(end)
                    if item is None: raise BudgetError('unavailable')
                    received += len(json.dumps(item))
                    if received > MAX_OUTPUT: raise BudgetError('invalid-response')
                    if 'method' in item:
                        # Budget probes never answer auth, tool, approval or model requests.
                        if 'id' in item: raise BudgetError('unsupported')
                        continue
                    if item.get('id') != counter: continue
                    if 'error' in item: raise failure(item['error'])
                    if not isinstance(item.get('result'), dict): raise BudgetError('invalid-response')
                    return item['result']

            yield request
    finally:
        if reader: reader.close()
