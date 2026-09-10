"""SQLite 영속화.

stdlib sqlite3 만 쓴다 — PoC 에 ORM 을 얹을 이유가 없고, 스키마를 팀 ERD 이름에
정확히 맞춰야 하는데 ORM 이 끼면 그 대응이 오히려 흐려진다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from state.models import (
    EndReason,
    GoalObservationStatus,
    GoalState,
    IncidentAction,
    IncidentSource,
    IncidentType,
    IntakeCategory,
    InputType,
    MascotMode,
    MicroGoal,
    Persona,
    SafetyStatus,
    Scenario,
    SessionPhase,
    SessionState,
    SessionStatus,
    Speaker,
    SupportLevel,
    TurnRecord,
    Utterance,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _add_missing_columns(conn)
    _fix_stale_session_status(conn)
    return conn


# schema.sql 은 CREATE TABLE IF NOT EXISTS 라서 이미 만들어진 DB 에는 새 컬럼이 안 생긴다.
# 컬럼 추가는 여기 적어야 기존 DB 도 따라온다.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("SCENARIO", "generation_total_ms", "INTEGER"),
    ("SCENARIO", "generation_timings", "TEXT"),
    ("CONVERSATION_TURN", "hint_source", "TEXT NOT NULL DEFAULT 'NONE'"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, kind in _ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    conn.commit()


def _fix_stale_session_status(conn: sqlite3.Connection) -> None:
    """끝난 세션의 status 를 바로잡는다.

    status 는 세션 생성 때 IN_PROGRESS 로 한 번 쓰이고 그 뒤 아무도 갱신하지 않았다.
    그래서 phase=ENDED 인 세션까지 전부 "진행 중"으로 남았고, COMPLETED 는 한 건도
    없었다. 이 컬럼을 믿고 "지금 몇 세션이 돌고 있나"를 세면 전부 틀린다.

    이제 turn.py 가 종료 시점에 COMPLETED 를 쓴다. 이미 쌓인 행은 그 코드가 없던
    시절의 것이라 여기서 한 번 맞춘다 — phase 가 이미 답을 들고 있으므로 추측이 아니다.
    같은 조건을 다시 만족하는 행이 없으므로 두 번 돌려도 바뀌는 게 없다.
    """
    conn.execute(
        "UPDATE ROLEPLAY_SESSION SET status=? WHERE phase=? AND status<>?",
        (
            SessionStatus.COMPLETED.value,
            SessionPhase.ENDED.value,
            SessionStatus.COMPLETED.value,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------- 쓰기


GENERATED_PREFIX = "SCN-GEN-"


def load_generated_scenarios(conn: sqlite3.Connection) -> dict[str, Scenario]:
    """AI 가 지은 시나리오를 DB 에서 되살린다.

    픽스처는 코드가 원본이라 읽지 않는다 — 여기서 읽으면 코드와 DB 중 어느 쪽이
    진짜인지 모르게 된다. 되살릴 대상은 프로세스가 죽으면 사라지는 생성분뿐이다.
    """
    rows = conn.execute(
        "SELECT * FROM SCENARIO WHERE scenario_id LIKE ? ORDER BY scenario_id",
        (GENERATED_PREFIX + "%",),
    ).fetchall()

    scenarios: dict[str, Scenario] = {}
    for row in rows:
        scenario = _scenario_from_row(conn, row)
        if scenario is not None:
            scenarios[row["scenario_id"]] = scenario
    return scenarios


def _scenario_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Scenario | None:
    goals = conn.execute(
        "SELECT * FROM SUB_GOAL WHERE scenario_id=? ORDER BY order_no",
        (row["scenario_id"],),
    ).fetchall()
    if not goals:
        return None  # 목표 없는 시나리오는 역할극을 돌릴 수 없다.

    prompts_raw = json.loads(goals[0]["opening_prompts"])
    prefix = f"{row['scenario_id']}:"
    return Scenario(
        scenario_id=row["scenario_id"],
        scenario_version=row["scenario_version"],
        title=row["title"],
        scenario_level=row["scenario_level"],
        scenario_facts=json.loads(row["scenario_facts"]),
        prohibited_inferences=json.loads(row["prohibited_inferences"]),
        micro_goals=[
            MicroGoal(
                id=g["subgoal_id"].removeprefix(prefix),
                description=g["description"],
                required_evidence=json.loads(g["required_evidence"]),
            )
            for g in goals
        ],
        persona=Persona(**json.loads(row["persona"])),
        mascot_intro=row["mascot_intro"],
        character_opening_line=row["character_opening_line"],
        opening_prompts={SupportLevel(k): v for k, v in prompts_raw.items()},
        maximum_support_turns_per_micro_goal=row["max_support_turns"],
        maximum_turn_count=row["maximum_turn_count"],
    )


def load_scenario(conn: sqlite3.Connection, scenario_id: str) -> Scenario | None:
    """id 하나로 시나리오를 읽는다 — SCN-GEN- 접두사를 따지지 않는다.

    load_generated_scenarios 는 "프로세스가 죽으면 사라지는 생성분"만 되살리는 게
    목적이라 접두사로 걸러낸다. 여기는 지난 세션을 다시 읽는 용도라서 그 필터가
    있으면 안 된다 — TS 시절 픽스처로 돌린 세션도 기록으로는 남아 있기 때문이다.
    """
    row = conn.execute(
        "SELECT * FROM SCENARIO WHERE scenario_id=?", (scenario_id,)
    ).fetchone()
    return _scenario_from_row(conn, row) if row is not None else None


def delete_generated_scenario(conn: sqlite3.Connection, scenario_id: str) -> dict[str, int]:
    """지어진 시나리오와 그것으로 돌린 기록을 모두 지운다.

    픽스처는 지우지 않는다 — 원본이 코드라서 지워도 다음 실행에 다시 생긴다.
    스키마에 ON DELETE CASCADE 가 없어서 딸린 테이블을 손으로 훑는다.
    """
    if not scenario_id.startswith(GENERATED_PREFIX):
        raise ValueError(f"지울 수 없는 시나리오다(픽스처): {scenario_id}")

    sessions = [
        row["session_id"]
        for row in conn.execute(
            "SELECT session_id FROM ROLEPLAY_SESSION WHERE scenario_id=?", (scenario_id,)
        )
    ]
    turns: list[str] = []
    if sessions:
        marks = ",".join("?" * len(sessions))
        turns = [
            row["turn_id"]
            for row in conn.execute(
                f"SELECT turn_id FROM CONVERSATION_TURN WHERE session_id IN ({marks})", sessions
            )
        ]

    removed = {"sessions": len(sessions), "turns": len(turns)}

    if turns:
        marks = ",".join("?" * len(turns))
        for table in (
            # TEST_TURN 이 CONVERSATION_TURN 을 참조한다. 이걸 먼저 지우지 않으면
            # 아래 CONVERSATION_TURN 삭제가 FK 위반으로 실패한다.
            "TEST_TURN",
            "STAGE_TIMING",
            "TURN_CANDIDATE",
            "INTAKE_RESULT",
            "GOAL_SCAN_REJECTION",
        ):
            conn.execute(f"DELETE FROM {table} WHERE turn_id IN ({marks})", turns)
    if sessions:
        marks = ",".join("?" * len(sessions))
        for table in (
            "SAFETY_INCIDENT",
            "SUPPORT_LEVEL_CHANGE",
            "SESSION_GOAL_STATUS",
            "CONVERSATION_TURN",
            "ROLEPLAY_SESSION",
        ):
            conn.execute(f"DELETE FROM {table} WHERE session_id IN ({marks})", sessions)

    conn.execute("DELETE FROM SUB_GOAL WHERE scenario_id=?", (scenario_id,))
    conn.execute("DELETE FROM SCENARIO WHERE scenario_id=?", (scenario_id,))
    conn.commit()
    return removed


def save_generation_metrics(
    conn: sqlite3.Connection, scenario_id: str, *, total_ms: int, timings: list[dict]
) -> None:
    """시나리오를 짓는 데 걸린 시간. 시나리오 내용이 아니라 만들어진 경위라서 따로 쓴다."""
    conn.execute(
        "UPDATE SCENARIO SET generation_total_ms=?, generation_timings=? WHERE scenario_id=?",
        (total_ms, _json(timings), scenario_id),
    )
    conn.commit()


def load_generation_metrics(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT scenario_id, generation_total_ms, generation_timings FROM SCENARIO
           WHERE generation_total_ms IS NOT NULL"""
    ).fetchall()
    return {
        row["scenario_id"]: {
            "total_ms": row["generation_total_ms"],
            "timings": json.loads(row["generation_timings"] or "[]"),
        }
        for row in rows
    }


