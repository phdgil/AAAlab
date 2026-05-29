# AAAlab

<p align="center">
  <img src="assets/aaalab-logo.png" alt="AAAlab logo" width="220">
</p>

AAAlab stands for Autonomous AI Agent.

AAAlab is a collection of agent harnesses for scientific and engineering workflows.

The first harness is:

| Harness | Purpose | Install name |
|---|---|---|
| [autodock-vina-harness](harnesses/autodock-vina-harness/README.md) | Multi-agent AutoDock Vina / gnina docking execution, validation, reruns, troubleshooting, and QA | `autodock-vina-harness` |

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
```

One-shot:

```bash
npx --yes github:phdgil/AAAlab install autodock-vina-harness
```

Custom agent home:

```bash
aaalab install autodock-vina-harness --agent-home "$HOME/.codex"
```

From a local clone:

PowerShell:

```powershell
.\install.ps1 -Harness autodock-vina-harness
```

macOS/Linux:

```bash
./install.sh autodock-vina-harness
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
```

Each harness directory is expected to be self-contained enough to install independently, while the repo root provides shared validation and install entry points.

## GitHub Actions

The validation workflow at [.github/workflows/validate.yml](.github/workflows/validate.yml) runs all harness validators on push and pull request.

## License

AAAlab is licensed under Apache-2.0. Individual harnesses may include their own license audits and third-party notices for external tools they reference.
