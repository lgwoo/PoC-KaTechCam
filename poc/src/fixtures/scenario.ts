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
  openingPrompts: {
    S0: "친구는 어떤 기분일까?",
    S1: "친구가 넘어져서 울고 있어. 어떤 기분일까?",
    S2: "친구는 기쁠까, 아프고 속상할까?",
    S3: "친구는 넘어져서 아프고 속상한 기분이야.",
  },
  maximumSupportTurnsPerMicroGoal: 4,
};

export type Scenario = typeof scenario;