def subgoal_key(scenario_id: str, goal_id: str) -> str:
    """SUB_GOAL 의 PK. 목표 번호만으로는 시나리오 간에 충돌한다."""
    return f"{scenario_id}:{goal_id}"


def upsert_scenario(conn: sqlite3.Connection, scenario: Scenario) -> None:
    conn.execute(
        """INSERT INTO SCENARIO (scenario_id, scenario_version, title, scenario_level,
               scenario_facts, prohibited_inferences, persona, mascot_intro,
               character_opening_line, maximum_turn_count, max_support_turns)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(scenario_id) DO UPDATE SET
               scenario_version=excluded.scenario_version, title=excluded.title""",
        (
            scenario.scenario_id,
            scenario.scenario_version,
            scenario.title,
            scenario.scenario_level,
            _json(scenario.scenario_facts),
            _json(scenario.prohibited_inferences),
            _json(scenario.persona.model_dump()),
            scenario.mascot_intro,
            scenario.character_opening_line,
            scenario.maximum_turn_count,
            scenario.maximum_support_turns_per_micro_goal,
        ),
    )
    for order_no, goal in enumerate(scenario.micro_goals, start=1):
        conn.execute(
            """INSERT INTO SUB_GOAL (subgoal_id, scenario_id, order_no, description,
                   required_evidence, opening_prompts)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(subgoal_id) DO UPDATE SET
                   description=excluded.description,
                   required_evidence=excluded.required_evidence,
                   opening_prompts=excluded.opening_prompts""",
            (
                # 목표 번호는 시나리오마다 MG-01 부터 다시 시작한다. 그대로 PK 로 쓰면
                # 새 시나리오가 기존 시나리오의 목표 설명을 덮어쓴다(실제로 그랬다).
                subgoal_key(scenario.scenario_id, goal.id),
                scenario.scenario_id,
                order_no,
                goal.description,
                _json(goal.required_evidence),
                _json({k.value: v for k, v in scenario.opening_prompts.items()}),
            ),
        )
    conn.commit()


