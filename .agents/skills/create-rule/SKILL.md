---
name: create-rule
description: >-
  Create or refine persistent project instructions in AGENTS.md and scoped
  Cursor rules in .cursor/rules/*.mdc. Use for repository context engineering,
  coding conventions, and requests to improve existing agent rules.
---
# Create useful project context

Produce the smallest set of maintained instructions that materially helps an
agent perform the user's work. Prefer concrete repository knowledge and
operational invariants over generic coding advice.

## Inspect before writing

- Read applicable existing instructions and inspect the relevant repository
  files, commands, and user requirements. Preserve instructions with a clear use.
- Infer purpose, scope, and file patterns from the request and actual files.
  Ask only when an unresolved choice would materially change the result.
- Separate durable constraints from changing state and reference material.
  Avoid copying an entire repository overview into always-loaded instructions.
- When asked for current best practices, verify primary sources and read
  [context-engineering.md](references/context-engineering.md). Do not treat
  a vendor suggestion or a small empirical study as a universal guarantee.

## Choose the context surface

- Use a root `AGENTS.md` for concise project-wide guidance and the entry points
  needed to start work. Honor the agent and formats already used by the project.
- Use `.cursor/rules/*.mdc` when Cursor-specific discovery or conditional
  attachment is useful. Put file-specific invariants behind verified glob
  patterns rather than loading them for unrelated tasks.
- Keep detailed procedures and evidence in existing documentation or a skill;
  link them with a short explanation of when they should be read.
- Keep changing progress in a state or handoff document when work spans sessions.
  Include the current task, verified results, open decisions, and next action.
- Maintain one authoritative location for each instruction. A compatibility
  rule may point to `AGENTS.md`; do not duplicate its entire body.

## Write actionable instructions

- Record non-obvious requirements that affect a decision: a measured limitation,
  a real command, a required output contract, or a demonstrated failure mode.
- Link existing canonical code or examples instead of restating implementation.
- Distinguish requirements, recommendations, and unverified assumptions.
- Preserve the user's current authorization and preferences. Rules must not
  create new approval gates, standing permissions, or unrelated work.
- Date changing technical claims and include their evidence. Update or remove
  stale state when the project advances.
- Do not embed secrets, personal machine paths, raw logs, or unrelated history.

## Cursor rule syntax

Use the current Cursor documentation when behavior is uncertain. Typical
project-wide rule:

```mdc
---
description: Project entry point
alwaysApply: true
---
Follow the repository's AGENTS.md.
```

Typical conditional rule:

```mdc
---
description: Invariants for benchmark measurement
globs: "**/*benchmark*.py"
alwaysApply: false
---
See docs/benchmark-design.md before changing timing or cache behavior.
```

Descriptions explain relevance; glob patterns must match the intended existing
files. Do not promise scoped behavior for a plain Markdown file or an agent
whose discovery behavior has not been verified.

## Verify and maintain

- Check referenced paths, commands, formats, and glob matches in the real repo.
- Review for contradictions, duplicate instructions, stale status, and
  unnecessary always-loaded material. Line count is a signal, not the objective.
- Check the change against a realistic task: does it expose the necessary
  constraint and give the agent a working next action without extra obligations?
- For substantial changes, use an authorized behavioral evaluation if it adds
  useful evidence. Match validation effort to the actual risk.
- Report which files changed and why. Describe improved structure separately
  from any measured improvement in agent performance.
