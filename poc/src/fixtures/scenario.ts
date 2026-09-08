// 기능요구사항 §7.3 생성 결과 예시를 그대로 재사용 (SCN-001 "넘어져서 우는 친구").
// 출처: 개인 페이지 & 공유된 페이지/테크 스펙 작성 초안/기능 요구사항/느링고 전체 기능 요구사항_v3.md §7.3

export interface MicroGoal {
  id: string;
  description: string;
  requiredEvidence: string[];
}

export const scenario = {
  scenarioId: "SCN-001",
  scenarioVersion: 1,
  title: "넘어져서 우는 친구",
  scenarioLevel: "L1",
  scenarioFacts: [
    "두 친구가 사탕을 가지려고 했다.",
    "친구가 넘어졌다.",
    "친구가 울었다.",
  ],
  prohibitedInferences: [
    "친구가 일부러 넘어졌다고 단정하지 않는다.",
    "친구가 화났다고 정답을 단정하지 않는다.",
  ],
  microGoals: [
    {
      id: "MG-01",
      description: "친구가 넘어졌다는 사건을 파악한다.",
      requiredEvidence: ["넘어짐 또는 다침을 언급한다."],
    },
    {
      id: "MG-02",
      description: "친구가 아프거나 속상할 수 있음을 파악한다.",
      requiredEvidence: ["상황에 맞는 감정을 표현한다."],
    },
    {
      id: "MG-03",
      description: "감정과 넘어진 사건을 연결한다.",
      requiredEvidence: ["넘어졌기 때문에 아프거나 속상하다고 표현한다."],
    },
  ] satisfies MicroGoal[],
  // 기능요구사항 §7.3 원문 그대로 — S0~S3별 "시작 질문"으로 스펙에 정의돼 있으나,
  // "역할극에 대해" 문서(§1)가 지적한 대로 이 문구들은 캐릭터의 1인칭 대사가 아니라
  // 3인칭 설명+질문(교사 톤)이다. 실제 역할극 화면에는 이걸 그대로 캐릭터 대사로 쓰지 않고,
  // 아래 mascotIntro/characterOpeningLine으로 화자를 분리해서 쓴다. 참고용으로 남겨둠.
  openingPrompts: {
    S0: "친구는 어떤 기분일까?",
    S1: "친구가 넘어져서 울고 있어. 어떤 기분일까?",
    S2: "친구는 기쁠까, 아프고 속상할까?",
    S3: "친구는 넘어져서 아프고 속상한 기분이야.",
  },
  // 마스코트(도우미 강아지)가 장면을 소개하는 대사 — 캐릭터가 아니라 마스코트 화자.
  // 매 턴 나오는 게 아니라 세션 시작 시 한 번 나오는 것으로 PoC에서는 단순화했다.
  mascotIntro: "멍멍! 오늘은 친구를 도와주러 왔어. 무슨 일이 있었는지 같이 봐볼까?",
  // 캐릭터(친구)의 진짜 첫 대사 — 1인칭, 상황을 설명하지 않고 그 순간의 반응만 담는다.
  characterOpeningLine: "(무릎을 감싸며 훌쩍인다) ...아야... 흑흑...",
  maximumSupportTurnsPerMicroGoal: 4,
};

export type Scenario = typeof scenario;
