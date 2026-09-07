export type TestRow = Record<string, string | number | boolean>;

export interface TestResult {
  id: string;
  title: string;
  /** 확정 스펙이 아니라 우리가 임의로 가정하고 테스트한 부분이 있으면 명시 */
  assumption?: string;
  rows: TestRow[];
  notes: string[];
}
