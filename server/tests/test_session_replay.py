"""지난 세션 되읽기 — 기록이 DB 에 있으면 읽을 수 있어야 한다.

예전에는 세션 조회가 메모리에만 있는 것만 답하고 나머지는 404 였다. 서버가 한 번
재시작되면 턴이 DB 에 다 남아 있는데도 그 세션을 읽을 수 없었고, 시뮬레이션은 세션
요약을 통째로 잃었다. 기록을 정확히 남기는 게 목적인 실행에서 그건 쓸 수 없다.

동시에 되살린 세션으로 새 턴을 이어 갈 수는 없다는 것도 지켜야 한다 — 진행 상태의
원본은 프로세스 메모리이고 DB 는 관측용 기록이라는 구분이 뒤집히면, 반쯤 복원된
맥락으로 아이에게 대사가 나간다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from db import repo
from state.models import (
    CandidateRecord,
    EndReason,
    GoalObservationStatus,
    GoalScanRecord,
    HintSource,
    IntakeCategory,
    IntakeRecord,
    MascotMode,
    ModerationOutcome,
    SessionPhase,
    Speaker,
    SupportLevel,
    TurnRecord,
)
from tests.conftest import TEST_SCENARIO, make_session


@pytest.fixture
def conn(tmp_path: Path):
    connection = repo.connect(tmp_path / "replay.db")
    yield connection
    connection.close()


def turn_record(
    session,
    *,
    child: str = "친구가 넘어져서 아팠을 것 같아",
    stored: str | None = None,
    mascot_mode: MascotMode = MascotMode.NONE,
    mascot_line: str | None = None,
    delivered: str | None = "응... 무릎이 좀 그래.",
    closing: str | None = None,
    category: IntakeCategory = IntakeCategory.NORMAL,
    end_reason: EndReason | None = None,
) -> TurnRecord:
    return TurnRecord(
        session_id=session.session_id,
        turn_index=session.turn_index,
        child_input=child,
        child_input_stored=stored if stored is not None else child,
        intake=IntakeRecord(category=category, reason="테스트"),
        moderation=ModerationOutcome(available=True),
        mascot_mode=mascot_mode,
        mascot_line=mascot_line,
        closing_line=closing,
        focus_goal_id="MG-01",
        support_level=SupportLevel.S0,
        hint_source=HintSource.CHARACTER,
        candidates=(
            [CandidateRecord(attempt_no=1, text=delivered, delivered=True, outcome="PASS")]
            if delivered
            else []
        ),
        delivered_text=delivered,
        goal_scan=GoalScanRecord(scanned_goal_ids=["MG-01", "MG-02", "MG-03"]),
        total_ms=1234,
        phase_after=SessionPhase.ENDED if end_reason else SessionPhase.ACTIVE,
        end_reason=end_reason,
    )


def seed(conn, *, turns: int = 2):
    """세션 하나를 DB 에 심는다 — 라우트가 하는 것과 같은 순서로."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id="S-REPLAY")
    repo.create_session(conn, session)
    for index in range(1, turns + 1):
        session.turn_index = index
        repo.save_turn(conn, session, turn_record(session))
    return session


# ------------------------------------------------------------- 시나리오


def test_load_scenario_ignores_the_generated_prefix(conn) -> None:
    """SCN-GEN- 이 아닌 시나리오로 돌린 세션도 기록으로는 남아 있다.
    load_generated_scenarios 의 접두사 필터를 그대로 쓰면 그 세션을 영원히 못 읽는다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)

    assert TEST_SCENARIO.scenario_id not in repo.load_generated_scenarios(conn)
    loaded = repo.load_scenario(conn, TEST_SCENARIO.scenario_id)
    assert loaded is not None
    assert loaded.title == TEST_SCENARIO.title
    assert [g.id for g in loaded.micro_goals] == ["MG-01", "MG-02", "MG-03"]


def test_load_scenario_returns_none_for_unknown(conn) -> None:
    assert repo.load_scenario(conn, "SCN-NOPE") is None


def test_subgoal_prefix_is_stripped_on_load(conn) -> None:
    """SUB_GOAL 은 시나리오 id 를 붙인 키로 저장한다. 벗기지 않으면 목표 id 가
    안 맞아서 되살린 세션의 목표 상태가 전부 초기값으로 보인다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    loaded = repo.load_scenario(conn, TEST_SCENARIO.scenario_id)

    assert all(not g.id.startswith("SCN-") for g in loaded.micro_goals)


# --------------------------------------------------------------- 되읽기


