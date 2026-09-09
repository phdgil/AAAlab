# AAAlab

<p align="center">
  <img src="assets/aaalab-logo.png" alt="AAAlab logo" width="220">
</p>

AAAlab stands for Autonomous AI Agent.

AAAlab is a collection of agent harnesses for scientific and engineering workflows.

Bundled harnesses are:

| Harness | Purpose | Install name |
|---|---|---|
| [autodock-vina-harness](harnesses/autodock-vina-harness/README.md) | Multi-agent AutoDock Vina / gnina docking execution, validation, reruns, troubleshooting, and QA | `autodock-vina-harness` |
| [qsar-autoresearch-harness](harnesses/qsar-autoresearch-harness/README.md) | Multi-agent QSAR workbook auditing, leakage-aware in-vivo preparation, per-table training, bounded next-experiment control, and share-ready result packaging | `qsar-autoresearch-harness` |
| [research-manuscript-harness](harnesses/research-manuscript-harness/README.md) | Multi-agent research-manuscript creation and revision with evidence, methods, abbreviation, citation, figure, DOCX-format, and independent QA gates | `research-manuscript-harness` |
| [classroom-slide-design-harness](harnesses/classroom-slide-design-harness/README.md) | Short visual lectures for project-based classes, with message-specific color, simple English, editable PPTX, rendering, and independent review | `classroom-slide-design-harness` |

## Installation Model

AAAlab has two install layers:

- `npm install -g github:phdgil/AAAlab` installs the `aaalab` command-line manager.
- `aaalab install` installs the bundled harness skills into an agent runtime's skill directory.

The second step is explicit so a global npm install does not silently modify an agent home such as `~/.codex`, `~/.claude`, or another custom runtime directory. By default, `aaalab install` uses `AAALAB_AGENT_HOME`, then `CODEX_HOME`, then `~/.codex`. Use `--agent-home <path>` to install into a different runtime.

## Install All Harnesses

Recommended global install:

```bash
npm install -g github:phdgil/AAAlab
aaalab install
```

One-shot install without keeping a global `aaalab` command:

```bash
npx --yes github:phdgil/AAAlab install
```

Custom agent home:

```bash
aaalab install --agent-home "$HOME/.codex"
```

From a local clone:

PowerShell:

```powershell
.\install.ps1
```

macOS/Linux:

```bash
./install.sh
```

Restart your agent runtime after installation so its skill or harness registry reloads.

## Install One Harness

Recommended:

```bash
aaalab install autodock-vina-harness
aaalab install qsar-autoresearch-harness
aaalab install research-manuscript-harness
aaalab install classroom-slide-design-harness
```

One-shot:

```bash
npx --yes github:phdgil/AAAlab install autodock-vina-harness
npx --yes github:phdgil/AAAlab install qsar-autoresearch-harness
npx --yes github:phdgil/AAAlab install research-manuscript-harness
npx --yes github:phdgil/AAAlab install classroom-slide-design-harness
```

Custom agent home:

```bash
aaalab install autodock-vina-harness --agent-home "$HOME/.codex"
aaalab install qsar-autoresearch-harness --agent-home "$HOME/.codex"
aaalab install research-manuscript-harness --agent-home "$HOME/.codex"
aaalab install classroom-slide-design-harness --agent-home "$HOME/.codex"
```

From a local clone:

PowerShell:

```powershell
.\install.ps1 -Harness autodock-vina-harness
.\install.ps1 -Harness qsar-autoresearch-harness
.\install.ps1 -Harness research-manuscript-harness
.\install.ps1 -Harness classroom-slide-design-harness
```

macOS/Linux:

```bash
./install.sh autodock-vina-harness
./install.sh qsar-autoresearch-harness
./install.sh research-manuscript-harness
./install.sh classroom-slide-design-harness
```

## Validate

Cross-platform npm validation:

```bash
aaalab validate
```

From a local clone:

```bash
npm test
```

## Repository Layout

```text
AAAlab/
  .github/
    workflows/
      validate.yml
  harnesses/
    autodock-vina-harness/
      skills/
        autodock-vina/
        autodock-vina-harness/
    qsar-autoresearch-harness/
      skills/
        qsar-autoresearch/
        qsar-autoresearch-harness/
    research-manuscript-harness/
      skills/
        research-manuscript/
        research-manuscript-harness/
    classroom-slide-design-harness/
      skills/
        classroom-slide-design-harness/
```

Each harness directory is expected to be self-contained enough to install independently, while the repo root provides shared validation and install entry points.

## GitHub Actions

The validation workflow at [.github/workflows/validate.yml](.github/workflows/validate.yml) runs all harness validators on push and pull request.

## License

AAAlab is licensed under Apache-2.0. Individual harnesses may include their own license audits and third-party notices for external tools they reference.
