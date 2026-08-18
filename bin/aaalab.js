#!/usr/bin/env node

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const repoRoot = path.resolve(__dirname, "..");
const harnessesRoot = path.join(repoRoot, "harnesses");

function usage(exitCode = 0) {
  const text = `AAAlab harness manager

Usage:
  aaalab list
  aaalab install [harness] [--agent-home <path>]
  aaalab validate [harness]
  aaalab runtime-check [harness]

Examples:
  aaalab install
  aaalab install autodock-vina-harness
  aaalab install autodock-vina-harness --agent-home "$HOME/.codex"
  aaalab validate
  aaalab runtime-check autodock-vina-harness
`;
  console.log(text.trim());
  process.exit(exitCode);
}

function fail(message) {
  console.error(`Error: ${message}`);
  process.exit(1);
}

function readText(file) {
  return fs.readFileSync(file, "utf8");
}

function exists(file) {
  return fs.existsSync(file);
}

function listHarnesses(pattern = "*") {
  if (!exists(harnessesRoot)) {
    fail(`Missing harnesses directory: ${harnessesRoot}`);
  }
  const all = fs.readdirSync(harnessesRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();
  if (pattern === "*" || !pattern) return all;
  return all.filter((name) => name === pattern);
}

function parseOptions(args) {
  const out = { positional: [] };
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--agent-home" || arg === "--codex-home") {
      i += 1;
      if (!args[i]) fail(`${arg} requires a path`);
      out.agentHome = path.resolve(args[i]);
    } else if (arg === "-h" || arg === "--help") {
      usage(0);
    } else {
      out.positional.push(arg);
    }
  }
  return out;
}

function defaultAgentHome() {
  return process.env.AAALAB_AGENT_HOME || process.env.CODEX_HOME || path.join(os.homedir(), ".codex");
}

function copyDir(source, target) {
  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(source, target, { recursive: true });
}

function copyHarnessNotices(harnessDir, harnessName, targetSkills) {
  const targetHarnessSkill = path.join(targetSkills, harnessName);
  if (!exists(targetHarnessSkill)) return;

  for (const file of ["LICENSE", "NOTICE", "LICENSE_AUDIT.md", "THIRD_PARTY_NOTICES.md"]) {
    const source = path.join(harnessDir, file);
    if (exists(source)) {
      fs.copyFileSync(source, path.join(targetHarnessSkill, file));
    }
  }
}

function installHarness(harnessName, agentHome) {
  const harnessDir = path.join(harnessesRoot, harnessName);
  const skillsDir = path.join(harnessDir, "skills");
  if (!exists(skillsDir)) {
    fail(`Harness has no skills directory: ${skillsDir}`);
  }
  const targetSkills = path.join(agentHome, "skills");
  fs.mkdirSync(targetSkills, { recursive: true });

  const skills = fs.readdirSync(skillsDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();

  if (skills.length === 0) {
    fail(`Harness has no installable skills: ${harnessName}`);
  }

  for (const skill of skills) {
    const source = path.join(skillsDir, skill);
    const target = path.join(targetSkills, skill);
    copyDir(source, target);
    console.log(`Installed ${skill} -> ${target}`);
  }

  copyHarnessNotices(harnessDir, harnessName, targetSkills);
}

function assertContains(content, needle, label) {
  if (!content.includes(needle)) {
    fail(`Dependency contract missing: ${label}`);
  }
}

function assertRegex(content, regex, label) {
  if (!regex.test(content)) {
    fail(`Dependency contract missing: ${label}`);
  }
}

function collectFiles(dir) {
  const files = [];
  function walk(current) {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) walk(full);
      else files.push(full);
    }
  }
  walk(dir);
  return files;
}

function validateNoVendoredBinaries(harnessDir) {
  const forbidden = new Set([".exe", ".dll", ".so", ".dylib", ".whl", ".tar", ".gz", ".zip"]);
  const matches = collectFiles(harnessDir).filter((file) => forbidden.has(path.extname(file).toLowerCase()));
  if (matches.length > 0) {
    fail(`Potential vendored binary/archive dependencies found:\n${matches.join("\n")}`);
  }
}

