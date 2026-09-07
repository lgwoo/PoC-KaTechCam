import path from "node:path";
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";
import { buildRegistry } from "./providers/registry.js";
import { writeReport } from "./report.js";
import * as test01 from "./tests/01-safety.js";
import * as test02 from "./tests/02-latency.js";
import * as test03 from "./tests/03-mascot.js";
import * as test04 from "./tests/04-microgoal.js";
import * as test05 from "./tests/05-recommend-report.js";
import * as test06 from "./tests/06-privacy.js";
import * as test07 from "./tests/07-persona.js";
import type { TestResult } from "./tests/types.js";

dotenv.config();

const ALL_TESTS: { id: string; run: (registry: ReturnType<typeof buildRegistry>) => Promise<TestResult> }[] = [
  { id: "01", run: test01.run },
  { id: "02", run: test02.run },
  { id: "03", run: test03.run },
  { id: "04", run: test04.run },
  { id: "05", run: test05.run },
  { id: "06", run: test06.run },
  { id: "07", run: test07.run },
];

function parseOnlyArg(): string[] | null {
  const arg = process.argv.find((a) => a.startsWith("--only="));
  if (!arg) return null;
  return arg
    .replace("--only=", "")
    .split(",")
    .map((s) => s.trim());
}

async function main() {
  const registry = buildRegistry();

  console.log("=== Provider 상태 ===");
  for (const provider of registry.providers) {
    console.log(
      provider.available
        ? `[OK] ${provider.label}`
        : `[NO_API_KEY] ${provider.id} — ${provider.unavailableReason}`,
    );
  }
  console.log(
    registry.moderation.available
      ? "[OK] OpenAI Moderation"
      : `[NO_API_KEY] OpenAI Moderation — ${registry.moderation.unavailableReason}`,
  );
  console.log("");

  const only = parseOnlyArg();
  const testsToRun = only ? ALL_TESTS.filter((t) => only.includes(t.id)) : ALL_TESTS;

  const results: TestResult[] = [];
  for (const test of testsToRun) {
    console.log(`--- running ${test.id} ---`);
    const result = await test.run(registry);
    results.push(result);
    console.log(`--- done ${test.id}: ${result.title} (${result.rows.length} rows) ---\n`);
  }

  const here = path.dirname(fileURLToPath(import.meta.url));
  const outDir = path.join(here, "..", "results");
  const { jsonPath, markdownPath } = writeReport(results, outDir);

  console.log(`결과 저장: ${jsonPath}`);
  console.log(`요약 저장: ${markdownPath}`);
}

main().catch((error) => {
  console.error("PoC 실행 실패:", error);
  process.exitCode = 1;
});
