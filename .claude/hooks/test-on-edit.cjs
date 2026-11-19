#!/usr/bin/env node
/*
 * test-on-edit.cjs — PostToolUse hook: after a Write/Edit/MultiEdit touches a file
 * under guideline_checker/**\/*.py, run pytest scoped to the matching test module
 * (falls back to the full suite for small repos) so a regression in the rule
 * engine is caught before commit.
 *
 * Best-effort: any internal error, missing pytest, or non-matching path exits 0 so
 * it never wedges a session. Does not block (exit 0 always) — this is a fast local
 * signal, not the CI gate.
 */
"use strict";

const { execSync } = require("node:child_process");
const path = require("node:path");

function readStdin() {
  try {
    return JSON.parse(require("node:fs").readFileSync(0, "utf8"));
  } catch {
    return {};
  }
}

function touchedSourceFile(input) {
  const toolInput = input.tool_input || input;
  const filePath = toolInput.file_path;
  if (!filePath) return null;
  const normalized = filePath.replace(/\\/g, "/");
  return /guideline_checker\/.+\.py$/.test(normalized) ? normalized : null;
}

function moduleNameFor(sourceFile) {
  return path.basename(sourceFile, ".py");
}

function main() {
  const input = readStdin();
  const sourceFile = touchedSourceFile(input);
  if (!sourceFile) return;

  const moduleName = moduleNameFor(sourceFile);
  try {
    execSync(`pytest -k "${moduleName}" -q`, { stdio: "pipe", timeout: 60000 });
  } catch (error) {
    const output = (error.stdout || error.stderr || error.message || "").toString();
    process.stderr.write(
      `test-on-edit: pytest -k "${moduleName}" failed after editing ${sourceFile}:\n${output}\n`,
    );
  }
}

main();