function validateAutodockVinaHarness(harnessDir) {
  const harnessSkillRoot = path.join(harnessDir, "skills", "autodock-vina-harness");
  const protocolRoot = path.join(harnessDir, "skills", "autodock-vina");
  const required = [
    path.join(harnessDir, "README.md"),
    path.join(harnessDir, "LICENSE_AUDIT.md"),
    path.join(harnessDir, "THIRD_PARTY_NOTICES.md"),
    path.join(harnessSkillRoot, "SKILL.md"),
    path.join(harnessSkillRoot, "README.md"),
    path.join(harnessSkillRoot, "agents", "openai.yaml"),
    path.join(harnessSkillRoot, "references", "trigger-tests.md"),
    path.join(harnessSkillRoot, "references", "agents", "structure-box-lead.md"),
    path.join(harnessSkillRoot, "references", "agents", "validation-lead.md"),
    path.join(harnessSkillRoot, "references", "agents", "prep-lead.md"),
    path.join(harnessSkillRoot, "references", "agents", "docking-runner.md"),
    path.join(harnessSkillRoot, "references", "agents", "qa-reviewer.md"),
    path.join(harnessSkillRoot, "scripts", "validate_harness.ps1"),
    path.join(harnessSkillRoot, "scripts", "verify_dependency_contract.ps1"),
    path.join(harnessSkillRoot, "scripts", "check_runtime_dependencies.ps1"),
    path.join(protocolRoot, "SKILL.md"),
    path.join(protocolRoot, "references", "vina-draft-protocol.md")
  ];

  const missing = required.filter((file) => !exists(file));
  if (missing.length > 0) {
    fail(`Missing required harness files:\n${missing.join("\n")}`);
  }

  const harnessSkill = readText(path.join(harnessSkillRoot, "SKILL.md"));
  const protocolSkill = readText(path.join(protocolRoot, "SKILL.md"));
  const protocolReference = readText(path.join(protocolRoot, "references", "vina-draft-protocol.md"));
  const licenseAudit = readText(path.join(harnessDir, "LICENSE_AUDIT.md"));
  const thirdParty = readText(path.join(harnessDir, "THIRD_PARTY_NOTICES.md"));
  const combinedProtocol = `${harnessSkill}\n${protocolSkill}\n${protocolReference}`;

  for (const needle of [
    "name: autodock-vina-harness",
    "Phase 0: Context Check",
    "QA Review",
    "Test Scenarios"
  ]) {
    assertContains(harnessSkill, needle, `SKILL.md must include ${needle}`);
  }

  assertContains(harnessSkill, "Verify at least one PDBQT prep path exists: Meeko, MGLTools/AutoDockTools, or Open Babel", "harness preflight must preserve all PDBQT prep alternatives");
  assertContains(harnessSkill, "Check RDKit or another conformer path", "harness must require a conformer path for SMILES or 2D ligand generation");
  assertContains(harnessSkill, "If gnina is requested, check Docker or native gnina", "gnina must remain optional and explicitly checked");
  assertContains(harnessSkill, "If Vina or all PDBQT preparation paths are missing, stop and report missing dependencies", "missing runtime dependencies must stop execution");
  assertContains(protocolSkill, "Verify `vina -h` works", "base protocol must verify Vina before docking");
  assertContains(protocolSkill, "If `vina` or PDBQT preparation tools are missing, stop", "base protocol must fail closed when required tools are missing");
  assertContains(protocolSkill, "never parse the CNN pose score as CNN affinity", "gnina parser safety rule must remain");

  for (const tool of ["AutoDock Vina", "gnina", "Meeko", "MGLTools", "AutoDockTools", "Open Babel", "RDKit", "Datamol", "PDBFixer", "3Dmol.js", "py3Dmol", "Docker"]) {
    assertRegex(combinedProtocol, new RegExp(tool.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), `workflow/protocol must still mention ${tool}`);
  }

  for (const tool of ["AutoDock Vina", "gnina", "Meeko", "Open Babel", "RDKit", "Datamol", "PDBFixer", "3Dmol.js", "py3Dmol"]) {
    assertRegex(licenseAudit, new RegExp(tool.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), `license audit must mention ${tool}`);
    assertRegex(thirdParty, new RegExp(tool.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), `third-party notices must mention ${tool}`);
  }

  validateNoVendoredBinaries(harnessDir);
}

function validateHarness(harnessName) {
  const harnessDir = path.join(harnessesRoot, harnessName);
  if (harnessName === "autodock-vina-harness") {
    validateAutodockVinaHarness(harnessDir);
  } else {
    const skillsDir = path.join(harnessDir, "skills");
    if (!exists(path.join(harnessDir, "README.md"))) fail(`Missing README.md for ${harnessName}`);
    if (!exists(skillsDir)) fail(`Missing skills directory for ${harnessName}`);
    validateNoVendoredBinaries(harnessDir);
  }
  console.log(`${harnessName}: OK`);
}

function commandAvailable(name) {
  const command = process.platform === "win32" ? "where" : "command";
  const args = process.platform === "win32" ? [name] : ["-v", name];
  const result = spawnSync(command, args, { encoding: "utf8", shell: process.platform !== "win32" });
  return {
    name,
    available: result.status === 0,
    source: result.stdout ? result.stdout.trim().split(/\r?\n/)[0] : ""
  };
}

function pythonModuleAvailable(name) {
  const py = commandAvailable("python");
  if (!py.available) {
    return { name: `python module: ${name}`, available: false, source: "python not found" };
  }
  const code = `import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(${JSON.stringify(name)}) else 1)`;
  const result = spawnSync("python", ["-c", code], { encoding: "utf8" });
  return { name: `python module: ${name}`, available: result.status === 0, source: "python" };
}

