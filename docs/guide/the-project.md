# The project

Nora is an open-source study of assistants that live with people over time.
It is developed in the open by one maintainer, with AI coding agents doing
much of the building under written rules.

## What the project is trying to learn

- Whether an assistant can take initiative in shared spaces without becoming
  noise: speaking when it matters, and staying quiet on purpose otherwise.
- Whether memory, commitments and learning work better as one connected
  understanding than as separate features.
- Whether an assistant can measurably learn from the outcomes of its own
  actions, and how to tell real learning from luck.

The headline measure is **adaptation**: replay a decision she actually made,
once as it was and once with what she had learned removed, and check whether
the decision changes by more than replaying it unchanged would.

## How decisions are made

- **Design first, for architecture.** A change to the parts of the system,
  their connections, boundaries or vocabulary is written in the
  [design](../../design/cognition/README.md) before or with the code.
- **Code directly, for everything else.** Fixes, wording and thresholds go
  straight to code with tests.
- **Decisions are recorded.** Lasting decisions and their trade-offs are kept
  as [decision records](../../docs/adr/README.md).
- **Evidence changes decisions.** A measurement or a recorded result can
  reopen a decision; a better-argued restatement does not.

## How AI agents build it

Much of Nora is written by AI coding agents. They follow the rules in
[AGENTS.md](../../AGENTS.md): every change starts on its own branch and is
reviewed independently of the agent that wrote it, and a fix is proven by a
test that fails when the fix is reverted. A claim that something is done needs
a check, not the builder's word.

## Where things are documented

| layer | holds | kept by |
|---|---|---|
| this guide | purpose, concepts and how she works, in plain words | people, with AI-proposed updates |
| the design | the architecture builders work to | people and agents, with each architecture change |
| the code | exact behaviour, tools, settings and data | the code itself, never copied into prose |

## Contributing

Bug reports, focused proposals and pull requests are welcome; see
[Contributing](../../CONTRIBUTING.md). Use made-up examples: never share real
conversations, account identifiers or locations. Report security issues
through [Security](../../SECURITY.md).

## License

The code is MIT licensed. Nora's art is all rights reserved, for building and
running Nora only. See [LICENSE](../../LICENSE).
