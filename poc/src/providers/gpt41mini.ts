import { buildOpenAiCompatibleProvider } from "./openaiCompatible.js";
import type { Provider } from "./types.js";

// Elice MLAPI(OpenAI 호환 서버리스 엔드포인트)에서 서빙하는 gpt-4.1-mini 비교용.
// Luna/Nano/Mini와 별개 슬롯 — --provider=gpt41mini로 따로 골라서 비교할 수 있음.
// ⚠️ 이것도 제3자 서버리스 호스팅 — PoC 테스트 전용, 실제 서비스/아동 데이터에 쓰지 말 것.
export function buildGpt41MiniProvider(): Provider {
  const apiKey = process.env.GPT41MINI_API_KEY;
  const baseURL = process.env.GPT41MINI_BASE_URL;
  const model = process.env.GPT41MINI_MODEL || "gpt-4.1-mini";

  if (!apiKey) {
    return unavailable("GPT41MINI_API_KEY 미설정");
  }
  if (!baseURL) {
    return unavailable("GPT41MINI_BASE_URL 미설정 — Elice MLAPI 엔드포인트(예: https://.../v1)를 넣을 것");
  }

  // gpt-4.1-mini는 Luna/Nano/Mini(gpt-5.x 계열)와 달리 reasoning-tier 모델이 아니라
  // max_tokens를 그대로 받아들일 가능성이 높음. 400 나오면 max_completion_tokens로 교체.
  return buildOpenAiCompatibleProvider({
    id: "gpt41mini",
    label: `GPT-4.1 Mini (${model})`,
    apiKey,
    model,
    baseURL,
  });
}

function unavailable(reason: string): Provider {
  return {
    id: "gpt41mini",
    label: "GPT-4.1 Mini",
    available: false,
    unavailableReason: reason,
    async complete() {
      throw new Error(`GPT-4.1 Mini provider unavailable: ${reason}`);
    },
  };
}
