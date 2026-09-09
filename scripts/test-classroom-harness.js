const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const name = "classroom-slide-design-harness";
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "aaalab-slide-test-"));
function run(args, expected = 0) {
  const result = spawnSync(process.execPath, [path.join(temp, "bin", "aaalab.js"), ...args], { encoding: "utf8" });
  assert.equal(result.status, expected, result.stdout + result.stderr);
  return result.stdout + result.stderr;
}
try {
  fs.mkdirSync(path.join(temp, "bin"));
  fs.copyFileSync(path.join(root, "package.json"), path.join(temp, "package.json"));
  fs.copyFileSync(path.join(root, "bin", "aaalab.js"), path.join(temp, "bin", "aaalab.js"));
  const pack = path.join(temp, "harnesses", name);
  fs.cpSync(path.join(root, "harnesses", name), pack, { recursive: true });
  assert.match(run(["list"]), new RegExp(name));
  run(["validate", name]);

  const home = path.join(temp, "isolated-agent-home");
  fs.mkdirSync(home);
  fs.writeFileSync(path.join(home, "keep.txt"), "unrelated user file");
  run(["install", name, "--agent-home", home]);
  const installed = path.join(home, "skills", name);
  assert.equal(fs.readFileSync(path.join(home, "keep.txt"), "utf8"), "unrelated user file");
  for (const file of ["SKILL.md", "LICENSE", "NOTICE", "references/roles.md", "scripts/audit_pptx.py"]) {
    assert.ok(fs.statSync(path.join(installed, file)).isFile(), `Installed file missing: ${file}`);
  }
  assert.equal(fs.readFileSync(path.join(installed, "SKILL.md"), "utf8"), fs.readFileSync(path.join(pack, "skills", name, "SKILL.md"), "utf8"));

  const briefPath = path.join(pack, "skills", name, "templates", "brief.example.json");
  const original = fs.readFileSync(briefPath, "utf8");
  const brief = JSON.parse(original);
  brief.explanation_minutes += 1;
  fs.writeFileSync(briefPath, JSON.stringify(brief));
  assert.match(run(["validate", name], 1), /speaking times/);
  fs.writeFileSync(briefPath, "not JSON");
  assert.match(run(["validate", name], 1), /Invalid classroom slide JSON/);
  fs.writeFileSync(briefPath, original);

  const roles = path.join(pack, "skills", name, "references", "roles.md");
  const roleText = fs.readFileSync(roles);
  fs.unlinkSync(roles);
  assert.match(run(["validate", name], 1), /Missing classroom slide harness files/);
  fs.writeFileSync(roles, roleText);
  fs.writeFileSync(path.join(pack, "unexpected.exe"), "test fixture only");
  assert.match(run(["validate", name], 1), /vendored binary/);
  fs.unlinkSync(path.join(pack, "unexpected.exe"));
  run(["validate", name]);
  console.log("Classroom slide harness CLI regression tests passed.");
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}