def create_session(conn: sqlite3.Connection, session: SessionState) -> None:
    conn.execute(
        """INSERT INTO ROLEPLAY_SESSION
               (session_id, scenario_id, status, phase, turn_index, started_at)
           VALUES (?,?,?,?,?,?)""",
        (
            session.session_id,
            session.scenario_id,
            session.status.value,
            session.phase.value,
            session.turn_index,
            _now(),
        ),
    )
    for goal in session.goals.values():
        conn.execute(
            """INSERT INTO SESSION_GOAL_STATUS
                   (status_id, session_id, subgoal_id, status, reached_level)
               VALUES (?,?,?,?,?)""",
            (
                uuid.uuid4().hex,
                session.session_id,
                goal.goal_id,
                goal.status.value,
                goal.support_level.value,
            ),
        )
    conn.commit()


def _safety_status(record: TurnRecord) -> SafetyStatus:
    if record.mascot_mode is MascotMode.HALT:
        return SafetyStatus.ESCALATED
    if record.intake.category is IntakeCategory.PII:
        return SafetyStatus.MASKED
    return SafetyStatus.SAFE


def _incident_type(record: TurnRecord) -> IncidentType | None:
    category = record.intake.category
    if category is IntakeCategory.RISK:
        self_harm = any("self-harm" in c for c in record.moderation.categories)
        return IncidentType.SELF_HARM_SIGNAL if self_harm else IncidentType.HARM_TO_OTHERS
    if category is IntakeCategory.HIGH_CONCERN:
        return IncidentType.AGGRESSION
    if category is IntakeCategory.PROFANITY:
        return IncidentType.PROFANITY
    if category is IntakeCategory.PII:
        return IncidentType.PII_EXPOSURE
    return None