function runtimeCheck(harnessName) {
  if (harnessName === "autodock-vina-harness") {
    const checks = [
      commandAvailable("vina"),
      commandAvailable("obabel"),
      commandAvailable("mk_prepare_receptor.py"),
      commandAvailable("mk_prepare_ligand.py"),
      commandAvailable("prepare_receptor4.py"),
      commandAvailable("prepare_ligand4.py"),
      commandAvailable("gnina"),
      commandAvailable("docker"),
      commandAvailable("python"),
      pythonModuleAvailable("rdkit"),
      pythonModuleAvailable("meeko"),
      pythonModuleAvailable("py3Dmol")
    ];

    for (const check of checks) {
      const status = check.available ? "available" : "missing";
      console.log(`${check.name}: ${status}${check.source ? ` (${check.source})` : ""}`);
    }

    const hasVina = checks.find((item) => item.name === "vina").available;
    const hasPrep = checks.some((item) => [
      "obabel",
      "mk_prepare_receptor.py",
      "mk_prepare_ligand.py",
      "prepare_receptor4.py",
      "prepare_ligand4.py",
      "python module: meeko"
    ].includes(item.name) && item.available);
    const hasGnina = checks.some((item) => ["gnina", "docker"].includes(item.name) && item.available);

    if (!hasVina || !hasPrep) {
      console.log("Preflight verdict: real docking must stop and report missing dependencies.");
    } else {
      console.log("Preflight verdict: Vina docking runtime appears available.");
    }

    if (!hasGnina) {
      console.log("gnina verdict: gnina follow-up must be skipped unless installed.");
    } else {
      console.log("gnina verdict: gnina follow-up may proceed after a real CNN smoke test when GPU scoring is intended.");
    }
    return;
  }

  if (harnessName === "qsar-autoresearch-harness") {
    const checks = [
      commandAvailable("python"),
      pythonModuleAvailable("pandas"),
      pythonModuleAvailable("sklearn"),
      pythonModuleAvailable("openpyxl"),
      pythonModuleAvailable("rdkit")
    ];

    for (const check of checks) {
      const status = check.available ? "available" : "missing";
      console.log(`${check.name}: ${status}${check.source ? ` (${check.source})` : ""}`);
    }

    const hasPython = checks.find((item) => item.name === "python").available;
    const hasCore = checks.every((item) => item.name === "python" || item.available);

    if (!hasPython || !hasCore) {
      console.log("Preflight verdict: QSAR harness must stop and report missing Python or modeling dependencies.");
    } else {
      console.log("Preflight verdict: QSAR workbook runtime appears available.");
    }
    return;
  }

  if (harnessName === "research-manuscript-harness") {
    const checks = [
      commandAvailable("python"),
      pythonModuleAvailable("docx"),
      pythonModuleAvailable("PIL")
    ];

    for (const check of checks) {
      const status = check.available ? "available" : "missing";
      console.log(`${check.name}: ${status}${check.source ? ` (${check.source})` : ""}`);
    }

    const hasPython = checks.find((item) => item.name === "python").available;
    const hasDocumentTools = checks.every((item) => item.name === "python" || item.available);
    if (!hasPython || !hasDocumentTools) {
      console.log("Preflight verdict: Markdown review may proceed, but DOCX mutation or verification must stop and report missing python-docx or Pillow.");
    } else {
      console.log("Preflight verdict: research manuscript DOCX runtime appears available.");
    }
    return;
  }

  fail(`No runtime-check implementation for ${harnessName}`);
}

function main() {
  const [command, ...rest] = process.argv.slice(2);
  if (!command || command === "-h" || command === "--help") usage(0);

  if (command === "list") {
    for (const name of listHarnesses("*")) console.log(name);
    return;
  }

  if (command === "install") {
    const opts = parseOptions(rest);
    const harnessPattern = opts.positional[0] || "*";
    const harnesses = listHarnesses(harnessPattern);
    if (harnesses.length === 0) fail(`No harness matched: ${harnessPattern}`);
    const agentHome = opts.agentHome || defaultAgentHome();
    for (const harness of harnesses) {
      console.log(`Installing harness: ${harness}`);
      installHarness(harness, agentHome);
    }
    console.log("Restart your agent runtime so its skill or harness registry reloads.");
    return;
  }

  if (command === "validate") {
    const opts = parseOptions(rest);
    const harnessPattern = opts.positional[0] || "*";
    const harnesses = listHarnesses(harnessPattern);
    if (harnesses.length === 0) fail(`No harness matched: ${harnessPattern}`);
    for (const harness of harnesses) validateHarness(harness);
    console.log("All harness validators passed.");
    return;
  }

  if (command === "runtime-check") {
    const opts = parseOptions(rest);
    runtimeCheck(opts.positional[0] || "autodock-vina-harness");
    return;
  }

  usage(1);
}

main();
