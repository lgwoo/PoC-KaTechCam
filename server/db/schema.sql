-- 느링고 역할극 PoC 스키마 (SQLite)
--
-- 테이블·컬럼·Enum 이름은 팀 ERD(「API 명세서 초안_v3」)를 그대로 따른다.
-- 나중에 팀 스키마로 합칠 때 이름이 갈리면 마이그레이션이 아니라 번역이 되어 버린다.
--
-- 팀 ERD 에 **없는** 것 세 개를 PoC 가 채운다. 명세서 §11.2 가 스스로 인정한 공백이다:
--   "현재 ERD는 검증을 통과해 실제 전달된 최종 응답만 보존하는 축약 모델이다.
--    분석 결과, 여러 후보와 평가 이력은 저장하지 못한다."
--   -> TURN_CANDIDATE / STAGE_TIMING / INTAKE_RESULT
--
-- 이 파일이 만드는 DB 에는 아동 발화 원문이 쌓인다. .gitignore 에 *.db 가 있다.

PRAGMA foreign_keys = ON;

-- 시나리오 자산 -----------------------------------------------------------
-- PoC 에서는 픽스처를 그대로 적재한다(강사 승인 흐름은 범위 밖).

CREATE TABLE IF NOT EXISTS SCENARIO (
    scenario_id            TEXT PRIMARY KEY,
    scenario_version       INTEGER NOT NULL,
    title                  TEXT NOT NULL,
    scenario_level         TEXT NOT NULL,              -- L1 | L2 | L3
    scenario_facts         TEXT NOT NULL,              -- JSON array
    prohibited_inferences  TEXT NOT NULL,              -- JSON array
    persona                TEXT NOT NULL,              -- JSON object
    mascot_intro           TEXT NOT NULL,
    character_opening_line TEXT NOT NULL,
    maximum_turn_count     INTEGER NOT NULL,
    max_support_turns      INTEGER NOT NULL,
    -- AI 가 지은 시나리오만 채운다. 픽스처는 NULL.
    generation_total_ms    INTEGER,
    generation_timings     TEXT                        -- JSON array, StageTiming
);

CREATE TABLE IF NOT EXISTS SUB_GOAL (
    subgoal_id        TEXT PRIMARY KEY,
    scenario_id       TEXT NOT NULL REFERENCES SCENARIO(scenario_id),
    order_no          INTEGER NOT NULL,
    description       TEXT NOT NULL,
    required_evidence TEXT NOT NULL,                   -- JSON array
    opening_prompts   TEXT NOT NULL,                   -- JSON object, S0~S3
    UNIQUE (scenario_id, order_no)                     -- DUPLICATE_SUB_GOAL_ORDER
);

-- 세션과 턴 ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ROLEPLAY_SESSION (
    session_id     TEXT PRIMARY KEY,
    scenario_id    TEXT NOT NULL REFERENCES SCENARIO(scenario_id),
    status         TEXT NOT NULL,                      -- SessionStatus
    phase          TEXT NOT NULL,                      -- ACTIVE | CLOSING | ENDED
    turn_index     INTEGER NOT NULL DEFAULT 0,
    end_reason     TEXT,                               -- ALL_GOALS | TURN_CAP | ...
    -- 팀 ERD 는 "시작·종료 시각 없음"이 약점으로 적혀 있다. PoC 에서는 넣는다.
    started_at     TEXT NOT NULL,
    ended_at       TEXT
);

CREATE TABLE IF NOT EXISTS CONVERSATION_TURN (
    turn_id               TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL REFERENCES ROLEPLAY_SESSION(session_id),
    turn_number           INTEGER NOT NULL,
    input_type            TEXT NOT NULL,               -- InputType
    raw_utterance         TEXT NOT NULL,               -- 아동 원문 (개발 버전 한정 보관)
    standardized_utterance TEXT NOT NULL,              -- 마스킹 적용본
    safety_status         TEXT NOT NULL,               -- SafetyStatus
    focus_subgoal_id      TEXT,
    support_level         TEXT NOT NULL,               -- S0~S3
    hint_source           TEXT NOT NULL DEFAULT 'NONE', -- MASCOT|CHARACTER|NONE
    mascot_mode           TEXT NOT NULL,               -- HALT|CORRECT|SHIELD|SCAFFOLD|NONE
    mascot_line           TEXT,
    closing_line          TEXT,
    agent_response_text   TEXT,
    fallback_used         INTEGER NOT NULL DEFAULT 0,
    retry_count           INTEGER NOT NULL DEFAULT 0,
    degraded_stages       TEXT NOT NULL DEFAULT '[]',  -- JSON array
    total_ms              INTEGER NOT NULL,
    end_reason            TEXT,
    delivered_at          TEXT NOT NULL,
    UNIQUE (session_id, turn_number)                   -- DUPLICATE_TURN_NUMBER
);