def save_turn(
    conn: sqlite3.Connection, session: SessionState, record: TurnRecord
) -> str:
    turn_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO CONVERSATION_TURN
               (turn_id, session_id, turn_number, input_type, raw_utterance,
                standardized_utterance, safety_status, focus_subgoal_id, support_level,
                hint_source, mascot_mode, mascot_line, closing_line, agent_response_text,
                fallback_used, retry_count, degraded_stages, total_ms, end_reason, delivered_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            turn_id,
            record.session_id,
            record.turn_index,
            InputType.CHAT.value,
            record.child_input,
            record.child_input_stored,
            _safety_status(record).value,
            record.focus_goal_id,
            record.support_level.value,
            record.hint_source.value,
            record.mascot_mode.value,
            record.mascot_line,
            record.closing_line,
            record.delivered_text,
            int(record.fallback_used),
            max(len(record.candidates) - 1, 0),
            _json(record.degraded_stages),
            record.total_ms,
            record.end_reason.value if record.end_reason else None,
            _now(),
        ),
    )

    intake = record.intake
    moderation = record.moderation
    conn.execute(
        """INSERT INTO INTAKE_RESULT
               (turn_id, category, reason, misunderstanding, character_bind, goal_progress,
                masked, override_source, degraded, moderation_available,
                moderation_flagged, moderation_severity, moderation_categories, moderation_scores)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            turn_id,
            intake.category.value,
            intake.reason,
            int(intake.misunderstanding),
            int(intake.character_bind),
            intake.goal_progress.value,
            int(bool(intake.masked_text)),
            intake.override_source,
            int(intake.degraded),
            int(moderation.available),
            int(moderation.flagged),
            moderation.severity.value,
            _json(moderation.categories),
            _json(moderation.category_scores),
        ),
    )

    for candidate in record.candidates:
        judge = candidate.judge
        conn.execute(
            """INSERT INTO TURN_CANDIDATE
                   (candidate_id, turn_id, attempt_no, text, outcome, delivered,
                    judge_safe_to_send, judge_decision, judge_failure_codes, judge_reason,
                    judge_revision, referee_violations, moderation_flagged, moderation_categories)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex,
                turn_id,
                candidate.attempt_no,
                candidate.text,
                candidate.outcome,
                int(candidate.delivered),
                int(judge.safe_to_send) if judge else None,
                judge.decision if judge else None,
                _json(judge.failure_codes) if judge else None,
                judge.reason if judge else None,
                _json({"change": judge.revision_change, "avoid": judge.revision_avoid})
                if judge
                else None,
                _json([v.value for v in candidate.referee_violations]),
                int(candidate.moderation.flagged) if candidate.moderation else None,
                _json(candidate.moderation.categories) if candidate.moderation else None,
            ),
        )

    for timing in record.timings:
        conn.execute(
            """INSERT INTO STAGE_TIMING
                   (turn_id, stage, attempt_no, start_offset_ms, duration_ms, ok)
               VALUES (?,?,?,?,?,?)""",
            (
                turn_id,
                timing.stage,
                timing.attempt_no,
                timing.start_offset_ms,
                timing.duration_ms,
                int(timing.ok),
            ),
        )

    for rejected in record.goal_scan.rejected:
        conn.execute(
            """INSERT INTO GOAL_SCAN_REJECTION (turn_id, subgoal_id, evidence, reason)
               VALUES (?,?,?,?)""",
            (turn_id, rejected.goal_id, rejected.evidence, rejected.reason),
        )

    # 안전 사건은 일반 학습 기록과 분리해 남긴다(SAFE-06).
    if incident_type := _incident_type(record):
        action = (
            IncidentAction.ESCALATED_TO_INSTRUCTOR
            if record.mascot_mode is MascotMode.HALT
            else IncidentAction.SAFE_REPHRASE
        )
        conn.execute(
            """INSERT INTO SAFETY_INCIDENT
                   (incident_id, session_id, turn_id, source, incident_type, action_taken,
                    detail, occurred_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex,
                record.session_id,
                turn_id,
                IncidentSource.LEARNER_INPUT.value,
                incident_type.value,
                action.value,
                record.intake.reason,
                _now(),
            ),
        )
    if any(c.moderation and c.moderation.flagged for c in record.candidates):
        conn.execute(
            """INSERT INTO SAFETY_INCIDENT
                   (incident_id, session_id, turn_id, source, incident_type, action_taken,
                    detail, occurred_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex,
                record.session_id,
                turn_id,
                IncidentSource.AI_OUTPUT.value,
                IncidentType.AGE_INAPPROPRIATE.value,
                (
                    IncidentAction.FIXED_SAFE_MESSAGE
                    if record.fallback_used
                    else IncidentAction.SAFE_REPHRASE
                ).value,
                "Moderation 이 AI 후보를 유해로 판정해 재생성",
                _now(),
            ),
        )

    # 도움 세기가 올라간 내역. 사유까지 남겨야 "왜 올라갔나"를 되짚을 수 있다.
    for change in record.support_changes:
        conn.execute(
            """INSERT INTO SUPPORT_LEVEL_CHANGE
                   (change_id, session_id, turn_id, subgoal_id, from_level, to_level,
                    reason_code, changed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex,
                session.session_id,
                turn_id,
                change.goal_id,
                change.from_level.value,
                change.to_level.value,
                change.reason.value,
                _now(),
            ),
        )

    # 목표 상태와 세션 상태 갱신
    for goal in session.goals.values():
        conn.execute(
            """UPDATE SESSION_GOAL_STATUS
                  SET status=?, reached_level=?, turns_to_reach=?, evidence_text=?,
                      why_this_satisfies=?, achieved_with_support=?, guidance_count=?,
                      updated_turn_id=CASE WHEN ?=1 THEN ? ELSE updated_turn_id END
                WHERE session_id=? AND subgoal_id=?""",
            (
                goal.status.value,
                goal.support_level.value,
                goal.achieved_at_turn,
                goal.evidence_text,
                goal.why_this_satisfies,
                int(goal.achieved_with_support),
                goal.support_turns_used,
                int(goal.achieved_at_turn == record.turn_index),
                turn_id,
                session.session_id,
                goal.goal_id,
            ),
        )

    conn.execute(
        """UPDATE ROLEPLAY_SESSION
              SET status=?, phase=?, turn_index=?, end_reason=?, ended_at=?
            WHERE session_id=?""",
        (
            session.status.value,
            session.phase.value,
            session.turn_index,
            session.end_reason.value if session.end_reason else None,
            _now() if session.end_reason else None,
            session.session_id,
        ),
    )
    conn.commit()
    return turn_id


# ---------------------------------------------------------------- 읽기


def load_session_turns(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """관측 콘솔이 렌더링할 턴 목록. 저장된 것을 그대로 되읽는다."""
    turns = conn.execute(
        "SELECT * FROM CONVERSATION_TURN WHERE session_id=? ORDER BY turn_number",
        (session_id,),
    ).fetchall()

    out: list[dict] = []
    for turn in turns:
        turn_id = turn["turn_id"]
        intake = conn.execute(
            "SELECT * FROM INTAKE_RESULT WHERE turn_id=?", (turn_id,)
        ).fetchone()
        candidates = conn.execute(
            "SELECT * FROM TURN_CANDIDATE WHERE turn_id=? ORDER BY attempt_no", (turn_id,)
        ).fetchall()
        timings = conn.execute(
            "SELECT * FROM STAGE_TIMING WHERE turn_id=? ORDER BY start_offset_ms", (turn_id,)
        ).fetchall()
        rejections = conn.execute(
            "SELECT * FROM GOAL_SCAN_REJECTION WHERE turn_id=?", (turn_id,)
        ).fetchall()

        out.append(
            {
                "turn_id": turn_id,
                "turn_number": turn["turn_number"],
                "child_input": turn["raw_utterance"],
                "child_input_stored": turn["standardized_utterance"],
                "safety_status": turn["safety_status"],
                "focus_subgoal_id": turn["focus_subgoal_id"],
                "support_level": turn["support_level"],
                "mascot_mode": turn["mascot_mode"],
                "mascot_line": turn["mascot_line"],
                "closing_line": turn["closing_line"],
                "delivered_text": turn["agent_response_text"],
                "fallback_used": bool(turn["fallback_used"]),
                "degraded_stages": json.loads(turn["degraded_stages"]),
                "total_ms": turn["total_ms"],
                "end_reason": turn["end_reason"],
                "intake": dict(intake) if intake else None,
                "candidates": [dict(c) for c in candidates],
                "timings": [dict(t) for t in timings],
                "goal_rejections": [dict(r) for r in rejections],
            }
        )
    return out


def load_goal_statuses(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM SESSION_GOAL_STATUS WHERE session_id=? ORDER BY subgoal_id",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_sessions(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM ROLEPLAY_SESSION ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def hint_summary(conn: sqlite3.Connection, session_id: str) -> dict:
    """아이가 도움을 몇 번, 누구에게, 어떤 세기로 받았는가.

    마스코트 개입 횟수만 세면 안 된다 — S0 에서는 캐릭터 프롬프트에 목표 힌트가 들어가서
    캐릭터가 대사 속에 유도한다. 아이 입장에서는 그것도 도움받은 턴이다.
    """
    by_source = {
        row["hint_source"]: row["n"]
        for row in conn.execute(
            """SELECT hint_source, COUNT(*) AS n FROM CONVERSATION_TURN
               WHERE session_id=? GROUP BY hint_source""",
            (session_id,),
        )
    }
    turns = sum(by_source.values())
    from_mascot = by_source.get("MASCOT", 0)
    from_character = by_source.get("CHARACTER", 0)

    escalations = [
        dict(row)
        for row in conn.execute(
            """SELECT c.subgoal_id, c.from_level, c.to_level, c.reason_code, t.turn_number
                 FROM SUPPORT_LEVEL_CHANGE c
                 JOIN CONVERSATION_TURN t ON t.turn_id = c.turn_id
                WHERE c.session_id=? ORDER BY t.turn_number""",
            (session_id,),
        )
    ]
    # S3 는 정답을 알려주는 단계다. 여기까지 갔는지가 지표에서 가장 무겁다.
    answer_revealed = sum(1 for e in escalations if e["to_level"] == SupportLevel.S3.value)

    return {
        "turns": turns,
        "hinted_turns": from_mascot + from_character,
        "from_mascot": from_mascot,
        "from_character": from_character,
        "unhinted_turns": by_source.get("NONE", 0),
        "escalations": escalations,
        "answer_revealed": answer_revealed,
    }


def goal_summary(conn: sqlite3.Connection, session_id: str) -> dict:
    """PoC 성공률 지표. achieved_with_support 를 분리해서 센다 —
    S3 정답 공개 뒤 달성된 것을 스스로 도달한 것과 같이 세면 지표가 허구가 된다."""
    rows = load_goal_statuses(conn, session_id)
    achieved = [r for r in rows if r["status"] == GoalObservationStatus.ACHIEVED.value]
    unaided = [r for r in achieved if not r["achieved_with_support"]]
    return {
        "total": len(rows),
        "achieved": len(achieved),
        "achieved_without_answer_reveal": len(unaided),
        "abandoned": sum(
            1 for r in rows if r["status"] == GoalObservationStatus.ABANDONED.value
        ),
    }


# ------------------------------------------------------- 시뮬레이션 실행


def start_test_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    base_url: str,
    agent_model: str,
    moderation_model: str | None,
    suites: list[str],
    note: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO TEST_RUN (
            run_id, started_at, base_url, agent_model, moderation_model, suites, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, _now(), base_url, agent_model, moderation_model, _json(suites), note),
    )
    conn.commit()


def finish_test_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    scenario_count: int,
    session_count: int,
    turn_count: int,
) -> None:
    conn.execute(
        """
        UPDATE TEST_RUN
           SET ended_at = ?, scenario_count = ?, session_count = ?, turn_count = ?
         WHERE run_id = ?
        """,
        (_now(), scenario_count, session_count, turn_count, run_id),
    )
    conn.commit()


def record_test_turn(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    turn_id: str,
    suite: str,
    scenario_id: str,
    session_id: str,
    probe_id: str | None,
    expectation: dict,
    verdict: str,
    findings: list[str],
    client_ms: int,
) -> None:
    conn.execute(
        """
        INSERT INTO TEST_TURN (
            run_id, turn_id, suite, scenario_id, session_id, probe_id,
            expectation, verdict, findings, client_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            turn_id,
            suite,
            scenario_id,
            session_id,
            probe_id,
            _json(expectation),
            verdict,
            _json(findings),
            client_ms,
        ),
    )
    conn.commit()


