"""파이프라인 전수 시뮬레이션 — 떠 있는 서버를 HTTP 로 두들긴다.

    python -m scripts.simulate --scenarios all --suites play,safety,ladder

왜 HTTP 인가: 턴을 DB 에 남기는 건 repo.save_turn 이고, 그걸 부르는 곳은
api/routes_session.py 의 라우트뿐이다. 파이프라인을 직접 호출하면(scripts/smoke_turn.py
가 그렇게 한다) 화면에만 찍히고 STAGE_TIMING 에 아무것도 안 남는다. 실측을 남기는 게
목적이므로 반드시 라우트를 거친다.

세션 진행 상태는 서버 프로세스 메모리에만 있다(routes_session._live). 그래서 이
스크립트는 자기가 만든 세션만 다룬다 — 예전 세션은 GET 으로도 안 잡힌다.

판정은 대사 내용을 보지 않는다. LLM 출력은 회차마다 달라서 텍스트를 걸면 리포트가
전부 빨개진다. 구조가 깨진 것만 FAIL 이고, 분류가 기대와 다른 것은 MISMATCH 로
적어 두고 리포트에서 모아 본다 — 분류기 품질 자체가 관측 대상이다.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.config import load_settings
from db import repo
from scripts.utterances import (
    SUITE_NAMES,
    Probe,
    expected_end_reasons,
    has_script,
    probes_for,
)

SERVER_ROOT = Path(__file__).resolve().parent.parent

# 이 스크립트는 한 턴에 최대 15초쯤 기다린다. 판정 LLM 이 재생성까지 돌면 그보다 길다.
TURN_TIMEOUT_S = 180.0


def _stdout_utf8() -> None:
    """Windows 콘솔은 기본이 cp949 라서 '—' 하나에 UnicodeEncodeError 로 죽는다.
    리포트가 아니라 진행 표시 때문에 실행이 날아가면 안 된다."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


# ---------------------------------------------------------------- 판정


def _stage_names(timings: list[dict]) -> set[str]:
    """'.repair' 는 같은 단계의 수리 재시도다. 존재 확인에서는 본체와 같이 센다."""
    return {t["stage"].removesuffix(".repair") for t in timings}


def judge_turn(
    *,
    probe: Probe,
    turn: dict,
    session: dict,
    timing_rows: int,
) -> tuple[str, list[str]]:
    """(verdict, findings) 를 돌려준다. verdict 는 OK / MISMATCH / FAIL."""
    fails: list[str] = []
    mismatches: list[str] = []

    timings = turn["timings"]
    stages = _stage_names(timings)
    halted = turn["end_reason"] == "SAFETY_HALT"

    # 1. 저장 완전성 — 응답에 있는 단계가 DB 에 그대로 들어갔는가.
    #    이게 어긋나면 콘솔에는 보이는데 저장은 안 된 값이 있다는 뜻이다.
    if timing_rows != len(timings):
        fails.append(f"STAGE_TIMING 행 {timing_rows}개 ≠ 응답 단계 {len(timings)}개")
    if turn["total_ms"] <= 0:
        fails.append(f"total_ms 가 {turn['total_ms']}")

    # 2. 어느 경로든 입력 판정 두 단계는 반드시 돈다.
    for required in ("intake", "moderation.input"):
        if required not in stages:
            fails.append(f"'{required}' 단계가 없다")

    # 3. 병렬성 — 겹쳐 도는 단계가 있으면 소요 합계가 전체 시간을 넘는다.
    overlap = sum(t["duration_ms"] for t in timings) - turn["total_ms"]
    if overlap <= 0:
        mismatches.append(f"병렬 구간이 안 보인다(합계−전체 = {overlap}ms)")

    if halted:
        # 4. 중단 턴 — 생성까지 가지 않고, 아이 발화가 기록에 남지 않는다.
        if turn["phase_after"] != "ENDED":
            fails.append(f"중단인데 phase_after 가 {turn['phase_after']}")
        if turn["candidates"]:
            fails.append(f"중단인데 후보가 {len(turn['candidates'])}개 생성됐다")
        if any(u["speaker"] == "CHILD" and u["text"] == probe.text
               for u in session["transcript"]):
            fails.append("중단된 아이 발화가 transcript 에 남았다")
        if not turn["mascot_line"]:
            fails.append("중단인데 마스코트 대사가 없다")
    else:
        if "generate" not in stages:
            fails.append("'generate' 단계가 없다")
        if not turn["delivered_text"]:
            fails.append("전달된 캐릭터 대사가 없다")

    # 5. 목표 스캔은 열린 목표가 있을 때만 호출된다(없으면 LLM 을 안 부른다).
    if turn["goal_scan"]["scanned_goal_ids"] and "goal_scan" not in stages:
        fails.append("열린 목표가 있는데 'goal_scan' 단계가 없다")

    # 6. 여기부터는 기대와의 대조 — 어긋나도 MISMATCH 다.
    if probe.category and turn["intake"]["category"] != probe.category:
        mismatches.append(
            f"분류 기대 {probe.category} → 실제 {turn['intake']['category']}"
        )
    if probe.mascot_mode and turn["mascot_mode"] != probe.mascot_mode:
        mismatches.append(
            f"마스코트 기대 {probe.mascot_mode} → 실제 {turn['mascot_mode']}"
        )
    if probe.support_level and turn["support_level"] != probe.support_level:
        mismatches.append(
            f"도움 세기 기대 {probe.support_level} → 실제 {turn['support_level']}"
        )
    if probe.halt and not halted:
        mismatches.append(f"중단 기대 → 실제 종료 사유 {turn['end_reason'] or '없음'}")

    if probe.masked and turn["child_input_stored"] == turn["child_input"]:
        # Moderation 이 HIGH/CRITICAL 로 격상하면 intake 가 마스크를 버린다.
        # 알려진 엣지라서 실패로 세지 않고 그렇게 적어 둔다.
        if turn["intake"]["override_source"]:
            mismatches.append("마스킹 유실 — Moderation 격상으로 마스크가 버려졌다(알려진 엣지)")
        else:
            fails.append("개인정보인데 저장 발화가 원문과 같다")

    if fails:
        return "FAIL", fails + mismatches
    if mismatches:
        return "MISMATCH", mismatches
    return "OK", []


