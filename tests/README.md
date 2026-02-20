# Tests

Tests verify that execution logic is correct and stays correct.

## Rules

- **Tests are first-class citizens.** Write tests before or alongside new logic, never after.
- **Unit tests are required for all non-trivial logic.** Pure functions, transformations, and decisions must be covered. Mock all I/O.
- **Never touch production.** Tests run against mocks, sandboxes, or test databases only.
- **Integration and E2E tests require explicit opt-in.** Gate them behind an environment flag (e.g. `RUN_INTEGRATION=true`) or a CI label. They must never fire automatically against real systems.

## Test Types

| Type | Scope | Runs locally? | Touches prod? |
|------|-------|---------------|---------------|
| Unit | Single function / module | Yes, always | Never |
| Integration | Multiple modules + external deps | Opt-in only | Never |
| E2E / UI | Full flows via browser (Playwright) | Opt-in only | Never |

## Conventions

- Mirror the structure of `/execution` — one test file per script
- Test files named `test_<script_name>` or `<script_name>.test.<ext>`
- One-command execution: `npm test`, `pytest`, or equivalent must work from repo root
