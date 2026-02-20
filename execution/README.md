# Execution

Execution contains the runnable scripts and services that do the actual work.

## Rules

- **This is the only place real work happens.** API calls, data processing, database reads/writes, and file operations all live here.
- **No orchestration prose.** Scripts must not contain planning logic, prompts, or decision-making. One script = one clear responsibility.
- **Keep it deterministic and testable.** Core logic must be importable so unit tests can call it directly. Side effects (network, disk) must be isolatable.
- **Safe to rerun.** Scripts must be idempotent or clearly document when they are not.

## Conventions

- One file = one responsibility
- Exported functions are testable; entry-point (`main`) handles I/O
- Never read secrets from the repo; use environment variables
