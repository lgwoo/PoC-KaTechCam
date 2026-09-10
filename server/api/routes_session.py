"""역할극 세션 API.

세션 실시간 상태는 프로세스 메모리에 둔다(PoC — 단일 프로세스 전제).
DB 는 관측·분석용 기록이지 진행 상태의 원본이 아니다.
"""

from __future__ import annotations

import sqlite3
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import repo
from llm.client import LlmClient, StageRecorder
from llm.errors import LlmCallError, LlmContractError
from pipeline import scenario_author
from safety.moderation import ModerationClient
from state.models import (
    GoalState,
    Scenario,
    SessionPhase,
    SessionState,
    Speaker,
    Utterance,
)

router = APIRouter(prefix="/api")

# 라우터가 만들어질 때는 아직 없다 — app/main.py 의 lifespan 이 채운다.
_live: dict[str, tuple[SessionState, Scenario]] = {}
# 시나리오는 전부 AI 가 짓는다. 원본은 DB 이고 여기는 그 캐시다.
_drafted: dict[str, Scenario] = {}
# 시나리오를 짓는 데 걸린 시간. 시나리오 내용이 아니라 만들어진 경위라서 분리한다.
_draft_metrics: dict[str, dict] = {}
_conn: sqlite3.Connection
_client: LlmClient
_moderation: ModerationClient


def configure(
    *, conn: sqlite3.Connection, client: LlmClient, moderation: ModerationClient
) -> None:
    global _conn, _client, _moderation
    _conn, _client, _moderation = conn, client, moderation
    _drafted.update(repo.load_generated_scenarios(conn))
    _draft_metrics.update(repo.load_generation_metrics(conn))


class CreateSessionRequest(BaseModel):
    scenario: str = Field(description="시나리오 키(SCN-GEN-…)")


class TurnRequest(BaseModel):
    utterance: str = Field(min_length=1, max_length=500)


class DraftScenarioRequest(BaseModel):
    theme: str = Field(min_length=2, max_length=200, description="어떤 상황을 만들지 한 줄")
    level: str = Field(default="L2", description="L1 | L2 | L3")


def _resolve_scenario(key: str) -> Scenario:
    if key not in _drafted:
        raise KeyError(f"없는 시나리오 '{key}' — 사용 가능: {sorted(_drafted)}")
    return _drafted[key]


def _scenario_view(scenario: Scenario) -> dict:
    return {
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "scenario_level": scenario.scenario_level,
        "scenario_facts": scenario.scenario_facts,
        "prohibited_inferences": scenario.prohibited_inferences,
        "mascot_intro": scenario.mascot_intro,
        "character_opening_line": scenario.character_opening_line,
        "character_name": scenario.persona.name,
        "maximum_turn_count": scenario.maximum_turn_count,
        "max_support_turns": scenario.maximum_support_turns_per_micro_goal,
        "micro_goals": [g.model_dump() for g in scenario.micro_goals],
        # 만드는 데 걸린 시간. 지표를 붙이기 전에 만든 시나리오는 null.
        "generation": _draft_metrics.get(scenario.scenario_id),
    }


def _models_view() -> dict:
    """콘솔이 단계별로 "실제 어느 모델을 불렀는지" 표시할 수 있게 설정값을 그대로 내려준다."""
    return {
        "agent": _client.model,
        "moderation": _moderation.model if _moderation.available else None,
    }


def _session_view(session: SessionState, scenario: Scenario) -> dict:
    return {
        "models": _models_view(),
        "session_id": session.session_id,
        "phase": session.phase.value,
        "turn_index": session.turn_index,
        "end_reason": session.end_reason.value if session.end_reason else None,
        "scenario": _scenario_view(scenario),
        "transcript": [u.model_dump() for u in session.transcript],
        "goals": [g.model_dump() for g in session.goals.values()],
        "summary": repo.goal_summary(_conn, session.session_id),
        "hints": repo.hint_summary(_conn, session.session_id),
    }


def _require(session_id: str) -> tuple[SessionState, Scenario]:
    """진행 중인 세션. 새 턴을 받을 수 있는 것만 여기서 돌려준다."""
    if session_id in _live:
        return _live[session_id]

    # 기록은 DB 에 남아 있는데 메모리에는 없는 상태 — 서버가 재시작된 경우다.
    # 이것을 SESSION_NOT_FOUND 로 뭉개면 "없는 세션"과 구별이 안 되고, 시뮬레이션
    # 하니스는 자기가 방금 만든 세션이 사라진 이유를 알 수 없다.
    if repo.load_session_state(_conn, session_id) is not None:
        raise HTTPException(status_code=409, detail="SESSION_NOT_LIVE")
    raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")


@router.get("/scenarios")
def list_scenarios() -> dict:
    scenarios = [
        {"key": key, "generated": True, **_scenario_view(scenario)}
        for key, scenario in sorted(_drafted.items(), key=lambda kv: kv[1].title)
    ]
    return {"models": _models_view(), "scenarios": scenarios}


