export type ProviderId = "gpt" | "gemini" | "claude";

export interface CompletionInput {
  system?: string;
  user: string;
}

export interface CompletionResult {
  text: string;
  latencyMs: number;
}

export interface Provider {
  id: ProviderId;
  label: string;
  available: boolean;
  /** 키/모델 미설정 등으로 사용 불가할 때의 사유. available=false일 때만 채워짐. */
  unavailableReason?: string;
  complete(input: CompletionInput): Promise<CompletionResult>;
}

export interface ModerationResult {
  flagged: boolean;
  categories: string[];
  /** flagged=true인 카테고리들의 확률 점수(0~1). 카테고리명 → 점수. */
  categoryScores: Record<string, number>;
  latencyMs: number;
}

export interface ModerationClient {
  available: boolean;
  unavailableReason?: string;
  moderate(text: string): Promise<ModerationResult>;
}