def test_unknown_session_is_none(conn) -> None:
    assert repo.load_session_state(conn, "없는세션") is None


def test_restored_session_carries_phase_and_turn_index(conn) -> None:
    seed(conn, turns=2)

    session, scenario = repo.load_session_state(conn, "S-REPLAY")

    assert session.turn_index == 2
    assert session.phase is SessionPhase.ACTIVE
    assert session.end_reason is None
    assert scenario.scenario_id == TEST_SCENARIO.scenario_id


def test_restored_transcript_keeps_the_opening_and_turn_order(conn) -> None:
    """대화 순서가 run_turn 이 쌓는 순서와 같아야 한다 — 아이 → 마스코트 → 캐릭터.
    순서가 어긋나면 되읽은 대화만 보고 판단할 수 없다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id="S-ORDER")
    repo.create_session(conn, session)
    session.turn_index = 1
    repo.save_turn(
        conn,
        session,
        turn_record(
            session, mascot_mode=MascotMode.SCAFFOLD, mascot_line="친구 표정이 어때 보여?"
        ),
    )

    restored, _ = repo.load_session_state(conn, "S-ORDER")

    assert [u.speaker for u in restored.transcript] == [
        Speaker.MASCOT,     # 장면 소개
        Speaker.CHARACTER,  # 첫 대사
        Speaker.CHILD,
        Speaker.MASCOT,
        Speaker.CHARACTER,
    ]
    assert restored.transcript[0].text == TEST_SCENARIO.mascot_intro
    assert restored.transcript[1].text == TEST_SCENARIO.character_opening_line


def test_restored_transcript_stores_the_masked_utterance(conn) -> None:
    """마스킹본이 기록의 원본이다. 되읽을 때 원문이 되살아나면 안 된다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id="S-PII")
    repo.create_session(conn, session)
    session.turn_index = 1
    repo.save_turn(
        conn,
        session,
        turn_record(
            session,
            child="내 이름은 김민수야",
            stored="내 이름은 [이름]이야",
            category=IntakeCategory.PII,
        ),
    )

    restored, _ = repo.load_session_state(conn, "S-PII")
    child = [u.text for u in restored.transcript if u.speaker is Speaker.CHILD]

    assert child == ["내 이름은 [이름]이야"]
    assert not any("김민수" in u.text for u in restored.transcript)


def test_halted_turn_does_not_resurrect_the_child_utterance(conn) -> None:
    """중단된 발화는 애초에 기록에 넣지 않았다(SAFE-06). DB 에는 raw_utterance 로
    남아 있으니, 되읽기가 그걸 대화에 끼워 넣으면 화면에 없던 문장이 살아난다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id="S-HALT")
    repo.create_session(conn, session)
    session.turn_index = 1
    session.phase = SessionPhase.ENDED
    session.end_reason = EndReason.SAFETY_HALT
    repo.save_turn(
        conn,
        session,
        turn_record(
            session,
            child="위험한 말",
            mascot_mode=MascotMode.HALT,
            mascot_line="오늘은 여기서 멈출게.",
            delivered=None,
            end_reason=EndReason.SAFETY_HALT,
        ),
    )

    restored, _ = repo.load_session_state(conn, "S-HALT")

    assert not any(u.speaker is Speaker.CHILD for u in restored.transcript)
    assert all("위험한 말" != u.text for u in restored.transcript)
    assert restored.phase is SessionPhase.ENDED
    assert restored.end_reason is EndReason.SAFETY_HALT


def test_closing_line_is_restored_as_a_mascot_utterance(conn) -> None:
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id="S-CLOSE")
    repo.create_session(conn, session)
    session.turn_index = 1
    session.phase = SessionPhase.ENDED
    session.end_reason = EndReason.ALL_GOALS
    repo.save_turn(
        conn,
        session,
        turn_record(session, closing="멍멍! 오늘 잘했어.", end_reason=EndReason.ALL_GOALS),
    )

    restored, _ = repo.load_session_state(conn, "S-CLOSE")

    assert restored.transcript[-1].speaker is Speaker.MASCOT
    assert restored.transcript[-1].text == "멍멍! 오늘 잘했어."


def test_restored_goal_states_come_back(conn) -> None:
    """달성 여부와 도달 수준이 살아나야 리포트의 지표가 되읽기에서도 같다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id="S-GOALS")
    repo.create_session(conn, session)
    goal = session.goals["MG-01"]
    goal.status = GoalObservationStatus.ACHIEVED
    goal.support_level = SupportLevel.S2
    goal.support_turns_used = 2
    goal.achieved_at_turn = 1
    goal.evidence_text = "넘어져서 아팠을"
    goal.why_this_satisfies = "사건과 기분을 함께 말했다"
    goal.achieved_with_support = True
    session.turn_index = 1
    repo.save_turn(conn, session, turn_record(session))

    restored, _ = repo.load_session_state(conn, "S-GOALS")
    back = restored.goals["MG-01"]

    assert back.status is GoalObservationStatus.ACHIEVED
    assert back.support_level is SupportLevel.S2
    assert back.support_turns_used == 2
    assert back.achieved_at_turn == 1
    assert back.evidence_text == "넘어져서 아팠을"
    assert back.achieved_with_support is True


