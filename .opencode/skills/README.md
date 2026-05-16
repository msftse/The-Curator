# Skills

This directory contains AI agent skills -- reusable instruction sets and reference documentation that teach AI coding assistants how to use specific SDKs, frameworks, and tools.

## Available Skills

| Skill | Description | Language |
|-------|-------------|----------|
| [agent-browser](.opencode/skills/agent-browser/SKILL.md) | Capture browser screenshots and page state while running iterative agent workflows | Python |
| [e2e-test](.opencode/skills/e2e-test/SKILL.md) | Run and maintain end-to-end tests for validating user flows across the full app stack | Python |
| [azure-mgmt-fabric-py](azure-mgmt-fabric-py/SKILL.md) | Manage Microsoft Fabric capacities and resources | Python |

## Skill Structure

Each skill follows this structure:

```
skill-name/
├── SKILL.md          # Main skill definition (entry point)
└── references/       # Detailed reference documentation
    ├── tools.md
    ├── mcp.md
    └── ...
```

- **SKILL.md** -- The primary file loaded by the AI assistant. Contains architecture overview, installation, core workflows, and quick references.
- **references/** -- Deep-dive docs on specific topics. Referenced from SKILL.md for when the assistant needs more detail.