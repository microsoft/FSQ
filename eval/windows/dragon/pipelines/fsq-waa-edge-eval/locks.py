# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Serializes package-owned filesystem resources across Windows evaluation processes.
import os
from contextlib import contextmanager


@contextmanager
def file_lock(path, busy_message):
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError(busy_message) from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
