const fs = require("node:fs");
const path = require("node:path");

const stages = ["contract", "collection", "curation", "preprocessing", "features", "training", "development-evaluation", "improvement", "terminal-evaluation", "release"];
const roles = new Set(["analyst", "executor", "verifier", "critic"]);
function validate(harnessRoot = path.resolve(__dirname, "..")) {
  const skillsRoot = path.dirname(harnessRoot);
  const registry = JSON.parse(fs.readFileSync(path.join(harnessRoot, "references", "agent-skills.json"), "utf8"));
  const checkFile = relative => {
    if (typeof relative !== "string") throw new Error("Skill reference must be a string");
    const resolved = path.resolve(skillsRoot, relative);
    const delta = path.relative(skillsRoot, resolved);
    if (delta.startsWith("..") || path.isAbsolute(delta) || !fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
      throw new Error(`Missing or outside-pack skill reference: ${relative}`);
    }
    return resolved;
  };
  if (registry.schema_version !== 1 || !Array.isArray(registry.stages)) throw new Error("Unsupported skill map schema");
  checkFile(registry.shared_protocol);
  checkFile(registry.artifact_contracts);
  if (JSON.stringify(registry.stages.map(s => s.id)) !== JSON.stringify(stages)) throw new Error("Incomplete or misordered research stages");
  for (const [index, stage] of registry.stages.entries()) {
    if (stage.reviewer?.distinct_execution !== true || stage.reviewer?.skill !== "qsar-review") throw new Error(`Independent review missing: ${stage.id}`);
    for (const [kind, assignment] of [["producer", stage.producer], ["reviewer", stage.reviewer]]) {
      if (!assignment || !roles.has(assignment.agent_type) || !/^[a-z0-9-]+$/.test(assignment.skill)) throw new Error(`Invalid native role/skill: ${stage.id}`);
      const native = registry.native_roles?.[assignment.native_agent_type];
      if (!native || native.kind !== kind || native.skill !== assignment.skill || !native.stages?.includes(stage.id)) throw new Error(`Native role ownership mismatch: ${stage.id}/${kind}`);
      const file = checkFile(`${assignment.skill}/SKILL.md`);
      const text = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
      if (!text.startsWith("---\n") || !text.includes(`name: ${assignment.skill}\n`) || !/^description: .+/m.test(text)) throw new Error(`Invalid skill frontmatter: ${file}`);
    }
    const expected = index ? [stages[index - 1]] : [];
    if (JSON.stringify(stage.requires) !== JSON.stringify(expected)) throw new Error(`Missing upstream acceptance dependency: ${stage.id}`);
    if (stage.terminal_access !== (stage.id === "terminal-evaluation")) throw new Error(`Invalid terminal evaluation access: ${stage.id}`);
  }
  if (registry.stages.find(s => s.id === "terminal-evaluation").condition !== "reviewed STOP and locked pipeline") throw new Error("Terminal evaluation must follow reviewed STOP and model lock");
  return registry;
}
function dispatch(stageId, review = false, harnessRoot = path.resolve(__dirname, ".."), availableRoles = null) {
  const registry = validate(harnessRoot);
  const stage = registry.stages.find(s => s.id === stageId);
  if (!stage) throw new Error(`Unknown research stage: ${stageId}`);
  const assigned = review ? stage.reviewer : stage.producer;
  const skillsRoot = path.dirname(harnessRoot);
  const nativeType = assigned.native_agent_type || assigned.agent_type;
  const actualType = availableRoles?.includes(nativeType) ? nativeType : assigned.agent_type;
  if (availableRoles && !availableRoles.includes(actualType)) throw new Error(`No supported role for ${stageId}`);
  return {
    stage: stageId, operation: review ? "review" : "produce", stage_mode: stageId, agent_type: actualType, preferred_agent_type: nativeType,
    role_availability: availableRoles ? (actualType === nativeType ? "native_available" : "explicit_generic_fallback") : "catalog_check_required",
    fork_turns: "none",
    required_reads: [...new Set([registry.shared_protocol, registry.artifact_contracts, `${assigned.skill}/SKILL.md`])].map(p => path.resolve(skillsRoot, p)),
    requires: stage.requires, separate_from_producer: review,
    run_context_required: ["objective", "contract_version", "iteration", "input_paths_and_hashes", "owned_output_paths", "accepted_upstream_receipts", "acceptance_criteria", "stop_condition", ...(review ? ["producer_execution_id", "reviewer_execution_id", "review_output_ownership"] : [])],
    note: "Dispatch template only. The coordinator must supply real run evidence and invoke the native role; this does not launch or approve a stage."
  };
}
if (require.main === module) {
  try {
    const args = process.argv.slice(2);
    if (args.length === 0) { validate(); console.log("QSAR skill assignments and references OK (not scientific validation)."); }
    else if (args.length === 2 && ["--dispatch", "--review"].includes(args[0])) console.log(JSON.stringify(dispatch(args[1], args[0] === "--review"), null, 2));
    else throw new Error("Usage: node validate_skill_map.js [--dispatch STAGE | --review STAGE]");
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
module.exports = { validate, dispatch };
