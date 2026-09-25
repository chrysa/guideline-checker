#!/usr/bin/env node
/*
 * guideline-yaml-check.cjs — fast-feedback parity with .pre-commit-hooks.yaml.
 *
 * PostToolUse hook: after a Write/Edit/MultiEdit touches a file under
 * guidelines/**\/*.yml, run `guideline-checker check --root . --fail-on error`
 * (the same invocation the pre-commit hook uses) so a broken rule referential is
 * caught inside the session, not at commit time.
 *
 * Best-effort: any internal error, missing CLI, or non-matching path exits 0 so it
 * never wedges a session. Reports failures via stderr text back to the transcript,
 * does not block (exit 0 always) — enforcement of record stays the pre-commit hook.
 */
"use strict";

const { execSync } = require("node:child_process");

function readStdin() {
  try {
    return JSON.parse(require("node:fs").readFileSync(0, "utf8"));
  } catch {
    return {};
  }
}

function touchedGuidelinesYaml(input) {
  const toolInput = input.tool_input || input;
  const candidates = [
    toolInput.file_path,
    ...(Array.isArray(toolInput.edits) ? [] : []),
  ].filter(Boolean);
  return candidates.some(
    (filePath) => /guidelines\/.+\.ya?ml$/.test(filePath.replace(/\\/g, "/")),
  );
}

function main() {
  const input = readStdin();
  if (!touchedGuidelinesYaml(input)) return;

  try {
    execSync("guideline-checker check --root . --fail-on error", {
      stdio: "pipe",
      timeout: 20000,
    });
  } catch (error) {
    const output = (error.stdout || error.stderr || error.message || "").toString();
    process.stderr.write(
      `guideline-yaml-check: guideline-checker reported violations after this edit:\n${output}\n`,
    );
  }
}

main();
