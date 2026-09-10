// 파이프라인 페이지에 싣는 코드 조각. 손으로 베끼지 않고 실제 소스에서 잘라온다 —
// 베껴 두면 서버 코드를 고쳤을 때 페이지만 옛 내용으로 남는다(실제로 그랬다).
//
// 소스 쪽에 `# [snippet:이름]` ~ `# [/snippet:이름]` 표시를 넣어 두면 그 사이를 가져온다.
// 표시가 있다는 건 "이 구간은 콘솔에 노출된다"는 뜻이기도 하다.
import generatePy from "../../server/pipeline/generate.py?raw";
import mascotPy from "../../server/pipeline/mascot.py?raw";
import moderationPy from "../../server/safety/moderation.py?raw";
import refereePy from "../../server/pipeline/referee.py?raw";
import turnPy from "../../server/pipeline/turn.py?raw";

function extract(source: string, name: string): string {
  const start = source.indexOf(`# [snippet:${name}]`);
  const end = source.indexOf(`# [/snippet:${name}]`);
  if (start === -1 || end === -1 || end < start) {
    return `(소스에서 '${name}' 구간을 찾지 못했습니다 — 표시가 지워졌는지 확인하세요)`;
  }

  // 서버 소스가 CRLF 로 저장돼 있으면 \r 이 그대로 화면에 남는다.
  const body = source
    .slice(source.indexOf("\n", start) + 1, end)
    .replace(/\r/g, "")
    .replace(/\s+$/, "");

  // 잘라온 구간은 함수 안이라 통째로 들여쓰기돼 있다. 공통 들여쓰기만 걷어낸다.
  const lines = body.split("\n");
  const indents = lines
    .filter((line) => line.trim())
    .map((line) => line.length - line.trimStart().length);
  const shift = indents.length ? Math.min(...indents) : 0;
  return lines.map((line) => line.slice(shift)).join("\n");
}

export type Snippet = { title: string; file: string; note: string; code: string };

export const CODE_SNIPPETS: Snippet[] = [
  {
    title: "마스코트 모드 결정",
    file: "pipeline/mascot.py — select_mode()",
    note: "위에서부터 먼저 걸리는 게 이긴다. 안전이 유도를 항상 이긴다.",
    code: extract(mascotPy, "mascot-mode"),
  },
  {
    title: "마스코트 대사 + 캐릭터 대사 — 한 번의 호출",
    file: "pipeline/generate.py",
    note: "요청도 하나, 응답도 하나. 두 대사가 같은 JSON 안에 담겨 온다.",
    code: extract(generatePy, "one-call"),
  },
  {
    title: "검열도 두 대사를 한 번에",
    file: "pipeline/generate.py + safety/moderation.py",
    note: "Moderation API 는 input 배열을 받고 같은 순서로 결과를 준다. 왕복 한 번이면 된다.",
    code: `${extract(generatePy, "screen-once")}\n\n# safety/moderation.py — 순서가 어긋나면 판정이 뒤바뀐다\n${extract(moderationPy, "screen-guard")}`,
  },
  {
    title: "심판 — 순수 함수",
    file: "pipeline/referee.py — review()",
    note: "통과 → 재생성 강등만 한다. 반대로는 절대 못 올리므로 안전성이 낮아질 수 없다.",
    code: extract(refereePy, "referee"),
  },
  {
    title: "목표 확정 → 도움 세기 조정",
    file: "pipeline/turn.py",
    note: "목표 스캔이 인정한 것만 달성으로 기록한다. 못 했으면 다음 턴에 더 알려준다.",
    code: extract(turnPy, "confirm-goals"),
  },
];
