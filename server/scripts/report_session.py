"""저장된 세션을 사람이 읽을 수 있는 마크다운 리포트로 뽑는다.

DB 에 남긴 것만으로 리포트가 나오는지 확인하는 용도이기도 하다 —
콘솔 화면에만 보이고 저장은 안 된 값이 있으면 여기서 티가 난다.

실행: python -m scripts.report_session [출력경로]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from app.config import load_settings

STAGE_ORDER_HINT = "가로 오프셋이 겹치는 단계가 실제로 병렬로 돈 구간이다."


def _rows(conn: sqlite3.Connection, sql: str, *args) -> list[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def render(conn: sqlite3.Connection) -> str:
    out: list[str] = ["# 느링고 역할극 PoC — 세션 리포트", ""]

    sessions = _rows(conn, "SELECT * FROM ROLEPLAY_SESSION ORDER BY started_at")
    played = [s for s in sessions if s["turn_index"] > 0]
    out.append(f"세션 {len(sessions)}개 (턴이 있는 세션 {len(played)}개)")
    out.append("")

    for session in played:
        sid = session["session_id"]
        scenario = conn.execute(
            "SELECT * FROM SCENARIO WHERE scenario_id=?", (session["scenario_id"],)
        ).fetchone()
        out.append(f"## 세션 `{sid[:8]}` — {scenario['title']} [{scenario['scenario_level']}]")
        out.append(
            f"- 상태: **{session['phase']}** · 종료 사유: **{session['end_reason'] or '-'}** "
            f"· 턴 {session['turn_index']}/{scenario['maximum_turn_count']}"
        )

        goals = _rows(
            conn, "SELECT * FROM SESSION_GOAL_STATUS WHERE session_id=? ORDER BY subgoal_id", sid
        )
        achieved = [g for g in goals if g["status"] == "ACHIEVED"]
        unaided = [g for g in achieved if not g["achieved_with_support"]]
        out.append(
            f"- 목표: 달성 {len(achieved)}/{len(goals)} · "
            f"스스로 도달 {len(unaided)} · "
            f"유도 소진 {sum(1 for g in goals if g['status'] == 'ABANDONED')}"
        )
        out.append("")
        out.append("| 목표 | 상태 | 도달 수준 | 근거 |")
        out.append("| --- | --- | --- | --- |")
        for goal in goals:
            evidence = (goal["evidence_text"] or "-").replace("|", "\\|")
            support = " *(정답 제공 후)*" if goal["achieved_with_support"] else ""
            out.append(
                f"| {goal['subgoal_id']} | {goal['status']}{support} | "
                f"{goal['reached_level']} | {evidence} |"
            )
        out.append("")

        for turn in _rows(
            conn,
            "SELECT * FROM CONVERSATION_TURN WHERE session_id=? ORDER BY turn_number",
            sid,
        ):
            tid = turn["turn_id"]
            intake = conn.execute(
                "SELECT * FROM INTAKE_RESULT WHERE turn_id=?", (tid,)
            ).fetchone()

            out.append(f"### 턴 {turn['turn_number']} — {turn['total_ms']}ms")
            out.append(f"- 아이: `{turn['raw_utterance']}`")
            if turn["raw_utterance"] != turn["standardized_utterance"]:
                out.append(f"  - PII 마스킹 → `{turn['standardized_utterance']}`")
            out.append(
                f"- 인테이크: **{intake['category']}** · 진행 {intake['goal_progress']}"
                + (" · 상황 오인" if intake["misunderstanding"] else "")
                + (" · 캐릭터 곤란" if intake["character_bind"] else "")
                + (" · **degraded**" if intake["degraded"] else "")
            )
            out.append(f"  - 사유: {intake['reason']}")
            if intake["override_source"]:
                out.append(f"  - **오버라이드**: {intake['override_source']}")

            categories = json.loads(intake["moderation_categories"])
            scores = json.loads(intake["moderation_scores"])
            detail = ", ".join(f"{c} {scores.get(c, 0):.2f}" for c in categories) or "-"
            out.append(
                f"- Moderation: flagged={bool(intake['moderation_flagged'])} · "
                f"severity {intake['moderation_severity']} · {detail}"
            )
            out.append(
                f"- 마스코트: **{turn['mascot_mode']}** · 초점 {turn['focus_subgoal_id'] or '-'} "
                f"({turn['support_level']})"
            )
            if turn["mascot_line"]:
                out.append(f"  - 마스코트: “{turn['mascot_line']}”")

            candidates = _rows(
                conn,
                "SELECT * FROM TURN_CANDIDATE WHERE turn_id=? ORDER BY attempt_no",
                tid,
            )
            if candidates:
                out.append(f"- 후보 {len(candidates)}개")
                for candidate in candidates:
                    violations = json.loads(candidate["referee_violations"])
                    marks = [candidate["outcome"]]
                    if candidate["delivered"]:
                        marks.append("전달")
                    if violations:
                        marks.append(f"심판 {'/'.join(violations)}")
                    out.append(
                        f"  {candidate['attempt_no']}. `{'·'.join(marks)}` "
                        f"{candidate['text'][:110]}"
                    )
                    if candidate["judge_reason"]:
                        codes = json.loads(candidate["judge_failure_codes"] or "[]")
                        suffix = f" · 실패코드 {', '.join(codes)}" if codes else ""
                        out.append(f"     - 판단: {candidate['judge_reason'][:160]}{suffix}")
            else:
                out.append("- 후보 없음 (안전 중단으로 생성하지 않음)")

            if turn["agent_response_text"]:
                fallback = " **[사전 승인 기본 응답]**" if turn["fallback_used"] else ""
                out.append(f"- 캐릭터: “{turn['agent_response_text']}”{fallback}")
            if turn["closing_line"]:
                out.append(f"- 마무리: “{turn['closing_line']}”")

            rejections = _rows(
                conn, "SELECT * FROM GOAL_SCAN_REJECTION WHERE turn_id=?", tid
            )
            for rejection in rejections:
                out.append(
                    f"- 목표 스캔 기각 {rejection['subgoal_id']}: "
                    f"“{rejection['evidence']}” — {rejection['reason']}"
                )

            timings = _rows(
                conn,
                "SELECT * FROM STAGE_TIMING WHERE turn_id=? ORDER BY start_offset_ms",
                tid,
            )
            out.append("")
            out.append("| 단계 | 시작(+ms) | 소요(ms) |")
            out.append("| --- | ---: | ---: |")
            for timing in timings:
                attempt = f"#{timing['attempt_no']}" if timing["attempt_no"] else ""
                failed = "" if timing["ok"] else " (실패)"
                out.append(
                    f"| {timing['stage']}{attempt}{failed} | "
                    f"{timing['start_offset_ms']} | {timing['duration_ms']} |"
                )
            out.append("")
            out.append(f"> {STAGE_ORDER_HINT}")
            out.append("")

        incidents = _rows(
            conn, "SELECT * FROM SAFETY_INCIDENT WHERE session_id=? ORDER BY occurred_at", sid
        )
        if incidents:
            out.append("#### 안전 사건")
            out.append("")
            out.append("| 출처 | 유형 | 조치 | 내용 |")
            out.append("| --- | --- | --- | --- |")
            for incident in incidents:
                out.append(
                    f"| {incident['source']} | {incident['incident_type']} | "
                    f"{incident['action_taken']} | {(incident['detail'] or '')[:70]} |"
                )
            out.append("")

    return "\n".join(out)


def main() -> int:
    settings = load_settings()
    if not settings.db_path.exists():
        raise SystemExit(f"DB 가 없다: {settings.db_path}")
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    text = render(conn)
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("session_report.md")
    target.write_text(text, encoding="utf-8")
    print(f"{target} ({len(text):,} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
