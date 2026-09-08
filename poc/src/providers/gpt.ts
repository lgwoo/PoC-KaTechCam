import { buildOpenAiCompatibleProvider } from "./openaiCompatible.js";
import type { Provider } from "./types.js";

export function buildGptProvider(): Provider {
  const apiKey = process.env.OPENAI_API_KEY;
  const model = process.env.OPENAI_MODEL;
  // 공식 OpenAI가 아닌 제3자 브릿지(예: Timely GPT SDK)를 거치는 옵션.
  // 설정 시 latency 결과에 반드시 "브릿지 경유"로 표시해야 한다 — 직접 API 기준(PERF-01~03)과
  // 비교 불가능한 수치가 되기 때문. 실제 서비스/실제 아동 데이터에는 사용 금지, PoC 테스트 전용.
  const baseURL = process.env.OPENAI_BASE_URL;

  if (!apiKey) {
    return unavailable("OPENAI_API_KEY 미설정");
  }
  if (!model) {
    return unavailable(
      "OPENAI_MODEL 미설정 — .env.example 주석 참고, 현재 사용 가능한 모델 ID를 직접 확인해 채울 것",
    );
  }

  const label = baseURL ? `GPT (${model}, 브릿지 경유: ${baseURL})` : `GPT (${model})`;
  return buildOpenAiCompatibleProvider({ id: "gpt", label, apiKey, model, baseURL });
}

function unavailable(reason: string): Provider {
  return {
    id: "gpt",
    label: "GPT",
    available: false,
    unavailableReason: reason,
    async complete() {
      throw new Error(`GPT provider unavailable: ${reason}`);
    },
  };
}
