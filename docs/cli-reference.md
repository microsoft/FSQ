# CLI Reference

FSQ installs `fsq` and the compatibility alias `fsq-agent`. Both invoke the same CLI. Run `fsq COMMAND --help` for the authoritative options of the installed version.

## Global options

| Option | Meaning |
|---|---|
| `--output human|json|jsonl` | Select human or machine-readable output. |
| `--non-interactive` | Reject flows requiring terminal interaction. |

## Public commands

| Command | Purpose | Workspace required |
|---|---|---|
| `fsq init` | Check readiness and initialize one Workspace platform. | No |
| `fsq doctor` | Diagnose configured platforms without mutation. | Yes |
| `fsq providers configure/status` | Configure or inspect the user-level Provider. | No |
| `fsq case create` | Execute one AI-driven goal. | Yes |
| `fsq case test` | Execute an existing Case exactly once. | Yes |
| `fsq case test --suggest` | Execute once, then analyze persisted facts. | Yes |
| `fsq runs list/show/logs` | Query Workspace Run history. | Yes |
| `fsq ui` | Start the local Control Plane. | No |

Except when creating an unregistered Workspace, Workspace commands use the exact current directory. They do not search ancestors or accept an alternate Workspace flag.

Human output is for terminals. JSON produces one structured result. JSONL emits documented events and a terminal result where streaming applies. Invalid command requests use exit code 2; consume stable machine fields rather than human wording.

`init` never installs Driver/Runtime packages or system prerequisites. The public CLI has no `environments` command, `providers list`, or `--install-driver` option.

## Static Case formatting

```bash
fsq case format PATH [--check | --diff | --write] [--json]
fsq case create --platform web --goal "Verify product search" --name product-search
```

`format` defaults to `--check`, works without an initialized Workspace, and uses the same validator and serializer as internal Case generation. `--diff` previews changes; `--write` validates before writing. Modes are exclusive. JSON diagnostics distinguish invalid input from formatting differences. See [Case format](case-format.md#canonical-formatting-and-static-checks) for exit codes, machine fields, and the static validation boundary. Case creation accepts an optional stable suffix-free name; publication never replaces a different existing Case.
