import { buildOpenAiCompatibleProvider } from "./openaiCompatible.js";
import type { Provider } from "./types.js";

// Elice MLAPI(OpenAI 호환 서버리스 엔드포인트)에서 서빙하는 gpt-5.4-mini 비교용.
// GPT/Luna/Nano와 별개 슬롯 — 여러 provider를 동시에 놓고 비교할 수 있게 한다.
// ⚠️ 이것도 제3자 서버리스 호스팅 — PoC 테스트 전용, 실제 서비스/아동 데이터에 쓰지 말 것.
export function buildMiniProvider(): Provider {
  const apiKey = process.env.MINI_API_KEY;
  const baseURL = process.env.MINI_BASE_URL;
  const model = process.env.MINI_MODEL || "gpt-5.4-mini";

  if (!apiKey) {
    return unavailable("MINI_API_KEY 미설정");
  }
  if (!baseURL) {
    return unavailable("MINI_BASE_URL 미설정 — Elice MLAPI 엔드포인트(예: https://.../v1)를 넣을 것");
  }

  // Luna/Nano와 같은 계열로 추정 — max_tokens 대신 max_completion_tokens 요구할 가능성이 높음.
  return buildOpenAiCompatibleProvider({
    id: "mini",
    label: `Mini (${model})`,
    apiKey,
    model,
    baseURL,
    maxTokensParam: "max_completion_tokens",
  });
}

function unavailable(reason: string): Provider {
  return {
    id: "mini",
    label: "Mini",
    available: false,
    unavailableReason: reason,
    async complete() {
      throw new Error(`Mini provider unavailable: ${reason}`);
    },
  };
}
