const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const root = path.resolve(__dirname, "..");
const name = "qsar-autoresearch-harness";
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "aaalab-qsar-skills-"));
function run(command, args, expected = 0) {
  const result = spawnSync(command, args, { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, expected, `${command}: ${result.error || ""}\n${result.stdout}\n${result.stderr}`);
  return result.stdout;
}
try {
  fs.mkdirSync(path.join(temp, "bin"));
  fs.copyFileSync(path.join(root, "bin", "aaalab.js"), path.join(temp, "bin", "aaalab.js"));
  fs.copyFileSync(path.join(root, "package.json"), path.join(temp, "package.json"));
  const pack = path.join(temp, "harnesses", name);
  fs.cpSync(path.join(root, "harnesses", name), pack, { recursive: true });
  const harness = path.join(pack, "skills", name);
  const mapPath = path.join(harness, "references", "agent-skills.json");
  const mapBytes = fs.readFileSync(mapPath);
  const api = require(path.join(harness, "scripts", "validate_skill_map.js"));
  const cli = (...args) => run(process.execPath, [path.join(temp, "bin", "aaalab.js"), ...args]);
  cli("validate", name);
  // Exercise the real CLI branch with isolated subprocess results: no PATH mutation.
  const vm = require("node:vm");
  const cliSource = fs.readFileSync(path.join(root, "bin", "aaalab.js"), "utf8");
  for (const scenario of [
    { versionOK: false, modulesOK: true, expected: 1 },
    { versionOK: true, modulesOK: true, expected: 0 },
    { versionOK: true, modulesOK: false, expected: 1 }
  ]) {
    const messages = [];
    let versionChecked = false;
    const fakeProcess = { platform: process.platform,
      argv: [process.execPath, "aaalab.js", "runtime-check", name], exitCode: 0 };
    vm.runInNewContext(cliSource, {
      __dirname: path.join(root, "bin"), process: fakeProcess,
      console: { log: value => messages.push(value) },
      require: module => module === "child_process" ? {
        spawnSync: (command, args) => {
          if (command !== "python") return { status: 0, stdout: "fixture-python" };
          if (args[1].includes("sys.version_info >= (3, 11)")) {
            versionChecked = true;
            return { status: scenario.versionOK ? 0 : 1 };
          }
          return { status: scenario.modulesOK ? 0 : 1 };
        }
      } : require(module)
    });
    assert.ok(versionChecked, "QSAR must check the minimum Python version");
    assert.equal(fakeProcess.exitCode, scenario.expected);
    assert.ok(messages.some(m => m.includes(scenario.expected ? "must stop" : "core runtime appears available")));
  }

  if (process.platform === "win32") {
    const checker = path.join(harness, "scripts", "check_runtime_dependencies.ps1").replace(/'/g, "''");
    for (const versionOK of [false, true]) {
      const command = `function python { if ($args[1] -like '*sys.version_info*') { $global:LASTEXITCODE = ${versionOK ? 0 : 1} } else { $global:LASTEXITCODE = 0 } }; & '${checker}'; exit $LASTEXITCODE`;
      const output = run("powershell.exe", ["-NoProfile", "-Command", command], versionOK ? 0 : 1);
      const checks = JSON.parse(output);
      assert.equal(checks.find(c => c.name === "python >= 3.11").available, versionOK);
    }
  }
  const training = api.dispatch("training", false, harness);
  assert.equal(training.agent_type, "executor");
  assert.equal(training.fork_turns, "none");
  assert.equal(training.preferred_agent_type, "qsar-trainer");
  assert.equal(training.role_availability, "catalog_check_required");
  assert.equal(api.dispatch("training", false, harness, ["qsar-trainer"]).agent_type, "qsar-trainer");
  assert.equal(api.dispatch("training", false, harness, ["executor"]).role_availability, "explicit_generic_fallback");
  assert.throws(() => api.dispatch("training", false, harness, ["explore"]), /No supported role/);
  assert.equal(training.stage_mode, "training");
  assert.equal(training.required_reads.length, 3);
  assert.ok(training.required_reads.some(p => p.endsWith(path.join("qsar-train", "SKILL.md"))));
  assert.ok(!training.required_reads.some(p => p.includes("qsar-collect")));
  const review = api.dispatch("training", true, harness);
  assert.equal(review.agent_type, "verifier");
  assert.equal(review.fork_turns, "none");
  assert.ok(review.run_context_required.includes("producer_execution_id"));
  assert.equal(review.separate_from_producer, true);
  assert.ok(review.required_reads.some(p => p.endsWith(path.join("qsar-review", "SKILL.md"))));
  assert.throws(() => api.dispatch("unknown", false, harness), /Unknown research stage/);

  assert.throws(() => api.dispatch("qsar-evolve", false, harness), /Unknown research stage/);
  const maintenance = path.join(pack, "skills", "qsar-evolve", "SKILL.md");
  const maintenanceBytes = fs.readFileSync(maintenance);
  fs.unlinkSync(maintenance);
  assert.throws(() => api.validate(harness), /Missing or outside-pack/);
  for (const malformed of [
    "---\nname: wrong-name\ndescription: maintenance\n---\n",
    "---\nname: qsar-evolve\n---\ndescription: only in body\n",
    "---\nname: qsar-evolve\ndescription: maintenance without closing delimiter\n"
  ]) {
    fs.writeFileSync(maintenance, malformed);
    assert.throws(() => api.validate(harness), /Invalid skill frontmatter/);
  }
  fs.writeFileSync(maintenance, maintenanceBytes);

  const mutate = (change, pattern) => {
    const doc = JSON.parse(mapBytes);
    change(doc);
    fs.writeFileSync(mapPath, JSON.stringify(doc));
    assert.throws(() => api.validate(harness), pattern);
    fs.writeFileSync(mapPath, mapBytes);
  };
  mutate(m => { m.stages[5].requires = []; }, /upstream acceptance/);
  mutate(m => { m.stages[5].reviewer.distinct_execution = false; }, /Independent review/);
  mutate(m => { m.stages[5].producer.agent_type = "worker"; }, /native role/);
  mutate(m => { m.stages[5].producer.native_agent_type = "qsar-data-reviewer"; }, /Native role ownership mismatch/);
  mutate(m => { m.stages[5].terminal_access = true; }, /terminal evaluation access/);
  mutate(m => { m.stages[8].condition = "any model result"; }, /reviewed STOP/);
  mutate(m => { m.shared_protocol = "../../outside.md"; }, /outside-pack/);
  const missing = path.join(pack, "skills", "qsar-curate", "SKILL.md");
  const saved = fs.readFileSync(missing); fs.unlinkSync(missing);
  assert.throws(() => api.validate(harness), /Missing or outside-pack/);
  fs.writeFileSync(missing, saved);

  const verifyInstall = home => {
    const names = fs.readdirSync(path.join(pack, "skills")).filter(n => fs.existsSync(path.join(pack, "skills", n, "SKILL.md")));
    assert.deepEqual(names.sort(), ["qsar-autoresearch", "qsar-autoresearch-harness",
      "qsar-collect", "qsar-curate", "qsar-evaluate", "qsar-evolve", "qsar-features",
      "qsar-improve", "qsar-preprocess", "qsar-review", "qsar-train"].sort());
    assert.equal(fs.readFileSync(path.join(home, "skills", "evolve", "SKILL.md"), "utf8"), "generic evolve sentinel");
    for (const n of names) assert.deepEqual(fs.readFileSync(path.join(home, "skills", n, "SKILL.md")), fs.readFileSync(path.join(pack, "skills", n, "SKILL.md")));
    assert.equal(fs.readFileSync(path.join(home, "skills", "unrelated", "keep.txt"), "utf8"), "preserve");
    require(path.join(home, "skills", name, "scripts", "validate_skill_map.js")).validate(path.join(home, "skills", name));
  };
  const newHome = label => {
    const home = path.join(temp, label);
    fs.mkdirSync(path.join(home, "skills", "unrelated"), { recursive: true });
    fs.mkdirSync(path.join(home, "skills", "evolve"), { recursive: true });
    fs.writeFileSync(path.join(home, "skills", "evolve", "SKILL.md"), "generic evolve sentinel");
    // User homes/projects can impose ESM on every installed .js descendant.
    fs.writeFileSync(path.join(home, "package.json"), JSON.stringify({ type: "module" }));
    fs.writeFileSync(path.join(home, "skills", "unrelated", "keep.txt"), "preserve");
    return home;
  };
  const nodeHome = newHome("node-home");
  cli("install", name, "--agent-home", nodeHome); verifyInstall(nodeHome);
  if (process.platform === "win32") {
    const psHome = newHome("powershell-home");
    run("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.join(pack, "install.ps1"), "-AgentHome", psHome]);
    verifyInstall(psHome);
    // Exercise replacement path as well as a fresh installation.
    run("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.join(pack, "install.ps1"), "-AgentHome", psHome]);
    verifyInstall(psHome);
  }
  const gitBash = process.platform === "win32" ? "C:/Program Files/Git/bin/bash.exe" : "/bin/bash";
  if (fs.existsSync(gitBash)) {
    const shellScript = path.join(pack, "install.sh").replace(/\\/g, "/");
    run(gitBash, ["-n", shellScript]);
    const bashHome = newHome("bash-home");
    run(gitBash, [shellScript, bashHome.replace(/\\/g, "/")]);
    verifyInstall(bashHome);
    fs.unlinkSync(missing);
    run(gitBash, [shellScript, bashHome.replace(/\\/g, "/")], 1);
    assert.deepEqual(fs.readFileSync(path.join(bashHome, "skills", "qsar-curate", "SKILL.md")), saved);
    fs.writeFileSync(missing, saved);
  }
  console.log("QSAR skill routing, rejected invalid mappings, and isolated installs passed; no scientific validation implied.");
} finally {
  const target = fs.realpathSync(temp);
  const parent = fs.realpathSync(os.tmpdir());
  if (path.dirname(target) !== parent || !path.basename(target).startsWith("aaalab-qsar-skills-")) throw new Error("Unsafe test cleanup target");
  fs.rmSync(target, { recursive: true, force: true });
}