def load_test_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM TEST_RUN ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def load_test_turns(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """실행에 속한 턴을 회차 꼬리표와 실제 턴 기록을 붙여서 돌려준다.
    STAGE_TIMING 은 여기서 안 읽는다 — 단계 통계는 run_timing_stats 가 SQL 로 뽑는다."""
    rows = conn.execute(
        """
        SELECT tt.*, ct.turn_number, ct.total_ms, ct.mascot_mode, ct.support_level,
               ct.hint_source, ct.safety_status, ct.fallback_used, ct.retry_count,
               ct.degraded_stages, ct.end_reason, ct.agent_response_text,
               ir.category, ir.override_source, ir.degraded,
               ir.moderation_available, ir.moderation_severity
          FROM TEST_TURN tt
          JOIN CONVERSATION_TURN ct ON ct.turn_id = tt.turn_id
          LEFT JOIN INTAKE_RESULT ir ON ir.turn_id = tt.turn_id
         WHERE tt.run_id = ?
         ORDER BY tt.scenario_id, tt.suite, ct.turn_number
        """,
        (run_id,),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["expectation"] = json.loads(item["expectation"])
        item["findings"] = json.loads(item["findings"])
        item["degraded_stages"] = json.loads(item["degraded_stages"])
        out.append(item)
    return out


def run_timing_stats(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """단계별 소요 분포. 백분위는 SQLite 에 함수가 없어서 값을 정렬해 받아 파이썬에서 센다."""
    rows = conn.execute(
        """
        SELECT st.stage, st.duration_ms, st.ok
          FROM STAGE_TIMING st
          JOIN TEST_TURN tt ON tt.turn_id = st.turn_id
         WHERE tt.run_id = ?
         ORDER BY st.stage, st.duration_ms
        """,
        (run_id,),
    ).fetchall()

    grouped: dict[str, list[int]] = {}
    failures: dict[str, int] = {}
    for row in rows:
        grouped.setdefault(row["stage"], []).append(row["duration_ms"])
        if not row["ok"]:
            failures[row["stage"]] = failures.get(row["stage"], 0) + 1

    return [
        {
            "stage": stage,
            "n": len(values),
            "min_ms": values[0],
            "p50_ms": _percentile(values, 0.50),
            "p95_ms": _percentile(values, 0.95),
            "max_ms": values[-1],
            "failed": failures.get(stage, 0),
        }
        for stage, values in sorted(grouped.items())
    ]


def _percentile(sorted_values: list[int], fraction: float) -> int:
    """poc/src/util.ts 의 percentile 과 같은 방식 — 가장 가까운 순위(nearest-rank).
    보간을 넣으면 실측하지 않은 값이 리포트에 찍힌다."""
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


# ------------------------------------------------------- 지난 세션 되읽기
#
# 세션 진행 상태의 원본은 서버 프로세스 메모리다(routes_session._live). DB 는 관측용
# 기록이고, 그 둘을 뒤집지 않는다. 다만 "이미 끝난 세션을 다시 읽는" 것은 관측이라서
# 여기서 만든다 — 서버가 재시작되면 기록이 DB 에 다 있는데도 읽을 수 없던 문제가
# 시뮬레이션 요약을 통째로 날렸다.
#
# 여기서 되살린 세션으로 새 턴을 이어 갈 수는 없다. 대사 원문은 남지만 후보 선정에
# 쓰인 맥락과 초점 목표의 중간 상태까지 복원되는 것은 아니다. 읽기 전용이다.


def load_transcript(conn: sqlite3.Connection, session_id: str, scenario: Scenario) -> list[Utterance]:
    """턴 기록에서 대화를 다시 세운다. 순서는 run_turn 이 쌓는 순서와 같아야 한다 —
    아이 → 마스코트 → 캐릭터 → (종료 시) 작별 인사."""
    lines = [
        Utterance(speaker=Speaker.MASCOT, text=scenario.mascot_intro, turn_index=0),
        Utterance(
            speaker=Speaker.CHARACTER, text=scenario.character_opening_line, turn_index=0
        ),
    ]
    rows = conn.execute(
        """SELECT turn_number, standardized_utterance, mascot_mode, mascot_line,
                  agent_response_text, closing_line
             FROM CONVERSATION_TURN WHERE session_id=? ORDER BY turn_number""",
        (session_id,),
    ).fetchall()

    for row in rows:
        index = row["turn_number"]
        # 중단된 턴의 아이 발화는 애초에 기록에 넣지 않았다(SAFE-06). 되읽을 때도
        # 넣지 않는다 — 여기서 넣으면 화면에 남지 않던 문장이 되살아난다.
        if row["mascot_mode"] != MascotMode.HALT.value:
            lines.append(
                Utterance(
                    speaker=Speaker.CHILD,
                    text=row["standardized_utterance"],
                    turn_index=index,
                )
            )
        if row["mascot_line"]:
            lines.append(
                Utterance(speaker=Speaker.MASCOT, text=row["mascot_line"], turn_index=index)
            )
        if row["agent_response_text"]:
            lines.append(
                Utterance(
                    speaker=Speaker.CHARACTER,
                    text=row["agent_response_text"],
                    turn_index=index,
                )
            )
        if row["closing_line"]:
            lines.append(
                Utterance(speaker=Speaker.MASCOT, text=row["closing_line"], turn_index=index)
            )
    return lines


def load_session_state(
    conn: sqlite3.Connection, session_id: str
) -> tuple[SessionState, Scenario] | None:
    """지난 세션을 읽기 전용으로 되살린다. 세션이나 시나리오가 없으면 None."""
    row = conn.execute(
        "SELECT * FROM ROLEPLAY_SESSION WHERE session_id=?", (session_id,)
    ).fetchone()
    if row is None:
        return None

    scenario = load_scenario(conn, row["scenario_id"])
    if scenario is None:
        return None

    statuses = {
        status["subgoal_id"]: status
        for status in conn.execute(
            "SELECT * FROM SESSION_GOAL_STATUS WHERE session_id=?", (session_id,)
        )
    }
    goals: dict[str, GoalState] = {}
    for goal in scenario.micro_goals:
        saved = statuses.get(goal.id)
        if saved is None:
            goals[goal.id] = GoalState(goal_id=goal.id)
            continue
        goals[goal.id] = GoalState(
            goal_id=goal.id,
            status=GoalObservationStatus(saved["status"]),
            support_level=SupportLevel(saved["reached_level"]),
            support_turns_used=saved["guidance_count"],
            achieved_at_turn=saved["turns_to_reach"],
            evidence_text=saved["evidence_text"],
            why_this_satisfies=saved["why_this_satisfies"],
            achieved_with_support=bool(saved["achieved_with_support"]),
        )

    session = SessionState(
        session_id=session_id,
        scenario_id=row["scenario_id"],
        phase=SessionPhase(row["phase"]),
        status=SessionStatus(row["status"]),
        turn_index=row["turn_index"],
        transcript=load_transcript(conn, session_id, scenario),
        goals=goals,
        end_reason=EndReason(row["end_reason"]) if row["end_reason"] else None,
    )
    return session, scenario


# 세션 하나에 딸린 기록이 흩어져 있는 테이블. 지우는 순서가 곧 FK 순서다 —
# 참조하는 쪽을 먼저 지우지 않으면 PRAGMA foreign_keys=ON 이 삭제를 거부한다.
_BY_TURN_ID: tuple[str, ...] = (
    "TEST_TURN",
    "STAGE_TIMING",
    "TURN_CANDIDATE",
    "INTAKE_RESULT",
    "GOAL_SCAN_REJECTION",
)
_BY_SESSION_ID: tuple[str, ...] = (
    "SAFETY_INCIDENT",
    "SUPPORT_LEVEL_CHANGE",
    "SESSION_GOAL_STATUS",
    "CONVERSATION_TURN",
    "ROLEPLAY_SESSION",
)


def unfinished_sessions(conn: sqlite3.Connection) -> list[dict]:
    """끝까지 가지 않은 세션.

    end_reason 이 없다는 건 목표 달성도, 턴 상한도, 안전 중단도 아닌 채로 멈췄다는
    뜻이다. 사람이 콘솔에서 몇 마디 해보고 닫은 것이 대부분이고, 대화가 중간에
    끊겨 있어서 기록으로 읽어도 판단 근거가 되지 않는다.
    """
    rows = conn.execute(
        """
        SELECT s.session_id, s.scenario_id, s.turn_index, s.started_at,
               EXISTS(SELECT 1 FROM TEST_TURN t WHERE t.session_id = s.session_id) AS from_sweep
          FROM ROLEPLAY_SESSION s
         WHERE s.end_reason IS NULL
         ORDER BY s.started_at
        """
    ).fetchall()
    return [dict(row) for row in rows]


def delete_sessions(conn: sqlite3.Connection, session_ids: list[str]) -> dict[str, int]:
    """세션과 그에 딸린 모든 기록을 지운다. 시나리오는 건드리지 않는다.

    스키마에 ON DELETE CASCADE 가 없어서 딸린 테이블을 손으로 훑는다
    (delete_generated_scenario 가 같은 이유로 같은 일을 한다).
    """
    if not session_ids:
        return {"sessions": 0, "turns": 0}

    marks = ",".join("?" * len(session_ids))
    turns = [
        row["turn_id"]
        for row in conn.execute(
            f"SELECT turn_id FROM CONVERSATION_TURN WHERE session_id IN ({marks})",
            session_ids,
        )
    ]

    if turns:
        turn_marks = ",".join("?" * len(turns))
        for table in _BY_TURN_ID:
            conn.execute(f"DELETE FROM {table} WHERE turn_id IN ({turn_marks})", turns)
    for table in _BY_SESSION_ID:
        conn.execute(f"DELETE FROM {table} WHERE session_id IN ({marks})", session_ids)
    conn.commit()
    return {"sessions": len(session_ids), "turns": len(turns)}
