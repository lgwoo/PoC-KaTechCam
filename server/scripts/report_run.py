"""시뮬레이션 실행 리포트.

    python -m scripts.report_run --latest
    python -m scripts.report_run --run-id 20260910T053000Z --compare DRYRUN-2

DB 만 읽는다. LLM 호출도, HTTP 호출도 없다. scripts/report_session.py 와 같은 이유로
그렇게 한다 — 저장된 것만으로 리포트가 나오는지가 곧 저장이 제대로 됐는지의 증거다.
화면에만 보이고 DB 에 없는 값이 있으면 여기서 티가 난다.

섹션 형식은 report_session.py 를 따라간다. 두 리포트를 나란히 놓고 읽을 사람이
형식을 두 번 배우게 하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from app.config import load_settings
from db import repo

SERVER_ROOT = Path(__file__).resolve().parent.parent

STAGE_ORDER_HINT = "> 가로 오프셋이 겹치는 단계가 실제로 병렬로 돈 구간이다."


def _stdout_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


def _cell(text: object, limit: int = 90) -> str:
    """표 안에서 파이프와 줄바꿈은 표를 깨뜨린다."""
    value = str(text).replace("\n", " ").replace("|", "\\|")
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ------------------------------------------------------------- 조각 조회


def _candidate_stats(conn: sqlite3.Connection, run_id: str) -> tuple[list[dict], dict[str, int]]:
    rows = conn.execute(
        """
        SELECT tc.attempt_no, tc.outcome, tc.delivered, tc.judge_failure_codes
          FROM TURN_CANDIDATE tc
          JOIN TEST_TURN tt ON tt.turn_id = tc.turn_id
         WHERE tt.run_id = ?
        """,
        (run_id,),
    ).fetchall()

    by_outcome: dict[str, int] = {}
    codes: dict[str, int] = {}
    for row in rows:
        by_outcome[row["outcome"]] = by_outcome.get(row["outcome"], 0) + 1
        for code in json.loads(row["judge_failure_codes"] or "[]"):
            codes[code] = codes.get(code, 0) + 1

    outcomes = [
        {"outcome": name, "n": count}
        for name, count in sorted(by_outcome.items(), key=lambda kv: -kv[1])
    ]
    return outcomes, codes


def _incidents(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT si.source, si.incident_type, si.action_taken, si.detail,
               tt.suite, tt.scenario_id, tt.probe_id
          FROM SAFETY_INCIDENT si
          JOIN TEST_TURN tt ON tt.turn_id = si.turn_id
         WHERE tt.run_id = ?
         ORDER BY si.occurred_at
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _overhead(turns: list[dict]) -> list[int]:
    return sorted(t["client_ms"] - t["total_ms"] for t in turns)


# ------------------------------------------------------------------ 렌더


def render(conn: sqlite3.Connection, run_id: str, compare_id: str | None = None) -> str:
    run = conn.execute("SELECT * FROM TEST_RUN WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        raise SystemExit(f"없는 실행 '{run_id}'. --list 로 확인하라.")

    turns = repo.load_test_turns(conn, run_id)
    timing = repo.run_timing_stats(conn, run_id)
    outcomes, codes = _candidate_stats(conn, run_id)
    incidents = _incidents(conn, run_id)

    out: list[str] = []
    write = out.append

    write(f"# 느링고 역할극 PoC — 시뮬레이션 실행 리포트 `{run_id}`")
    write("")
    write(
        f"- 시작 {run['started_at']} · 종료 {run['ended_at'] or '(미완)'}"
    )
    write(
        f"- 에이전트 **{run['agent_model']}** · Moderation "
        f"**{run['moderation_model'] or '미설정'}**"
    )
    write(
        f"- 스위트 {', '.join(json.loads(run['suites']))}"
        f" · 시나리오 {run['scenario_count']}개"
        f" · 세션 {run['session_count']}개 · 턴 {run['turn_count']}개"
    )
    if run["note"]:
        write(f"- 메모: {run['note']}")
    if run["moderation_model"] is None:
        write("")
        write("> Moderation 키 없이 돌린 실행이다. 안전 판정 결과를 신뢰하면 안 된다.")

    # ---- 판정 집계
    verdicts: dict[str, dict[str, int]] = {}
    for turn in turns:
        bucket = verdicts.setdefault(turn["suite"], {"OK": 0, "MISMATCH": 0, "FAIL": 0})
        bucket[turn["verdict"]] += 1

    write("")
    write("## 판정 집계")
    write("")
    write("| 스위트 | 턴 | OK | MISMATCH | FAIL |")
    write("| --- | ---: | ---: | ---: | ---: |")
    for suite, bucket in sorted(verdicts.items()):
        total = sum(bucket.values())
        write(
            f"| {suite} | {total} | {bucket['OK']} | {bucket['MISMATCH']} | {bucket['FAIL']} |"
        )
    write("")
    write("MISMATCH 는 분류가 기대와 달랐다는 뜻이고, 구조가 깨진 것만 FAIL 이다 —")
    write("분류기 품질 자체가 관측 대상이라서 둘을 분리해 센다.")

    # ---- 단계별 지연
    write("")
    write("## 단계별 소요")
    write("")
    write("| 단계 | n | 최소 | p50 | p95 | 최대 | 실패 |")
    write("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in timing:
        write(
            f"| `{row['stage']}` | {row['n']} | {row['min_ms']} | {row['p50_ms']}"
            f" | {row['p95_ms']} | {row['max_ms']} | {row['failed'] or ''} |"
        )
    write("")
    write("백분위는 보간 없이 가장 가까운 순위를 쓴다 — 실측하지 않은 값을 리포트에 찍지 않는다.")
    write("")
    write(STAGE_ORDER_HINT)

    # ---- 단계별 토큰
    tokens = repo.run_token_stats(conn, run_id)
    measured_any = any(row["measured"] for row in tokens)
    write("")
    write("## 단계별 토큰")
    write("")
    if not measured_any:
        write("이 실행에는 토큰 계측이 없다 — 계측을 넣기 전에 쌓인 기록이다.")
    else:
        write("| 단계 | 호출 | 계측된 호출 | 읽은 토큰(평균) | 쓴 토큰(평균) | 합계 | 비중 | 잘림 |")
        write("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in tokens:
            prompt_avg = row["prompt_avg"] if row["prompt_avg"] is not None else "—"
            completion_avg = (
                row["completion_avg"] if row["completion_avg"] is not None else "—"
            )
            write(
                f"| `{row['stage']}` | {row['calls']} | {row['measured']}"
                f" | {prompt_avg} | {completion_avg} | {row['total_sum']}"
                f" | {row['share_pct']}% | {row['truncated'] or ''} |"
            )
        write("")
        write("시간표와 나란히 읽어야 처방이 갈린다 — 읽은 토큰이 커서 느린 단계는")
        write("프롬프트를 깎아야 하고, 쓴 토큰이 커서 느린 단계는 출력 형식을 줄여야 한다.")
        write("Moderation 은 usage 를 주지 않아 계측된 호출이 0 이다.")
        write("'잘림'은 finish_reason=length — max_tokens 에 걸려 답이 중간에 끊긴 호출이다.")

    # ---- 실행 조건
    snapshot_ids = repo.run_snapshot_ids(conn, run_id)
    write("")
    write("## 실행 조건")
    write("")
    if not snapshot_ids:
        write("이 실행의 세션에는 조건 스냅샷이 없다 — 스냅샷을 넣기 전에 쌓인 기록이라")
        write("어느 프롬프트·설정에서 잰 수치인지 되짚을 수 없다.")
    else:
        if len(snapshot_ids) > 1:
            write(f"> 조건이 **{len(snapshot_ids)}종 섞인 실행**이다. 아래 수치를 한 덩어리로")
            write("> 읽으면 안 된다 — 서로 다른 프롬프트·설정의 결과가 합쳐져 있다.")
            write("")
        for snapshot_id in snapshot_ids:
            snap = repo.load_config_snapshot(conn, snapshot_id)
            if not snap:
                continue
            dirty = " (커밋 안 된 수정 있음)" if snap["git_dirty"] else ""
            write(f"**`{snapshot_id}`**")
            write("")
            write(f"- 코드 `{snap['git_sha'] or '알 수 없음'}`{dirty}"
                  f" · 프롬프트 `{snap['prompts_sha']}`")
            write(f"- 모델 **{snap['agent_model']}** · Moderation "
                  f"**{snap['moderation_model'] or '미설정'}** · `{snap['base_url_host']}`")
            write(f"- max_tokens {snap['max_output_tokens']}"
                  f" · 타임아웃 {snap['llm_timeout_s']}초"
                  f" · SDK 재시도 {snap['sdk_max_retries']}회"
                  f" · 후보 최대 {snap['max_candidates']}회"
                  f" · 수리 재시도 {snap['repair_attempts']}회")
            write(f"- Python {snap['python_version']} · {snap['platform']}"
                  f" · {'Docker' if snap['in_docker'] else '로컬'}")
            write("")
        write("프롬프트 해시가 다르면 이전 회차와 나란히 놓을 수 없다 — 판정 기준이 바뀌면")
        write("통과율과 재생성률이 같이 움직이기 때문이다.")

    # ---- 서버 밖 오버헤드
    if turns:
        overhead = _overhead(turns)
        totals = sorted(t["total_ms"] for t in turns)
        write("")
        write("## 파이프라인 밖 오버헤드")
        write("")
        write("| 값 | p50 | p95 | 최대 |")
        write("| --- | ---: | ---: | ---: |")
        write(
            f"| 턴 전체(total_ms) | {repo._percentile(totals, 0.5)}"
            f" | {repo._percentile(totals, 0.95)} | {totals[-1]} |"
        )
        write(
            f"| 왕복−전체(ms) | {repo._percentile(overhead, 0.5)}"
            f" | {repo._percentile(overhead, 0.95)} | {overhead[-1]} |"
        )
        write("")
        write("`total_ms` 는 run_turn 안쪽만 잰다. 차이가 FastAPI 직렬화와 save_turn 의")
        write("DB 쓰기다 — 서버는 이 값을 스스로 잴 수 없어서 하니스가 잰다.")

    # ---- 세션별 결과
    write("")
    write("## 세션")
    sessions: dict[tuple[str, str, str], list[dict]] = {}
    for turn in turns:
        key = (turn["scenario_id"], turn["suite"], turn["session_id"])
        sessions.setdefault(key, []).append(turn)

    for (scenario_id, suite, session_id), rows in sorted(sessions.items()):
        last = rows[-1]
        goals = repo.goal_summary(conn, session_id)
        hints = repo.hint_summary(conn, session_id)
        write("")
        write(f"### `{session_id[:8]}` — {scenario_id} [{suite}]")
        write(
            f"- 턴 {len(rows)} · 종료 사유 **{last['end_reason'] or '-'}**"
            f" · 목표 달성 {goals['achieved']}/{goals['total']}"
            f" (스스로 {goals['achieved_without_answer_reveal']}, 포기 {goals['abandoned']})"
        )
        write(
            f"- 힌트 준 턴 {hints['hinted_turns']}/{hints['turns']}"
            f" (마스코트 {hints['from_mascot']}, 캐릭터 {hints['from_character']})"
            f" · 정답 공개 {hints['answer_revealed']}"
        )
        write("")
        write("| 턴 | 프로브 | 분류 | 마스코트 | 세기 | 후보 | 전체(ms) | 판정 |")
        write("| ---: | --- | --- | --- | --- | ---: | ---: | --- |")
        for turn in rows:
            marks = []
            if turn["fallback_used"]:
                marks.append("기본응답")
            if turn["degraded_stages"]:
                marks.append("degraded")
            if not turn["moderation_available"]:
                marks.append("Moderation없음")
            verdict = turn["verdict"] + (f" ({', '.join(marks)})" if marks else "")
            write(
                f"| {turn['turn_number']} | {turn['probe_id']} | {turn['category']}"
                f" | {turn['mascot_mode']} | {turn['support_level']}"
                f" | {turn['retry_count'] + 1} | {turn['total_ms']} | {verdict} |"
            )
        for turn in rows:
            for finding in turn["findings"]:
                write(f"  - 턴 {turn['turn_number']} {turn['probe_id']}: {_cell(finding)}")

    # ---- 어긋난 것 모아 보기
    flagged = [t for t in turns if t["verdict"] != "OK"]
    write("")
    write("## 어긋난 턴")
    if not flagged:
        write("")
        write("없다.")
    else:
        write("")
        write("| 판정 | 시나리오 | 스위트 | 프로브 | 내용 |")
        write("| --- | --- | --- | --- | --- |")
        for turn in flagged:
            write(
                f"| {turn['verdict']} | {turn['scenario_id']} | {turn['suite']}"
                f" | {turn['probe_id']} | {_cell(' / '.join(turn['findings']))} |"
            )

    # ---- 후보 루프
    write("")
    write("## 후보 생성 루프")
    write("")
    write("| 결과 | 건수 |")
    write("| --- | ---: |")
    for row in outcomes:
        write(f"| `{row['outcome']}` | {row['n']} |")
    fallback_turns = [t for t in turns if t["fallback_used"]]
    write("")
    write(
        f"3회를 다 쓰고 사전 승인된 기본 응답으로 내려간 턴: **{len(fallback_turns)}개**"
        + (f" / {len(turns)}" if turns else "")
    )
    if fallback_turns:
        for turn in fallback_turns:
            write(
                f"- {turn['scenario_id']} [{turn['suite']}] 턴 {turn['turn_number']}"
                f" ({turn['probe_id']})"
            )

    if codes:
        write("")
        write("### 판정 실패 코드")
        write("")
        write("| 코드 | 건수 |")
        write("| --- | ---: |")
        for code, count in sorted(codes.items(), key=lambda kv: (-kv[1], kv[0])):
            write(f"| `{_cell(code, 60)}` | {count} |")
        write("")
        write("JudgeContract.failure_codes 는 자유 문자열이다. 표기가 영문 대문자·소문자·")
        write("한국어로 섞여 나오면 집계가 안 되므로 여기서 그대로 드러낸다.")

    # ---- 호출이 죽은 턴
    #
    # 이 구간이 있으면 그 턴들의 분류 결과와 소요 시간은 파이프라인 성능이 아니라
    # 네트워크 상태를 재고 있다. 위의 p50/p95 를 그대로 읽으면 안 된다는 뜻이라
    # 리포트 안에서 먼저 밝힌다.
    broken = conn.execute(
        """
        SELECT tt.scenario_id, tt.suite, tt.probe_id, ct.turn_number, ct.delivered_at,
               SUM(st.ok = 0) AS failed, COUNT(st.timing_id) AS spans
          FROM TEST_TURN tt
          JOIN CONVERSATION_TURN ct ON ct.turn_id = tt.turn_id
          JOIN STAGE_TIMING st ON st.turn_id = tt.turn_id
         WHERE tt.run_id = ?
         GROUP BY tt.turn_id
        HAVING failed > 0
         ORDER BY ct.delivered_at
        """,
        (run_id,),
    ).fetchall()

    write("")
    write("## 호출이 죽은 턴")
    if not broken:
        write("")
        write("없다. 모든 단계가 응답을 받았다.")
    else:
        first, last = broken[0]["delivered_at"], broken[-1]["delivered_at"]
        write("")
        write(
            f"**{len(broken)}개 턴** / {len(turns)} 에서 LLM·Moderation 호출이 실패했다"
            f" ({first[11:19]} ~ {last[11:19]})."
        )
        write("")
        write("한 구간에 몰려 있으면 파이프라인 결함이 아니라 네트워크 장애다. 그 턴들은")
        write("사전 승인된 기본 응답으로 내려가면서 대화는 이어졌지만, 분류 결과와")
        write("소요 시간은 파이프라인이 아니라 장애를 재고 있다 — 위의 지연 통계에서")
        write("그만큼을 걸러 읽어야 한다.")
        write("")
        write("| 시각 | 시나리오 | 스위트 | 프로브 | 실패/전체 단계 |")
        write("| --- | --- | --- | --- | ---: |")
        for row in broken:
            write(
                f"| {row['delivered_at'][11:19]} | {row['scenario_id']} | {row['suite']}"
                f" | {row['probe_id']} | {row['failed']}/{row['spans']} |"
            )

    # ---- degraded
    degraded = [t for t in turns if t["degraded_stages"] or t["degraded"]]
    write("")
    write("## 판정이 내려앉은 턴")
    if not degraded:
        write("")
        write("없다.")
    else:
        write("")
        write("| 시나리오 | 스위트 | 턴 | 내려앉은 단계 | 사유 |")
        write("| --- | --- | ---: | --- | --- |")
        for turn in degraded:
            write(
                f"| {turn['scenario_id']} | {turn['suite']} | {turn['turn_number']}"
                f" | {', '.join(turn['degraded_stages']) or 'intake'}"
                f" | {_cell(turn['override_source'] or '-')} |"
            )

    # ---- 안전 사건
    write("")
    write("## 안전 사건")
    if not incidents:
        write("")
        write("없다.")
    else:
        write("")
        write("| 출처 | 유형 | 조치 | 스위트 | 프로브 | 내용 |")
        write("| --- | --- | --- | --- | --- | --- |")
        for item in incidents:
            write(
                f"| {item['source']} | {item['incident_type']} | {item['action_taken']}"
                f" | {item['suite']} | {item['probe_id']}"
                f" | {_cell(item['detail'] or '-', 60)} |"
            )

    # ---- 회차 비교
    if compare_id:
        write("")
        write(f"## 이전 실행과 비교 — `{compare_id}`")
        before = {row["stage"]: row for row in repo.run_timing_stats(conn, compare_id)}
        if not before:
            write("")
            write(f"`{compare_id}` 에 단계 기록이 없다.")
        else:
            write("")
            write("| 단계 | 이전 p50 | 이번 p50 | 차이 |")
            write("| --- | ---: | ---: | ---: |")
            for row in timing:
                old = before.get(row["stage"])
                if old is None:
                    write(f"| `{row['stage']}` | - | {row['p50_ms']} | 신규 |")
                    continue
                delta = row["p50_ms"] - old["p50_ms"]
                write(
                    f"| `{row['stage']}` | {old['p50_ms']} | {row['p50_ms']}"
                    f" | {delta:+d} |"
                )

    return "\n".join(out) + "\n"


def main() -> int:
    _stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="scripts.report_run",
        description="시뮬레이션 실행을 마크다운 리포트로 만든다. DB 만 읽는다.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-id", help="리포트를 만들 실행 id")
    group.add_argument("--latest", action="store_true", help="가장 최근 실행")
    group.add_argument("--list", action="store_true", help="실행 목록만 보여준다")
    parser.add_argument("--compare", default=None, help="p50 을 비교할 이전 실행 id")
    parser.add_argument("--out", default=None,
                        help="출력 경로 (기본 runs/<run_id>/summary.md)")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.db_path.exists():
        raise SystemExit(f"DB 가 없다: {settings.db_path}")
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row

    runs = repo.load_test_runs(conn)
    if args.list or (not args.run_id and not args.latest):
        if not runs:
            print("기록된 실행이 없다. python -m scripts.simulate 로 하나 돌려라.")
            return 0
        print(f"{'run_id':<24} {'턴':>4} {'세션':>4}  시작")
        for run in runs:
            print(
                f"{run['run_id']:<24} {run['turn_count']:>4} {run['session_count']:>4}"
                f"  {run['started_at']}"
            )
        return 0

    run_id = args.run_id or runs[0]["run_id"]
    text = render(conn, run_id, args.compare)

    target = Path(args.out) if args.out else SERVER_ROOT / "runs" / run_id / "summary.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"{target} ({len(text):,} chars)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
