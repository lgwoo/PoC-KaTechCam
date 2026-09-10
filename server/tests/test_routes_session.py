"""세션 조회·진행 라우트의 404 / 409 구분.

읽기와 쓰기가 요구하는 게 다르다. 조회는 DB 기록만 있으면 되지만, 새 턴은 프로세스
메모리의 진행 상태가 있어야 한다. 예전에는 둘 다 메모리를 요구하면서 없으면 모두
SESSION_NOT_FOUND 였다 — 그래서 "없는 세션"과 "서버가 재시작돼 진행 상태가 사라진
세션"을 구별할 수 없었고, 시뮬레이션은 자기 세션이 왜 사라졌는지 알 수 없었다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from api import routes_session as routes
from db import repo
from state.models import GoalObservationStatus, SessionPhase, Speaker, SupportLevel
from tests.conftest import CLEAN, FakeLlmClient, FakeModerationClient, TEST_SCENARIO, make_session
from tests.test_session_replay import turn_record


@pytest.fixture
def wired(tmp_path: Path):
    """라우트의 모듈 전역을 임시 DB 로 바꿔 끼우고, 끝나면 되돌린다.
    되돌리지 않으면 다음 테스트가 앞 테스트의 세션을 본다."""
    conn = repo.connect(tmp_path / "routes.db")
    saved = (dict(routes._live), dict(routes._drafted), dict(routes._draft_metrics))
    routes._live.clear()
    routes._drafted.clear()
    routes._draft_metrics.clear()
    routes.configure(
        conn=conn,
        client=FakeLlmClient({}, model="fake-model"),
        moderation=FakeModerationClient(outcomes=[CLEAN]),
    )
    yield conn
    routes._live.clear()
    routes._drafted.clear()
    routes._draft_metrics.clear()
    routes._live.update(saved[0])
    routes._drafted.update(saved[1])
    routes._draft_metrics.update(saved[2])
    conn.close()


def seed_db_only(conn, *, session_id: str = "S-DB", turns: int = 2):
    """DB 에만 있는 세션 — 서버가 재시작된 뒤의 상태와 같다."""
    repo.upsert_scenario(conn, TEST_SCENARIO)
    session = make_session(session_id=session_id)
    repo.create_session(conn, session)
    for index in range(1, turns + 1):
        session.turn_index = index
        repo.save_turn(conn, session, turn_record(session))
    return session


# --------------------------------------------------------------- 조회


def test_unknown_session_is_404(wired) -> None:
    with pytest.raises(HTTPException) as caught:
        routes.get_session("없는세션")

    assert caught.value.status_code == 404
    assert caught.value.detail == "SESSION_NOT_FOUND"


def test_live_session_reports_itself_as_live(wired) -> None:
    session = make_session(session_id="S-LIVE")
    repo.upsert_scenario(wired, TEST_SCENARIO)
    repo.create_session(wired, session)
    routes._live["S-LIVE"] = (session, TEST_SCENARIO)

    view = routes.get_session("S-LIVE")

    assert view["live"] is True
    assert view["session_id"] == "S-LIVE"


def test_db_only_session_is_readable(wired) -> None:
    """이게 이번 수정의 핵심이다. 예전에는 여기서 404 가 났다."""
    seed_db_only(wired, turns=2)

    view = routes.get_session("S-DB")

    assert view["live"] is False
    assert view["turn_index"] == 2
    assert view["scenario"]["title"] == TEST_SCENARIO.title
    assert len(view["turns"]) == 2


def test_db_only_session_carries_the_transcript(wired) -> None:
    seed_db_only(wired, turns=1)

    view = routes.get_session("S-DB")
    speakers = [u["speaker"] for u in view["transcript"]]

    assert speakers[:2] == [Speaker.MASCOT.value, Speaker.CHARACTER.value]
    assert Speaker.CHILD.value in speakers


def test_db_only_session_carries_the_metrics(wired) -> None:
    """요약과 힌트 집계가 나와야 시뮬레이션이 세션 결과를 적을 수 있다 —
    이걸 못 읽어서 스윕이 KeyError 로 죽었다."""
    seed_db_only(wired, turns=1)

    view = routes.get_session("S-DB")

    assert view["summary"]["total"] == 3
    assert "hinted_turns" in view["hints"]
    assert len(view["goal_statuses"]) == 3


def test_db_only_session_keeps_goal_progress(wired) -> None:
    repo.upsert_scenario(wired, TEST_SCENARIO)
    session = make_session(session_id="S-PROG")
    repo.create_session(wired, session)
    session.goals["MG-01"].status = GoalObservationStatus.ACHIEVED
    session.goals["MG-01"].support_level = SupportLevel.S1
    session.turn_index = 1
    repo.save_turn(wired, session, turn_record(session))

    view = routes.get_session("S-PROG")
    goals = {g["goal_id"]: g for g in view["goals"]}

    assert goals["MG-01"]["status"] == GoalObservationStatus.ACHIEVED.value
    assert goals["MG-01"]["support_level"] == SupportLevel.S1.value
    assert view["summary"]["achieved"] == 1


def test_scenario_without_goal_rows_is_404(wired) -> None:
    seed_db_only(wired, turns=1)
    wired.execute("DELETE FROM SUB_GOAL WHERE scenario_id=?", (TEST_SCENARIO.scenario_id,))
    wired.commit()

    with pytest.raises(HTTPException) as caught:
        routes.get_session("S-DB")

    assert caught.value.status_code == 404


# ------------------------------------------------------- 새 턴을 받는 조건


def test_require_returns_the_live_session(wired) -> None:
    session = make_session(session_id="S-LIVE")
    routes._live["S-LIVE"] = (session, TEST_SCENARIO)

    got, scenario = routes._require("S-LIVE")

    assert got is session
    assert scenario is TEST_SCENARIO


def test_require_rejects_a_db_only_session_as_not_live(wired) -> None:
    """진행 상태는 프로세스 메모리에만 있어서 되살릴 수 없다. 대사 원문은 남지만
    후보 선정에 쓰인 맥락과 초점 목표의 중간 상태까지 복원되는 건 아니다 —
    반쯤 복원된 맥락으로 아이에게 대사가 나가는 게 더 나쁘다.

    다만 404 가 아니라 409 여야 한다. 원인이 '없는 세션'이 아니라 '재시작'이고,
    하니스가 그 둘에 다르게 반응해야 한다."""
    seed_db_only(wired, turns=1)

    with pytest.raises(HTTPException) as caught:
        routes._require("S-DB")

    assert caught.value.status_code == 409
    assert caught.value.detail == "SESSION_NOT_LIVE"


def test_require_rejects_an_unknown_session_as_not_found(wired) -> None:
    with pytest.raises(HTTPException) as caught:
        routes._require("없는세션")

    assert caught.value.status_code == 404
    assert caught.value.detail == "SESSION_NOT_FOUND"


def test_ended_live_session_still_refuses_new_turns(wired) -> None:
    """끝난 세션에 턴을 넣으면 409 다 — 이건 원래 동작이고 그대로 남아야 한다.
    SESSION_NOT_LIVE 와 detail 로 구분된다."""
    session = make_session(session_id="S-END")
    session.phase = SessionPhase.ENDED
    routes._live["S-END"] = (session, TEST_SCENARIO)

    got, _ = routes._require("S-END")

    assert got.phase is SessionPhase.ENDED