-- 목표 상태 ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS SESSION_GOAL_STATUS (
    status_id             TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL REFERENCES ROLEPLAY_SESSION(session_id),
    subgoal_id            TEXT NOT NULL,
    status                TEXT NOT NULL,               -- NOT_OBSERVED|PARTIAL|ACHIEVED|ABANDONED
    reached_level         TEXT NOT NULL,               -- S0~S3
    turns_to_reach        INTEGER,
    guidance_count        INTEGER NOT NULL DEFAULT 0,
    evidence_text         TEXT,
    why_this_satisfies    TEXT,
    -- 문서 §14 의 achieved_without_answer_reveal 의 반대값.
    -- S3 정답 공개 뒤 달성된 것을 스스로 도달한 것과 같이 세면 성공률이 부풀려진다.
    achieved_with_support INTEGER NOT NULL DEFAULT 0,
    updated_turn_id       TEXT REFERENCES CONVERSATION_TURN(turn_id),
    -- 팀 ERD 가 "세션+목표 UNIQUE 없음"을 스스로 약점으로 적어 뒀다. 여기서는 건다.
    UNIQUE (session_id, subgoal_id)
);

CREATE TABLE IF NOT EXISTS SUPPORT_LEVEL_CHANGE (
    change_id   TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES ROLEPLAY_SESSION(session_id),
    turn_id     TEXT NOT NULL REFERENCES CONVERSATION_TURN(turn_id),
    subgoal_id  TEXT NOT NULL,
    from_level  TEXT NOT NULL,
    to_level    TEXT NOT NULL,
    reason_code TEXT NOT NULL,                         -- SupportChangeReason
    changed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS SAFETY_INCIDENT (
    incident_id   TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES ROLEPLAY_SESSION(session_id),
    turn_id       TEXT REFERENCES CONVERSATION_TURN(turn_id),
    source        TEXT NOT NULL,                       -- LEARNER_INPUT | AI_OUTPUT
    incident_type TEXT NOT NULL,                       -- IncidentType
    action_taken  TEXT NOT NULL,                       -- IncidentAction
    detail        TEXT,
    occurred_at   TEXT NOT NULL
);

-- 팀 ERD 에 없는 세 테이블 -------------------------------------------------

-- §16 "후보 응답·평가·단계별 재시도 없음 / 실패 코드 분석, 동일 후보 재전송 증명,
-- 모델 품질 추적 불가" — 그 공백을 메운다. retry_count 합계만으로는 아무것도 못 본다.
CREATE TABLE IF NOT EXISTS TURN_CANDIDATE (
    candidate_id         TEXT PRIMARY KEY,
    turn_id              TEXT NOT NULL REFERENCES CONVERSATION_TURN(turn_id),
    attempt_no           INTEGER NOT NULL,
    text                 TEXT NOT NULL,
    outcome              TEXT NOT NULL,                -- PASS|REGENERATE|SAFETY_REGENERATE|
                                                       -- REFEREE_BLOCKED|JUDGE_ERROR|EMPTY|ERROR|
                                                       -- MASCOT_MISSING|MASCOT_ANSWER_LEAK
    delivered            INTEGER NOT NULL DEFAULT 0,
    judge_safe_to_send   INTEGER,
    judge_decision       TEXT,
    judge_failure_codes  TEXT,                         -- JSON array
    judge_reason         TEXT,
    judge_revision       TEXT,                         -- JSON object {change, avoid}
    referee_violations   TEXT NOT NULL DEFAULT '[]',   -- JSON array
    moderation_flagged   INTEGER,
    moderation_categories TEXT,                        -- JSON array
    UNIQUE (turn_id, attempt_no)
);

-- duration 만으로는 부족하다 — 목표 스캔과 생성 루프가 병렬이라 합계가 체감 시간과
-- 다르다. 턴 시작 기준 오프셋이 있어야 콘솔에서 간트로 그리고 무엇이 실제로
-- 병렬이었는지 볼 수 있다.
CREATE TABLE IF NOT EXISTS STAGE_TIMING (
    timing_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id         TEXT NOT NULL REFERENCES CONVERSATION_TURN(turn_id),
    stage           TEXT NOT NULL,
    attempt_no      INTEGER,
    start_offset_ms INTEGER NOT NULL,
    duration_ms     INTEGER NOT NULL,
    ok              INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS INTAKE_RESULT (
    turn_id             TEXT PRIMARY KEY REFERENCES CONVERSATION_TURN(turn_id),
    category            TEXT NOT NULL,
    reason              TEXT NOT NULL,
    misunderstanding    INTEGER NOT NULL DEFAULT 0,
    character_bind      INTEGER NOT NULL DEFAULT 0,
    goal_progress       TEXT NOT NULL,
    masked              INTEGER NOT NULL DEFAULT 0,
    override_source     TEXT,                          -- Moderation 이 분류를 덮었으면 그 사유
    degraded            INTEGER NOT NULL DEFAULT 0,
    moderation_available INTEGER NOT NULL DEFAULT 0,
    moderation_flagged  INTEGER NOT NULL DEFAULT 0,
    moderation_severity TEXT NOT NULL,
    moderation_categories TEXT NOT NULL DEFAULT '[]',  -- JSON array
    moderation_scores   TEXT NOT NULL DEFAULT '{}'     -- JSON object
);

-- 스캐너가 기각한 근거도 남긴다. 과소 인정을 디버깅하려면 무엇이 왜 떨어졌는지 봐야 한다.
CREATE TABLE IF NOT EXISTS GOAL_SCAN_REJECTION (
    rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id      TEXT NOT NULL REFERENCES CONVERSATION_TURN(turn_id),
    subgoal_id   TEXT NOT NULL,
    evidence     TEXT NOT NULL,
    reason       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turn_session ON CONVERSATION_TURN(session_id, turn_number);
CREATE INDEX IF NOT EXISTS idx_candidate_turn ON TURN_CANDIDATE(turn_id);
CREATE INDEX IF NOT EXISTS idx_timing_turn ON STAGE_TIMING(turn_id);
CREATE INDEX IF NOT EXISTS idx_goal_status_session ON SESSION_GOAL_STATUS(session_id);

-- 시뮬레이션 실행 기록 ----------------------------------------------------
-- 파이프라인 기능을 전수로 훑는 스윕(scripts/simulate.py)이 남기는 두 테이블.
--
-- 소요 시간은 여기 복사하지 않는다 — STAGE_TIMING 이 이미 원본이다. 여기 있는 건
-- "어느 회차의 어느 스위트가 그 턴을 만들었나"라는 꼬리표뿐이다. 꼬리표가 없으면
-- 1차 실행과 2차 실행의 지연을 나눠 볼 방법이 없다(그게 지금 상태다).

CREATE TABLE IF NOT EXISTS TEST_RUN (
    run_id           TEXT PRIMARY KEY,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    base_url         TEXT NOT NULL,
    agent_model      TEXT NOT NULL,
    moderation_model TEXT,                          -- 미설정이면 NULL — 방어망 한 겹이 빠진 실행이다
    suites           TEXT NOT NULL,                 -- JSON array
    scenario_count   INTEGER NOT NULL DEFAULT 0,
    session_count    INTEGER NOT NULL DEFAULT 0,
    turn_count       INTEGER NOT NULL DEFAULT 0,
    note             TEXT
);

-- client_ms 는 서버가 재는 total_ms 와 다르다 — HTTP 왕복과 save_turn 의 DB 쓰기가
-- 들어 있다. 둘의 차이가 파이프라인 밖 오버헤드다. 서버는 이 값을 잴 수 없다.
CREATE TABLE IF NOT EXISTS TEST_TURN (
    run_id      TEXT NOT NULL REFERENCES TEST_RUN(run_id),
    turn_id     TEXT NOT NULL REFERENCES CONVERSATION_TURN(turn_id),
    suite       TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    probe_id    TEXT,                               -- 발화 스크립트상의 이름
    expectation TEXT NOT NULL,                      -- JSON object — 이 턴에 기대한 것
    verdict     TEXT NOT NULL,                      -- OK / MISMATCH / FAIL
    findings    TEXT NOT NULL DEFAULT '[]',         -- JSON array — 어긋난 항목
    client_ms   INTEGER NOT NULL,
    PRIMARY KEY (run_id, turn_id)
);

CREATE INDEX IF NOT EXISTS idx_test_turn_run ON TEST_TURN(run_id);
