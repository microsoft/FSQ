# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import ipaddress
import os
import re
import socket
import struct
import time
from contextlib import contextmanager

from fsq_agent.models import AndroidDevice

MAX_BYTES = 1_048_576
PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[.][A-Za-z_][A-Za-z0-9_]*)*", re.ASCII)
SERIAL = re.compile(r"[A-Za-z0-9_.:@-]{1,256}", re.ASCII)


def endpoint():
    if os.environ.get("ADB_SERVER_SOCKET") or os.environ.get("ADB_SERVER_PORT"):
        raise ValueError("adb_endpoint_invalid")
    host = os.environ.get("ANDROID_ADB_SERVER_HOST", "127.0.0.1")
    port_text = os.environ.get("ANDROID_ADB_SERVER_PORT", "5037")
    try:
        if host == "localhost":
            host = "127.0.0.1"
        loopback = ipaddress.IPv4Address(host).is_loopback
        port = int(port_text)
    except ValueError:
        raise ValueError("adb_endpoint_invalid") from None
    if not loopback or not port_text.isascii() or not port_text.isdecimal() or not 1 <= port <= 65535:
        raise ValueError("adb_endpoint_invalid")
    return host, port


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError
    return value


@contextmanager
def connection(deadline):
    host, port = endpoint()
    wire = socket.create_connection((host, port), timeout=remaining(deadline))
    try:
        yield wire
    finally:
        wire.close()


def read(wire, length, deadline):
    if not 0 <= length <= MAX_BYTES:
        raise ValueError("adb_failed")
    chunks = bytearray()
    while len(chunks) < length:
        wire.settimeout(remaining(deadline))
        chunk = wire.recv(length - len(chunks))
        if not chunk:
            raise ValueError("adb_failed")
        chunks.extend(chunk)
    return bytes(chunks)


def request(wire, service, deadline):
    payload = service.encode("utf-8")
    if len(payload) > 65535:
        raise ValueError("adb_failed")
    wire.settimeout(remaining(deadline))
    wire.sendall(f"{len(payload):04x}".encode() + payload)
    if read(wire, 4, deadline) != b"OKAY":
        raise ValueError("adb_failed")


def inventory(deadline):
    with connection(deadline) as wire:
        request(wire, "host:devices-l", deadline)
        prefix = read(wire, 4, deadline)
        if not re.fullmatch(b"[0-9a-fA-F]{4}", prefix):
            raise ValueError("adb_failed")
        output = read(wire, int(prefix, 16), deadline).decode("utf-8")
    devices = []
    seen = set()
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 2 or not SERIAL.fullmatch(parts[0]) or parts[0] in seen:
            raise ValueError("adb_failed")
        seen.add(parts[0])
        metadata = {}
        for token in parts[2:]:
            key, _, value = token.partition(":")
            if key in {"model", "device", "product", "transport_id"} and value:
                metadata[key] = value[:200]
        state = "no_permissions" if parts[1:3] == ["no", "permissions"] else parts[1]
        devices.append(AndroidDevice(serial=parts[0], state=state, metadata=metadata))
    return devices


def valid_package(value):
    return isinstance(value, str) and 0 < len(value) <= 255 and PACKAGE.fullmatch(value) is not None


def package_status(serial: str, app_id: str, timeout_seconds: float = 5.0) -> str:
    if not SERIAL.fullmatch(serial) or not valid_package(app_id):
        return "query_failed"
    deadline = time.monotonic() + min(max(timeout_seconds, 0.01), 10.0)
    try:
        with connection(deadline) as wire:
            request(wire, f"host:transport:{serial}", deadline)
            # Validated package grammar has no shell metacharacters. Shell v2 provides exit status.
            request(wire, f"shell,v2,raw:cmd package list packages -f {app_id}", deadline)
            output = bytearray()
            received = 0
            stderr = False
            while True:
                channel, size = struct.unpack("<BI", read(wire, 5, deadline))
                received += size + 5
                if received > MAX_BYTES:
                    return "query_failed"
                data = read(wire, size, deadline)
                if channel == 1:
                    output.extend(data)
                elif channel == 2:
                    stderr = stderr or bool(data)
                elif channel == 3:
                    if data != b"\x00" or stderr:
                        return "query_failed"
                    break
                else:
                    return "query_failed"
        lines = output.decode("utf-8").splitlines()
        packages = set()
        for line in lines:
            path, separator, package = line[8:].rpartition("=")
            if not line.startswith("package:") or not separator or not valid_package(package) or not path.startswith("/") or not path.endswith(".apk") or any(ord(char) < 32 for char in path):
                return "query_failed"
            packages.add(package)
    except TimeoutError:
        return "timeout"
    except (OSError, ValueError, UnicodeError):
        return "query_failed"
    else:
        return "installed" if app_id in packages else "absent"
