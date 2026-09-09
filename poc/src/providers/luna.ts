import { buildOpenAiCompatibleProvider } from "./openaiCompatible.js";
import type { Provider } from "./types.js";

// Elice MLAPI(OpenAI 호환 서버리스 엔드포인트)에서 서빙하는 모델(예: gpt-5.6-luna) 비교용.
// GPT(Timely 브릿지)와 별개 슬롯으로 둬서 두 provider를 동시에 놓고 비교할 수 있게 한다.
// ⚠️ 이것도 제3자 서버리스 호스팅 — PoC 테스트 전용, 실제 서비스/아동 데이터에 쓰지 말 것.
export function buildLunaProvider(): Provider {
  const apiKey = process.env.LUNA_API_KEY;
  const baseURL = process.env.LUNA_BASE_URL;
  const model = process.env.LUNA_MODEL || "gpt-5.6-luna";

  if (!apiKey) {
    return unavailable("LUNA_API_KEY 미설정");
  }
  if (!baseURL) {
    return unavailable("LUNA_BASE_URL 미설정 — Elice MLAPI 엔드포인트(예: https://.../v1)를 넣을 것");
  }

  return buildOpenAiCompatibleProvider({ id: "luna", label: `Luna (${model})`, apiKey, model, baseURL });
}

function unavailable(reason: string): Provider {
  return {
    id: "luna",
    label: "Luna",
    available: false,
    unavailableReason: reason,
    async complete() {
      throw new Error(`Luna provider unavailable: ${reason}`);
    },
  };
}
