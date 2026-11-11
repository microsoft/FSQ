# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Loads unchanged WAA task definitions and independently scores the supported local Edge lane.
import hashlib
import json
from pathlib import Path

SUPPORTED = {
    "profile_name_from_edge": "profile.name",
    "edge_font_size": "webkit.webprefs.default_font_size",
    "edge_default_download_folder": "download.default_directory",
}
DEFAULT_IDS = [
    "1a1ec621-b675-4099-96a9-f702dc27afb4-wos",
    "2acd62b4-a2ab-44a7-a7e3-f5227bbd8324-wos",
    "58f493b5-5a96-4450-99ca-7cebe144c7e5-wos",
]


def load_dataset(directory, selected_ids):
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("dataset-dir must be a directory of WAA Edge JSON files.")
    records = []
    seen = set()
    for path in sorted(root.glob("*.json")):
        if path.is_symlink() or path.stat().st_size > 1_000_000:
            raise ValueError("Dataset files must be ordinary JSON files below 1 MB.")
        raw = path.read_bytes()
        case = json.loads(raw)
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError(f"Dataset file has no task ID: {path.name}")  # noqa: TRY004 - Preserve the Dataset admission error contract.
        case_id = case["id"]
        if case_id != path.stem or case_id in seen:
            raise ValueError(f"Dataset ID/filename mismatch or duplicate: {path.name}")
        if not case_id or not all(char.isascii() and (char.isalnum() or char == "-") for char in case_id):
            raise ValueError("Task IDs must use ASCII letters, numbers, and hyphens.")
        seen.add(case_id)
        if not isinstance(case.get("instruction"), str) or not case["instruction"].strip():
            raise ValueError(f"Dataset task has no instruction: {case_id}")
        evaluator = case.get("evaluator", {})
        getter = evaluator.get("result", {}).get("type")
        supported = getter in SUPPORTED
        records.append(
            {
                "id": case_id,
                "file": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "getter": getter,
                "metric": evaluator.get("func"),
                "supported": supported,
                "selected": case_id in selected_ids,
                "support_note": "Local isolated-Profile lane" if supported else "Not adapted in this first local lane; requires additional setup/state adapters.",
                "definition": case,
            }
        )
    if not records:
        raise ValueError("No JSON tasks were found in dataset-dir.")
    if len(set(selected_ids)) != len(selected_ids) or not selected_ids:
        raise ValueError("Select at least one unique task ID.")
    if set(selected_ids) - seen:
        raise ValueError(f"Unknown selected task IDs: {sorted(set(selected_ids) - seen)}")
    selected = []
    by_id = {record["id"]: record for record in records}
    for case_id in selected_ids:
        record = by_id[case_id]
        if not record["supported"]:
            raise ValueError(f"The local lane does not support task {case_id} ({record['getter']}).")
        validate_selected(record["definition"])
        selected.append(record)
    return records, selected


def validate_selected(case):
    evaluator = case["evaluator"]
    getter = evaluator["result"]["type"]
    expected = evaluator.get("expected", {})
    if expected.get("type") != "rule" or not isinstance(expected.get("rules"), dict):
        raise ValueError("The selected task requires a rule-based evaluator.")
    if set(evaluator) - {"postconfig", "func", "result", "expected"}:
        raise ValueError("The selected evaluator contains unsupported options.")
    if set(evaluator["result"]) != {"type"}:
        raise ValueError("The selected getter contains unsupported options.")
    rules = expected["rules"]
    if getter == "edge_font_size":
        if evaluator["func"] != "check_edge_font_size" or rules.get("type") != "range":
            raise ValueError("The font task requires the original range metric.")
        if set(rules) != {"type", "min", "max"} or not all(type(rules[key]) in (int, float) for key in ("min", "max")):
            raise ValueError("The font metric requires numeric min and max bounds.")
    elif evaluator["func"] != "exact_match" or set(rules) != {"expected"}:
        raise ValueError("The selected settings task requires the original exact_match metric.")
    if case.get("snapshot") not in {"msedge", "edge"}:
        raise ValueError("The local lane requires an Edge snapshot label.")
    # Translate known lifecycle intent; never execute commands obtained from Dataset JSON.
    for action in case.get("config", []):
        if action.get("type") != "launch":
            raise ValueError("This local lane supports only Edge launch initialization.")
        command = action.get("parameters", {}).get("command")
        if command not in (
            ["start", "msedge", "--remote-debugging-port=1337"],
            ["socat", "tcp-listen:9222,fork", "tcp:localhost:1337"],
            ["cmd", "/c", "start msedge"],
        ):
            raise ValueError("The dataset launch request is not an admitted Edge initialization.")
    for action in evaluator.get("postconfig", []):
        kind, params = action.get("type"), action.get("parameters", {})
        valid = (
            (kind == "execute" and params == {"command": "taskkill /IM msedge.exe /F", "shell": "true"})
            or (kind == "sleep" and params == {"seconds": 2})
            or (kind == "launch" and params == {"command": ["start", "msedge", "--remote-debugging-port=1337"]})
        )
        if not valid:
            raise ValueError("The dataset postconfig is not an admitted close/flush/reopen request.")


def score_preferences(case, preferences):
    getter = case["evaluator"]["result"]["type"]
    rules = case["evaluator"]["expected"]["rules"]
    if getter not in SUPPORTED:
        raise ValueError(f"Unsupported state getter: {getter}")
    path = SUPPORTED[getter]
    value = preferences
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"The private Profile lacks required evidence: {path}")
        value = value[key]
    if getter == "edge_font_size":
        if type(value) not in (int, float):
            raise ValueError("The font-size evidence is not numeric.")
        result = float(rules["min"] <= value < rules["max"])
    else:
        result = float(value == rules["expected"])
    return {
        "evaluation_status": "valid",
        "benchmark_score": result,
        "getter": getter,
        "metric": case["evaluator"]["func"],
        "observed": value,
        "expected": rules,
    }


def seed_profile(profile, downloads):
    profile = Path(profile)
    preferences = profile / "Default" / "Preferences"
    if profile.exists():
        raise ValueError("A fresh private Profile is required; existing directories are not reset in place.")
    downloads.mkdir(parents=True, exist_ok=False)
    preferences.parent.mkdir(parents=True)
    baseline = {
        "profile": {"name": "FSQ Eval", "exit_type": "Normal"},
        "webkit": {"webprefs": {"default_font_size": 16, "default_fixed_font_size": 13}},
        "download": {"default_directory": str(downloads), "prompt_for_download": True},
        "browser": {"has_seen_welcome_page": True},
    }
    preferences.write_text(json.dumps(baseline), encoding="utf-8")
    return baseline