@router.post("/scenarios/generate")
async def generate_scenario(request: DraftScenarioRequest) -> dict:
    recorder = StageRecorder()
    try:
        scenario = await scenario_author.draft_scenario(
            client=_client,
            moderation=_moderation,
            recorder=recorder,
            theme=request.theme.strip(),
            level=request.level,
        )
    except scenario_author.ScenarioDraftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (LlmCallError, LlmContractError) as exc:
        raise HTTPException(status_code=502, detail=f"시나리오 생성 실패: {exc}") from exc

    key = scenario.scenario_id
    timings = [t.model_dump() for t in recorder.timings]
    total_ms = recorder.total_ms()

    # 세션 시작을 기다리지 않고 바로 저장한다 — 만들어만 두고 재시작하면 사라지던 문제.
    repo.upsert_scenario(_conn, scenario)
    repo.save_generation_metrics(_conn, key, total_ms=total_ms, timings=timings)
    _drafted[key] = scenario
    _draft_metrics[key] = {"total_ms": total_ms, "timings": timings}

    return {"key": key, "generated": True, **_scenario_view(scenario)}


@router.delete("/scenarios/{key}")
def delete_scenario(key: str) -> dict:
    if key not in _drafted:
        raise HTTPException(status_code=404, detail=f"없는 시나리오입니다: {key}")

    removed = repo.delete_generated_scenario(_conn, key)
    _drafted.pop(key, None)
    _draft_metrics.pop(key, None)
    # 진행 중이던 세션도 같이 접는다. 시나리오가 없으면 턴을 더 돌릴 수 없다.
    for session_id, (_, scenario) in list(_live.items()):
        if scenario.scenario_id == key:
            _live.pop(session_id, None)

    return {"deleted": key, **removed}


@router.get("/sessions")
def list_sessions() -> dict:
    return {"sessions": repo.load_sessions(_conn)}


@router.post("/sessions")
def create_session(request: CreateSessionRequest) -> dict:
    try:
        scenario = _resolve_scenario(request.scenario)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = SessionState(
        session_id=uuid.uuid4().hex[:12],
        scenario_id=scenario.scenario_id,
        goals={g.id: GoalState(goal_id=g.id) for g in scenario.micro_goals},
    )
    # 마스코트가 장면을 소개하고, 캐릭터는 1인칭으로만 반응한다 — 화자가 다르다.
    session.transcript.append(
        Utterance(speaker=Speaker.MASCOT, text=scenario.mascot_intro, turn_index=0)
    )
    session.transcript.append(
        Utterance(speaker=Speaker.CHARACTER, text=scenario.character_opening_line, turn_index=0)
    )

    repo.upsert_scenario(_conn, scenario)
    repo.create_session(_conn, session)
    _live[session.session_id] = (session, scenario)
    return _session_view(session, scenario)


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    """세션 조회는 읽기다 — 메모리에 없으면 DB 에서 되살려 돌려준다.

    예전에는 메모리에만 있는 세션만 답하고 나머지는 404 였다. 그래서 서버가 한 번
    재시작되면 DB 에 턴이 다 남아 있는데도 그 세션을 읽을 수 없었고, 시뮬레이션은
    세션 요약을 통째로 잃었다. 기록을 정확히 남기는 게 목적인 실행에서 그건 못 쓴다.
    """
    live = session_id in _live
    if live:
        session, scenario = _live[session_id]
    else:
        restored = repo.load_session_state(_conn, session_id)
        if restored is None:
            raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
        session, scenario = restored

    view = _session_view(session, scenario)
    # 되살린 세션으로는 새 턴을 이어 갈 수 없다. 읽는 쪽이 그걸 알아야 한다.
    view["live"] = live
    view["turns"] = repo.load_session_turns(_conn, session_id)
    view["goal_statuses"] = repo.load_goal_statuses(_conn, session_id)
    return view


@router.post("/sessions/{session_id}/turns")
async def post_turn(session_id: str, request: TurnRequest) -> dict:
    session, scenario = _require(session_id)
    if session.phase is SessionPhase.ENDED:
        raise HTTPException(status_code=409, detail="INVALID_SESSION_TRANSITION")

    # 순환 import 를 피하려고 여기서 부른다(pipeline -> state -> ... 체인이 길다).
    from pipeline.turn import run_turn

    record = await run_turn(
        client=_client,
        moderation=_moderation,
        scenario=scenario,
        session=session,
        utterance=request.utterance.strip(),
    )
    turn_id = repo.save_turn(_conn, session, record)

    return {
        # turn_id 는 TurnRecord 에 없다(DB 가 붙이는 것이다). 시뮬레이션 하니스가
        # 자기 회차 기록을 이 턴에 걸려면 이 값이 응답에 있어야 한다.
        "turn_id": turn_id,
        "turn": record.model_dump(),
        "session": _session_view(session, scenario),
    }
