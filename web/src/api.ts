import type {
  ModelInfo,
  ScenarioOption,
  SessionListRow,
  SessionView,
  StoredSessionView,
  TurnRecord,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${body}`);
  }
  return response.json() as Promise<T>;
}

export function listScenarios() {
  return request<{ models: ModelInfo; scenarios: ScenarioOption[] }>(
    "/api/scenarios",
  );
}

export function generateScenario(theme: string, level: string) {
  return request<ScenarioOption>("/api/scenarios/generate", {
    method: "POST",
    body: JSON.stringify({ theme, level }),
  });
}

export function deleteScenario(key: string) {
  return request<{ deleted: string; sessions: number; turns: number }>(
    `/api/scenarios/${encodeURIComponent(key)}`,
    { method: "DELETE" },
  );
}

export function createSession(scenario: string) {
  return request<SessionView>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ scenario }),
  });
}

export function postTurn(sessionId: string, utterance: string) {
  return request<{ turn: TurnRecord; session: SessionView }>(
    `/api/sessions/${sessionId}/turns`,
    { method: "POST", body: JSON.stringify({ utterance }) },
  );
}

export function listSessions() {
  return request<{ sessions: SessionListRow[] }>("/api/sessions");
}

// 진행 중이 아니어도 읽힌다 — 서버가 재시작되면 live=false 로 DB 에서 되살아온다.
export function getSession(sessionId: string) {
  return request<StoredSessionView>(`/api/sessions/${sessionId}`);
}
