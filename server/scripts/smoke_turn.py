"""파이프라인 스모크 테스트 — 실제 LLM 으로 몇 턴 돌려 본다.

DB·API·UI 를 붙이기 전에 턴 루프 자체가 도는지 확인하는 용도.
실행: python scripts/smoke_turn.py [fall|jealousy]
"""

from __future__ import annotations

import asyncio
import sys

from app.config import load_settings
from db import repo
from llm.client import LlmClient
from pipeline.turn import run_turn
from safety.moderation import ModerationClient
from state.models import GoalState, SessionPhase, SessionState, Speaker, Utterance

SCRIPTED = [
    "친구가 울고 있어",
    "넘어져서 아팠나 봐",
    "넘어져서 아프고 속상했을 것 같아",
]


def new_session(scenario) -> SessionState:
    session = SessionState(
        session_id="SMOKE-1",
        scenario_id=scenario.scenario_id,
        goals={g.id: GoalState(goal_id=g.id) for g in scenario.micro_goals},
    )
    session.transcript.append(
        Utterance(speaker=Speaker.MASCOT, text=scenario.mascot_intro, turn_index=0)
    )
    session.transcript.append(
        Utterance(speaker=Speaker.CHARACTER, text=scenario.character_opening_line, turn_index=0)
    )
    return session


def show(record) -> None:
    print(f"\n{'=' * 72}")
    print(f"턴 {record.turn_index}  |  아이> {record.child_input}")
    if record.child_input_stored != record.child_input:
        print(f"   [PII 마스킹] -> {record.child_input_stored}")
    i = record.intake
    print(
        f"   [인테이크] {i.category} · misunderstanding={i.misunderstanding} "
        f"· character_bind={i.character_bind} · progress={i.goal_progress}"
    )
    print(f"      사유: {i.reason}")
    m = record.moderation
    print(
        f"   [Moderation] available={m.available} flagged={m.flagged} "
        f"severity={m.severity} {m.categories or ''}"
    )
    print(f"   [마스코트] {record.mascot_mode}  초점={record.focus_goal_id} ({record.support_level})")
    if record.mascot_line:
        print(f"      마스코트> {record.mascot_line}")
    for c in record.candidates:
        flag = " <= 전달" if c.delivered else ""
        violations = f" 심판={[v.value for v in c.referee_violations]}" if c.referee_violations else ""
        reason = f" · {c.judge.reason}" if c.judge else ""
        print(f"   [후보 {c.attempt_no}] {c.outcome}{violations}{reason}{flag}")
        print(f"      {c.text}")
    print(f"   친구> {record.delivered_text}{'  [기본 응답]' if record.fallback_used else ''}")
    if record.closing_line:
        print(f"   마스코트> {record.closing_line}   [마무리]")
    scan = record.goal_scan
    print(f"   [목표 스캔] 대상={scan.scanned_goal_ids} 인정={scan.accepted}")
    for rejected in scan.rejected:
        print(f"      기각 {rejected.goal_id}: {rejected.reason}")
    print("   [단계별 소요]")
    for t in sorted(record.timings, key=lambda x: x.start_offset_ms):
        attempt = f"#{t.attempt_no}" if t.attempt_no else ""
        mark = "" if t.ok else "  (실패)"
        print(f"      +{t.start_offset_ms:>5}ms  {t.duration_ms:>5}ms  {t.stage}{attempt}{mark}")
    print(f"   [전체] {record.total_ms}ms  phase={record.phase_after} end={record.end_reason}")
    if record.degraded_stages:
        print(f"   [degraded] {record.degraded_stages}")


async def main() -> int:
    settings = load_settings()
    conn = repo.connect(settings.db_path)
    scenarios = repo.load_generated_scenarios(conn)
    if not scenarios:
        print("저장된 시나리오가 없다. 콘솔에서 먼저 하나 만들어라.")
        return 1
    key = sys.argv[1] if len(sys.argv) > 1 else sorted(scenarios)[0]
    if key not in scenarios:
        print(f"없는 시나리오 '{key}' — 사용 가능: {sorted(scenarios)}")
        return 1
    scenario = scenarios[key]

    client = LlmClient(
        api_key=settings.elice_api_key,
        base_url=settings.elice_base_url,
        model=settings.elice_model,
        max_tokens=settings.max_output_tokens,
    )
    moderation = ModerationClient(
        api_key=settings.moderation_api_key, model=settings.moderation_model
    )

    session = new_session(scenario)
    print(f"시나리오: {scenario.title} [{scenario.scenario_level}]")
    print(f"마스코트> {scenario.mascot_intro}")
    print(f"친구> {scenario.character_opening_line}")

    for utterance in SCRIPTED:
        record = await run_turn(
            client=client,
            moderation=moderation,
            scenario=scenario,
            session=session,
            utterance=utterance,
        )
        show(record)
        if session.phase is SessionPhase.ENDED:
            break

    print(f"\n{'=' * 72}\n최종 목표 상태:")
    for goal_id, state in session.goals.items():
        support = " (정답 제공 후 달성)" if state.achieved_with_support else ""
        print(f"  {goal_id}: {state.status} @{state.support_level}{support}")
        if state.evidence_text:
            print(f"     근거: {state.evidence_text}")
            print(f"     이유: {state.why_this_satisfies}")
    print(f"세션 종료 사유: {session.end_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
