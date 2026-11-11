"""Private directories and atomic file publication."""
import os
import tempfile
import time


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def atomic(path, data):
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name == 'nt':
                # An elevated token's default owner can be Administrators.
                # Publish user-owned state using the created file's handle.
                from agent_manager.storage.platform import secure_file
                secure_file(stream)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers may briefly deny replacement while polling a state or
        # progress file. Keep the original intact and retry the atomic rename;
        # persistent access failures and unrelated errors must still surface.
        deadline = time.monotonic() + 2
        while True:
            try:
                os.replace(temporary, path)
                break
            except OSError as error:
                if getattr(error, 'winerror', None) not in (5, 32, 33) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
