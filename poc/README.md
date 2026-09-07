# 느링고 PoC 테스트 하네스

기획서(느링고: 감정 인식·공감 역할극 서비스)의 핵심 아키텍처 리스크 7개를
GPT / Gemini / Claude + OpenAI Moderation으로 직접 검증하는 독립 CLI.

계획 근거: `C:\Users\geonu\.claude\plans\poc-fuzzy-ladybug.md`

## 준비

```bash
cd poc
npm install
cp .env.example .env
# .env에 보유한 키만 채운다. GPT/Gemini는 OPENAI_MODEL / GEMINI_MODEL을
# 현재 사용 가능한 모델 ID로 직접 확인 후 채워야 한다 (임의 기본값을 넣지 않음).
```

## 실행

```bash
npm run test:all          # 7개 항목 전체 실행
npx tsx src/runner.ts --only=01,04   # 일부만 실행 (콤마 구분 ID)
```

키가 없는 provider는 `NO_API_KEY`로 표시되고 나머지는 계속 실행된다.

## 결과

`results/summary.md`(사람이 읽는 표), `results/run-<timestamp>.json`(원본 데이터)에 저장된다.

## 테스트 항목과 근거 문서

| ID | 항목 | 근거 |
| - | - | - |
| 01-safety | 유해 단어 대응(화자 무관) | `SAFE-01~07`, `EVAL-15/16` |
| 02-latency | 생성 시간 | `PERF-01~03` |
| 03-mascot | 마스코트(강아지) 개입 | "역할극에 대해" §5 — **정식 스펙 아님, PoC 가정** |
| 04-microgoal | 세부목표 판정 정확도 | `ANA-01~09`, 기능요구사항 §14.4 |
| 05-recommend-report | 추천목표·강사 리포트 생성 | `LVL-07` (MVP 방침과 별개로 에이전트 방식 참고 확인) |
| 06-privacy | 개인정보 노출 | `PRI-04`, `PRIV-01`, `INP-10` |
| 07-persona | 상황·캐릭터에 맞는 대사 | "역할극에 대해" — **정식 스펙 아님, PoC 가정(persona.ts)** |

03, 07은 기획서에 확정되지 않은 부분을 우리가 최소 구현으로 가정해 테스트한 것이다.
결과 리포트의 각 섹션에 "⚠️ 가정" 표시로 남는다.
