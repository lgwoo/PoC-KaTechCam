// 콘솔과 「지난 역할극」 화면이 같이 쓰는 한글 라벨.
//
// 두 화면이 각자 베껴 두면 서버 enum 이 바뀔 때 한쪽만 고쳐지고, 같은 값이 화면마다
// 다른 말로 보인다. snippets.ts 의 주석이 지적한 것과 같은 문제라서 여기로 모았다.

export const MASCOT_MODE_LABEL: Record<string, string> = {
  HALT: "중단 — 보호 절차",
  CORRECT: "제지·환기",
  SHIELD: "캐릭터 대신 받아줌",
  SCAFFOLD: "목표 유도",
  NONE: "개입 없음",
};

export const GOAL_STATUS_LABEL: Record<string, string> = {
  NOT_OBSERVED: "미관찰",
  PARTIAL: "부분",
  ACHIEVED: "달성",
  ABANDONED: "유도 소진",
};

export const INTAKE_CATEGORY_LABEL: Record<string, string> = {
  NORMAL: "일반",
  PROFANITY: "욕설·비난",
  PII: "개인정보",
  RISK: "위험 신호",
  OFF_TOPIC: "주제 이탈",
  HIGH_CONCERN: "심각 우려",
};

export const SUPPORT_LEVEL_LABEL: Record<string, string> = {
  S0: "S0 개방형 질문",
  S1: "S1 단서 제공",
  S2: "S2 선택지 제공",
  S3: "S3 정답 제공",
};

export const END_REASON_LABEL: Record<string, string> = {
  ALL_GOALS: "목표 전부 달성",
  TURN_CAP: "턴 한도 도달",
  LADDER_EXHAUSTED: "끝까지 도와줬는데 못 도달",
  SAFETY_HALT: "안전 중단",
};
