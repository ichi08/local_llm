# Evidence and practical decisions

Primary sources checked on 2026-10-03. Recheck tooling behavior when it matters.

## Evidence

- [Anthropic, Effective context engineering for AI agents, 2025-09-29](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents):
  curate relevant context, retrieve details when needed, and keep structured
  notes for long work. Minimal context must still contain the necessary detail.
- [Cursor, Rules](https://prod.cursor.com/help/customization/rules):
  root AGENTS.md is an instruction entry point; project rules support conditions
  and references. Begin with concrete needs and expand based on observed errors.
- [Evaluating AGENTS.md, 2026-02-12](https://arxiv.org/abs/2602.11988):
  unnecessary requirements can reduce task success and increase cost in the
  evaluated coding-agent settings. This supports removing redundant obligations,
  not discarding necessary project-specific constraints.
- [Do Context Files Help Coding Agents?, 2026-07-28](https://arxiv.org/abs/2607.27250):
  an ablation involving two agents and a limited set of real tasks did not find
  a measurable correctness improvement from its context strategies. Outcomes
  depend on tasks and agents; added files alone do not prove better performance.

## Apply the evidence

- Always-loaded context: purpose, durable constraints, and a short starting map.
- Conditional context: invariants that apply to a specific subsystem or format.
- Retrieved context: detailed procedures, examples, architecture, research.
- Handoff state: current phase, completed verification, decisions, next action.
- Evaluation: observe an actual task or known failure before claiming benefit.

These are design choices inferred from the sources, not a universal file layout.
Respect existing project conventions and the user's requested scope.
