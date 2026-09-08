import fs from "node:fs";
import path from "node:path";
import type { TestResult } from "./tests/types.js";

const MAX_CELL_LENGTH = 300;

function sanitizeCell(value: unknown): string {
  const text = String(value ?? "");
  const collapsed = text.replace(/\r?\n/g, " ¶ ").replace(/\|/g, "\\|");
  return collapsed.length > MAX_CELL_LENGTH ? `${collapsed.slice(0, MAX_CELL_LENGTH)}…` : collapsed;
}

function toMarkdownTable(rows: TestResult["rows"]): string {
  if (rows.length === 0) return "_결과 없음_";
  // rows마다 필드 구성이 다를 수 있으므로(예: SKIPPED 행 vs 정상 결과 행) 전체 행의 키 합집합을 컬럼으로 쓴다.
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  const header = `| ${columns.join(" | ")} |`;
  const divider = `| ${columns.map(() => "---").join(" | ")} |`;
  const body = rows
    .map((row) => `| ${columns.map((col) => sanitizeCell(row[col])).join(" | ")} |`)
    .join("\n");
  return [header, divider, body].join("\n");
}

export function writeReport(results: TestResult[], outDir: string): { jsonPath: string; markdownPath: string } {
  fs.mkdirSync(outDir, { recursive: true });
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");

  const jsonPath = path.join(outDir, `run-${timestamp}.json`);
  fs.writeFileSync(jsonPath, JSON.stringify(results, null, 2), "utf-8");

  const sections = results.map((result) => {
    const lines = [`## ${result.id} — ${result.title}`];
    if (result.assumption) {
      lines.push(`> ⚠️ 가정(정식 스펙 아님): ${result.assumption}`);
    }
    for (const note of result.notes) {
      lines.push(`- ${note}`);
    }
    lines.push("", toMarkdownTable(result.rows), "");
    return lines.join("\n");
  });

  const markdown = [
    "# 느링고 PoC 결과 요약",
    "",
    `실행 시각: ${new Date().toISOString()}`,
    "",
    ...sections,
  ].join("\n");

  const markdownPath = path.join(outDir, "summary.md");
  fs.writeFileSync(markdownPath, markdown, "utf-8");

  return { jsonPath, markdownPath };
}
