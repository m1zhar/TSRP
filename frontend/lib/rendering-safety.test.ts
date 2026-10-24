import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(fileURLToPath(new URL("../", import.meta.url)));
const renderRoots = ["app", "components"].map((path) => join(frontendRoot, path));
const forbiddenSinks = [
  /\bdangerouslySetInnerHTML\s*[:=]/u,
  /\binnerHTML\s*(?:[+*/-]?=)/u,
  /\binsertAdjacentHTML\s*\(/u,
  /\bdocument\.write\s*\(/u,
  /\beval\s*\(/u,
  /\bnew\s+Function\s*\(/u,
];

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(?:ts|tsx)$/u.test(entry.name) ? [path] : [];
  });
}

test("first-party UI rendering avoids direct HTML-writing and code-evaluation sinks", () => {
  const files = renderRoots.flatMap(sourceFiles);
  assert.ok(files.length > 0, "Expected to inspect the app and component source trees.");

  for (const path of files) {
    const source = readFileSync(path, "utf8");
    for (const sink of forbiddenSinks) {
      assert.doesNotMatch(source, sink, `${relative(frontendRoot, path)} must not use ${sink.source}`);
    }
  }
});

test("rendering sink guard recognizes representative unsafe patterns", () => {
  const unsafeExamples = [
    "<div dangerouslySetInnerHTML={{ __html: value }} />",
    "node.innerHTML = value",
    "node.insertAdjacentHTML('beforeend', value)",
    "document.write(value)",
    "eval(value)",
    "new Function(value)",
  ];
  for (const example of unsafeExamples) {
    assert.ok(forbiddenSinks.some((sink) => sink.test(example)), `Expected sink guard to catch: ${example}`);
  }
});