# ---------------------------------------------------------------- 실행


class Sweep:
    def __init__(self, *, client: httpx.Client, conn: sqlite3.Connection,
                 run_id: str, run_dir: Path) -> None:
        self.client = client
        self.conn = conn
        self.run_id = run_id
        self.run_dir = run_dir
        self.jsonl = (run_dir / "turns.jsonl").open("w", encoding="utf-8")
        self.turn_count = 0
        self.session_count = 0
        self.verdicts: dict[str, int] = {"OK": 0, "MISMATCH": 0, "FAIL": 0}
        self.session_notes: list[dict] = []

    def close(self) -> None:
        self.jsonl.close()

    def _timing_rows(self, turn_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM STAGE_TIMING WHERE turn_id=?", (turn_id,)
        ).fetchone()
        return int(row[0])

    def run_session(self, *, scenario_id: str, title: str, suite: str) -> None:
        probes = probes_for(suite, scenario_id)
        created = self.client.post("/api/sessions", json={"scenario": scenario_id})
        if created.status_code != 200:
            print(f"  !! 세션 생성 실패 {created.status_code} {created.text[:200]}")
            return
        session = created.json()
        session_id = session["session_id"]
        self.session_count += 1
        print(f"  [{suite}] 세션 {session_id} — {title}")

        end_reason = ""
        lost = False
        for probe in probes:
            started = time.monotonic()
            response = self.client.post(
                f"/api/sessions/{session_id}/turns", json={"utterance": probe.text}
            )
            client_ms = int((time.monotonic() - started) * 1000)

            if response.status_code == 409:
                detail = response.json().get("detail", "")
                if detail == "SESSION_NOT_LIVE":
                    # 기록은 DB 에 남았지만 진행 상태가 사라졌다 — 서버가 재시작된 것이다.
                    # 진행 상태는 프로세스 메모리에만 있어서 되살릴 수 없다.
                    print(
                        f"    {probe.id}: !! 서버가 재시작돼 진행 상태가 사라졌다."
                        " --reload 없이 띄우고 스윕 중에는 server/ 의 .py 를 고치지 마라."
                    )
                    lost = True
                else:
                    print(f"    {probe.id}: 세션이 이미 끝나 있어 남은 발화를 보내지 않는다")
                break
            if response.status_code != 200:
                print(f"    {probe.id}: !! {response.status_code} {response.text[:200]}")
                break

            payload = response.json()
            turn_id = payload["turn_id"]
            turn = payload["turn"]
            session = payload["session"]

            verdict, findings = judge_turn(
                probe=probe,
                turn=turn,
                session=session,
                timing_rows=self._timing_rows(turn_id),
            )
            self.verdicts[verdict] += 1
            self.turn_count += 1

            self.jsonl.write(json.dumps({
                "run_id": self.run_id,
                "suite": suite,
                "scenario_id": scenario_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "probe_id": probe.id,
                "probe_note": probe.note,
                "client_ms": client_ms,
                "verdict": verdict,
                "findings": findings,
                "expectation": probe.expectation(),
                "turn": turn,
            }, ensure_ascii=False) + "\n")

            repo.record_test_turn(
                self.conn,
                run_id=self.run_id,
                turn_id=turn_id,
                suite=suite,
                scenario_id=scenario_id,
                session_id=session_id,
                probe_id=probe.id,
                expectation=probe.expectation(),
                verdict=verdict,
                findings=findings,
                client_ms=client_ms,
            )

            mark = {"OK": "ok", "MISMATCH": "~~", "FAIL": "XX"}[verdict]
            overhead = client_ms - turn["total_ms"]
            print(
                f"    {mark} {probe.id:<16} {turn['intake']['category']:<12}"
                f" {turn['mascot_mode']:<9} {turn['support_level']}"
                f" {turn['total_ms']:>6}ms (+{overhead}ms 왕복)"
                + (f"  후보 {len(turn['candidates'])}" if len(turn["candidates"]) > 1 else "")
                + ("  기본응답" if turn["fallback_used"] else "")
            )
            for finding in findings:
                print(f"       - {finding}")

            end_reason = turn["end_reason"] or ""
            if turn["phase_after"] == "ENDED":
                break

        expected = expected_end_reasons(suite)
        matched = (not expected) or (end_reason in expected)

        # 세션이 사라졌으면 요약도 못 가져온다. 그 자리에서 KeyError 로 죽으면
        # 이미 저장된 앞부분 턴들의 집계까지 날아간다.
        detail = self.client.get(f"/api/sessions/{session_id}")
        if detail.status_code != 200:
            print(f"    세션 요약을 못 읽었다 ({detail.status_code}) — 집계에서 제외한다")
            self.session_notes.append({
                "suite": suite,
                "scenario_id": scenario_id,
                "session_id": session_id,
                "end_reason": end_reason,
                "expected_end_reasons": list(expected),
                "end_reason_ok": False,
                "lost": True,
            })
            return
        summary = detail.json()
        goals = summary["summary"]
        self.session_notes.append({
            "suite": suite,
            "scenario_id": scenario_id,
            "session_id": session_id,
            "end_reason": end_reason,
            "expected_end_reasons": list(expected),
            "end_reason_ok": matched,
            "goals": goals,
            "hints": summary["hints"],
            "lost": lost,
        })
        print(
            f"    종료 사유 {end_reason or '없음'}"
            + ("" if matched else f" (기대 {expected})")
            + f" · 목표 {goals['achieved']}/{goals['total']}"
            f" (스스로 {goals['achieved_without_answer_reveal']}, 포기 {goals['abandoned']})"
        )


