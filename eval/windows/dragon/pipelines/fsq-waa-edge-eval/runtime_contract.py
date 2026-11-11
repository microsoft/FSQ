# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Validates supported native-driver layouts without confusing an installed release with a deployed source revision.
import hashlib
import importlib.metadata
from pathlib import Path

SOURCE_DRIVER_SHA256 = "075b3ce5cb2b97a66efb8a6019bffb4c64b56ce9319a90f35f2c099d91db305f"


def driver_digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def verify_runtime():
    version = importlib.metadata.version("fsq-agent")
    if version == "0.1.0a3":
        return version
    import fsq_agent

    driver = Path(fsq_agent.__file__).parent / "drivers" / "windows" / "_pywinauto.py"
    if version != "0.1.4" or driver_digest(driver) != SOURCE_DRIVER_SHA256:
        raise RuntimeError("The FSQ Windows driver is not an inspected adapter-compatible version. Do not silently run another version.")
    return version