def test_goal_summary_matches_after_a_round_trip(conn) -> None:
    """되읽은 세션의 지표가 원래 세션과 달라지면 리포트 두 개가 서로 다른 말을 한다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id="S-SUM")
    repo.create_session(conn, session)
    session.goals["MG-01"].status = GoalObservationStatus.ACHIEVED
    session.goals["MG-02"].status = GoalObservationStatus.ABANDONED
    session.turn_index = 1
    repo.save_turn(conn, session, turn_record(session))

    summary = repo.goal_summary(conn, "S-SUM")

    assert summary == {
        "total": 3,
        "achieved": 1,
        "achieved_without_answer_reveal": 1,
        "abandoned": 1,
    }


def test_missing_goal_status_falls_back_to_initial_state(conn) -> None:
    """시나리오에 목표가 새로 늘어난 뒤 예전 세션을 읽는 경우다.
    KeyError 로 죽는 대신 초기값으로 채운다 — 읽을 수 있는 게 우선이다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id="S-PARTIAL")
    repo.create_session(conn, session)
    conn.execute(
        "DELETE FROM SESSION_GOAL_STATUS WHERE session_id=? AND subgoal_id=?",
        ("S-PARTIAL", "MG-03"),
    )
    conn.commit()

    restored, _ = repo.load_session_state(conn, "S-PARTIAL")

    assert restored.goals["MG-03"].status is GoalObservationStatus.NOT_OBSERVED


def test_session_without_goal_rows_is_unreadable(conn) -> None:
    """목표 행이 없는 시나리오로는 역할극을 세울 수 없다 — 목표 설명도 유도 문구도
    거기서 온다. 반쪽짜리를 돌려주는 것보다 못 읽는다고 하는 게 낫다.

    시나리오 자체는 지울 수 없다(ROLEPLAY_SESSION 이 참조한다). 그래서 이 상태는
    delete_generated_scenario 가 중간에 끊겼을 때 실제로 생길 수 있는 모양이다."""
    seed(conn, turns=1)
    conn.execute("DELETE FROM SUB_GOAL WHERE scenario_id=?", (TEST_SCENARIO.scenario_id,))
    conn.commit()

    assert repo.load_scenario(conn, TEST_SCENARIO.scenario_id) is None
    assert repo.load_session_state(conn, "S-REPLAY") is None


def test_stale_session_status_is_corrected_on_connect(tmp_path: Path) -> None:
    """status 를 갱신하는 코드가 없던 시절의 행이 DB 에 남아 있다. phase 가 이미
    답을 들고 있으므로 추측이 아니라 정정이다. 두 번 열어도 결과가 같아야 한다."""
    db = tmp_path / "stale.db"
    conn = repo.connect(db)
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id="S-STALE")
    repo.create_session(conn, session)
    # 예전 코드가 남긴 모양을 손으로 만든다 — 끝났는데 status 는 그대로다.
    conn.execute(
        "UPDATE ROLEPLAY_SESSION SET phase='ENDED', end_reason='ALL_GOALS' WHERE session_id=?",
        ("S-STALE",),
    )
    conn.commit()
    assert conn.execute(
        "SELECT status FROM ROLEPLAY_SESSION WHERE session_id=?", ("S-STALE",)
    ).fetchone()[0] == "IN_PROGRESS"
    conn.close()

    reopened = repo.connect(db)
    assert reopened.execute(
        "SELECT status FROM ROLEPLAY_SESSION WHERE session_id=?", ("S-STALE",)
    ).fetchone()[0] == "COMPLETED"
    reopened.close()

    again = repo.connect(db)
    assert again.execute(
        "SELECT COUNT(*) FROM ROLEPLAY_SESSION WHERE phase='ENDED' AND status<>'COMPLETED'"
    ).fetchone()[0] == 0
    again.close()


