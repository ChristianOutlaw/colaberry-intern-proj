# Directives

Directives are the rules, learnings, and decisions that govern this system.

## Rules

- **Directives are human-readable SOPs.** Write in plain language a junior developer can follow.
- **No business logic or runnable code.** Directives describe intent and constraints only. Scripts live in `/execution`.
- **Read before you change anything.** Always check relevant directives before writing or modifying code.
- **Update when the system learns.** If a failure reveals a missing rule or a decision is made, update the relevant directive immediately.

## Structure

Each directive file should include:
1. **Purpose** — what this directive governs
2. **Steps or rules** — clear, numbered or bulleted
3. **How success is verified** — what a passing state looks like