def write_timings_csv(conn: sqlite3.Connection, run_id: str, path: Path) -> int:
    rows = conn.execute(
        """
        SELECT tt.scenario_id, tt.suite, tt.session_id, tt.probe_id,
               ct.turn_number, ct.total_ms, tt.client_ms,
               st.stage, st.attempt_no, st.start_offset_ms, st.duration_ms, st.ok
          FROM TEST_TURN tt
          JOIN CONVERSATION_TURN ct ON ct.turn_id = tt.turn_id
          JOIN STAGE_TIMING st ON st.turn_id = tt.turn_id
         WHERE tt.run_id = ?
         ORDER BY tt.scenario_id, tt.suite, ct.turn_number, st.start_offset_ms
        """,
        (run_id,),
    ).fetchall()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "run_id", "scenario_id", "suite", "session_id", "probe_id", "turn_number",
            "turn_total_ms", "client_ms", "stage", "attempt_no",
            "start_offset_ms", "duration_ms", "ok",
        ])
        for row in rows:
            writer.writerow([run_id, *tuple(row)])
    return len(rows)


def main() -> int:
    _stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="scripts.simulate",
        description="저장된 시나리오를 전수로 돌려 파이프라인 분기와 실측 시간을 남긴다.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000",
                        help="떠 있는 서버 주소 (기본 http://127.0.0.1:8000)")
    parser.add_argument("--scenarios", default="all",
                        help="'all' 또는 콤마로 구분한 시나리오 키")
    parser.add_argument("--suites", default=",".join(SUITE_NAMES),
                        help=f"돌릴 스위트 — {', '.join(SUITE_NAMES)}")
    parser.add_argument("--run-id", default=None, help="미지정이면 타임스탬프")
    parser.add_argument("--out", default="runs", help="산출물 디렉터리 (기본 runs/)")
    parser.add_argument("--note", default=None, help="이 실행에 붙일 메모")
    args = parser.parse_args()

    suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    unknown = [s for s in suites if s not in SUITE_NAMES]
    if unknown:
        print(f"없는 스위트: {unknown} — {SUITE_NAMES} 중에서 골라라")
        return 2

    client = httpx.Client(base_url=args.base_url, timeout=TURN_TIMEOUT_S)
    try:
        listing = client.get("/api/scenarios")
        listing.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"서버에 못 붙었다 ({args.base_url}): {exc}")
        print("uvicorn 이 떠 있는지 확인하라 — /health 가 200 이어야 한다.")
        return 1

    catalog = listing.json()
    models = catalog["models"]
    available = {s["key"]: s["title"] for s in catalog["scenarios"]}
    if not available:
        print("서버에 시나리오가 없다. 콘솔에서 먼저 하나 지어라.")
        return 1

    if args.scenarios == "all":
        keys = sorted(available)
    else:
        keys = [k.strip() for k in args.scenarios.split(",") if k.strip()]
        missing = [k for k in keys if k not in available]
        if missing:
            print(f"없는 시나리오 {missing} — 사용 가능: {sorted(available)}")
            return 2

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (SERVER_ROOT / args.out / run_id) if not Path(args.out).is_absolute() \
        else Path(args.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    conn = repo.connect(load_settings().db_path)
    repo.start_test_run(
        conn,
        run_id=run_id,
        base_url=args.base_url,
        agent_model=models["agent"],
        moderation_model=models["moderation"],
        suites=suites,
        note=args.note,
    )

    print(f"실행 {run_id} · 에이전트 {models['agent']} · Moderation {models['moderation'] or '미설정'}")
    print(f"시나리오 {len(keys)}개 × 스위트 {len(suites)}개 → {run_dir}")
    if models["moderation"] is None:
        print("!! Moderation 키가 없다 — 안전 판정 결과를 신뢰할 수 없는 실행이다.")
    unscripted = [k for k in keys if not has_script(k)]
    if unscripted:
        print(f"!! 대본 없는 시나리오 {len(unscripted)}개는 목표 달성을 기대하지 않는다: {unscripted}")

    sweep = Sweep(client=client, conn=conn, run_id=run_id, run_dir=run_dir)
    started = time.monotonic()
    try:
        for key in keys:
            print(f"\n=== {key} — {available[key]}")
            for suite in suites:
                sweep.run_session(scenario_id=key, title=available[key], suite=suite)
    except KeyboardInterrupt:
        print("\n중단됨 — 여기까지의 턴은 이미 저장돼 있다.")
    finally:
        sweep.close()
        elapsed = int(time.monotonic() - started)
        repo.finish_test_run(
            conn,
            run_id,
            scenario_count=len(keys),
            session_count=sweep.session_count,
            turn_count=sweep.turn_count,
        )
        csv_rows = write_timings_csv(conn, run_id, run_dir / "timings.csv")
        (run_dir / "sessions.json").write_text(
            json.dumps(sweep.session_notes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        client.close()
        conn.close()

    print(
        f"\n턴 {sweep.turn_count}개 · 세션 {sweep.session_count}개 · {elapsed}초"
        f" · OK {sweep.verdicts['OK']} / MISMATCH {sweep.verdicts['MISMATCH']}"
        f" / FAIL {sweep.verdicts['FAIL']}"
    )
    print(f"단계 기록 {csv_rows}행 → {run_dir / 'timings.csv'}")
    print(f"리포트: python -m scripts.report_run --run-id {run_id}")
    return 1 if sweep.verdicts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