def test_active_session_status_is_left_alone(tmp_path: Path) -> None:
    conn = repo.connect(tmp_path / "active.db")
    repo.upsert_scenario(conn, TEST_SCENARIO)
    repo.create_session(conn, make_session(session_id="S-ACTIVE"))
    conn.close()

    reopened = repo.connect(tmp_path / "active.db")
    assert reopened.execute(
        "SELECT status FROM ROLEPLAY_SESSION WHERE session_id=?", ("S-ACTIVE",)
    ).fetchone()[0] == "IN_PROGRESS"
    reopened.close()


# ------------------------------------------------------------- 기록 삭제


def test_unfinished_sessions_finds_only_open_ones(conn) -> None:
    repo.upsert_scenario(conn, TEST_SCENARIO)
    open_session = make_session(session_id="S-OPEN")
    repo.create_session(conn, open_session)
    open_session.turn_index = 1
    repo.save_turn(conn, open_session, turn_record(open_session))

    done = make_session(session_id="S-DONE")
    repo.create_session(conn, done)
    done.turn_index = 1
    done.phase = SessionPhase.ENDED
    done.end_reason = EndReason.ALL_GOALS
    repo.save_turn(conn, done, turn_record(done, end_reason=EndReason.ALL_GOALS))

    ids = [row["session_id"] for row in repo.unfinished_sessions(conn)]
    assert ids == ["S-OPEN"]


def test_zero_turn_session_counts_as_unfinished(conn) -> None:
    """시작만 하고 발화 한 번 없이 버려진 세션이다. 목록만 늘린다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    repo.create_session(conn, make_session(session_id="S-EMPTY"))

    rows = repo.unfinished_sessions(conn)
    assert [r["session_id"] for r in rows] == ["S-EMPTY"]
    assert rows[0]["turn_index"] == 0


def test_delete_sessions_leaves_no_orphans(conn) -> None:
    """스키마에 ON DELETE CASCADE 가 없어서 딸린 테이블을 손으로 훑는다.
    하나라도 빠뜨리면 고아 행이 남아, 없는 세션의 턴을 리포트가 세게 된다."""
    seed(conn, turns=2)

    removed = repo.delete_sessions(conn, ["S-REPLAY"])

    assert removed == {"sessions": 1, "turns": 2}
    for table, key, parent, parent_key in (
        ("STAGE_TIMING", "turn_id", "CONVERSATION_TURN", "turn_id"),
        ("TURN_CANDIDATE", "turn_id", "CONVERSATION_TURN", "turn_id"),
        ("INTAKE_RESULT", "turn_id", "CONVERSATION_TURN", "turn_id"),
        ("SESSION_GOAL_STATUS", "session_id", "ROLEPLAY_SESSION", "session_id"),
        ("CONVERSATION_TURN", "session_id", "ROLEPLAY_SESSION", "session_id"),
    ):
        left = conn.execute(
            f"SELECT COUNT(*) FROM {table} x"
            f" WHERE NOT EXISTS(SELECT 1 FROM {parent} p WHERE p.{parent_key} = x.{key})"
        ).fetchone()[0]
        assert left == 0, f"{table} 에 고아 행이 남았다"
    assert repo.unfinished_sessions(conn) == []


def test_delete_sessions_removes_test_run_rows(conn) -> None:
    """TEST_TURN 은 CONVERSATION_TURN 을 FK 로 참조한다. 먼저 지우지 않으면
    PRAGMA foreign_keys=ON 이 삭제를 거부한다."""
    seed(conn, turns=1)
    repo.start_test_run(
        conn, run_id="R2", base_url="http://x", agent_model="m",
        moderation_model=None, suites=["play"],
    )
    turn_id = conn.execute(
        "SELECT turn_id FROM CONVERSATION_TURN WHERE session_id=?", ("S-REPLAY",)
    ).fetchone()[0]
    repo.record_test_turn(
        conn, run_id="R2", turn_id=turn_id, suite="play",
        scenario_id=TEST_SCENARIO.scenario_id, session_id="S-REPLAY",
        probe_id="P1", expectation={}, verdict="OK", findings=[], client_ms=1,
    )

    repo.delete_sessions(conn, ["S-REPLAY"])

    assert conn.execute("SELECT COUNT(*) FROM TEST_TURN").fetchone()[0] == 0


def test_delete_sessions_with_no_ids_is_a_noop(conn) -> None:
    seed(conn, turns=1)
    assert repo.delete_sessions(conn, []) == {"sessions": 0, "turns": 0}
    assert conn.execute("SELECT COUNT(*) FROM ROLEPLAY_SESSION").fetchone()[0] == 1
