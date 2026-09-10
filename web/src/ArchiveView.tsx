// 지난 역할극 열람.
//
// 세션 진행 상태의 원본은 서버 프로세스 메모리다(routes_session._live). 그래서 서버가
// 한 번 재시작되면 그 세션으로 대화를 이어 갈 수는 없다. 다만 기록은 DB 에 남아 있고
// 조회는 읽기라서 되살려 읽을 수 있다 — 이 화면은 그것만 한다.
//
// live=false 가 "DB 에서 되살린 읽기 전용"이라는 표시다. 그 구분을 화면에 적어 두지
// 않으면, 왜 여기서는 발화를 못 보내는지 알 방법이 없다.

import { useCallback, useEffect, useState } from "react";

import { getSession, listSessions } from "./api";
import {
  END_REASON_LABEL,
  INTAKE_CATEGORY_LABEL,
  MASCOT_MODE_LABEL,
} from "./labels";
import type { SessionListRow, StoredSessionView, StoredTurn } from "./types";

function TurnDetail({ turn }: { turn: StoredTurn }) {
  return (
    <div className="turn-detail">
      <p>
        <b>아이</b> {turn.child_input_stored}
        {turn.child_input_stored !== turn.child_input && (
          <span className="muted small"> (개인정보 마스킹됨)</span>
        )}
      </p>
      {turn.intake && <p className="muted small">판정 사유: {turn.intake.reason}</p>}
      {turn.intake?.override_source && (
        <p className="muted small">오버라이드: {turn.intake.override_source}</p>
      )}
      {turn.mascot_line && (
        <p>
          <b>마스코트</b> {turn.mascot_line}
        </p>
      )}

      <ol className="candidates">
        {turn.candidates.map((candidate) => (
          <li key={candidate.attempt_no} className={candidate.delivered ? "on" : ""}>
            <span className="tag subtle">{candidate.outcome}</span>
            {candidate.delivered === 1 && <span className="tag ok">전달</span>}{" "}
            {candidate.text}
            {candidate.judge_reason && (
              <div className="muted small">판정: {candidate.judge_reason}</div>
            )}
            {candidate.judge_failure_codes && candidate.judge_failure_codes !== "[]" && (
              <div className="muted small">실패 코드: {candidate.judge_failure_codes}</div>
            )}
          </li>
        ))}
      </ol>

      {turn.closing_line && (
        <p>
          <b>마무리</b> {turn.closing_line}
        </p>
      )}
      {turn.goal_rejections.length > 0 && (
        <ul className="muted small">
          {turn.goal_rejections.map((rejection, index) => (
            <li key={index}>
              목표 스캔 기각 {rejection.subgoal_id}: {rejection.reason}
            </li>
          ))}
        </ul>
      )}

      <table className="timing">
        <thead>
          <tr>
            <th>단계</th>
            <th>시작(+ms)</th>
            <th>소요(ms)</th>
          </tr>
        </thead>
        <tbody>
          {turn.timings.map((timing, index) => (
            <tr key={index} className={timing.ok ? "" : "failed"}>
              <td>
                {timing.stage}
                {timing.attempt_no ? ` ${timing.attempt_no}차` : ""}
                {timing.ok ? "" : " (실패)"}
              </td>
              <td>{timing.start_offset_ms}</td>
              <td>{timing.duration_ms}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">가로 오프셋이 겹치는 단계가 실제로 병렬로 돈 구간이다.</p>
    </div>
  );
}

export default function ArchiveView() {
  const [rows, setRows] = useState<SessionListRow[]>([]);
  const [picked, setPicked] = useState<string | null>(null);
  const [detail, setDetail] = useState<StoredSessionView | null>(null);
  const [openTurn, setOpenTurn] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    listSessions()
      .then((data) => setRows(data.sessions))
      .catch((e) => setError(String(e)));
  }, []);

  const open = useCallback(async (sessionId: string) => {
    setPicked(sessionId);
    setLoading(true);
    setError(null);
    setDetail(null);
    setOpenTurn(null);
    try {
      setDetail(await getSession(sessionId));
    } catch (e) {
      // 시나리오의 목표 행이 지워진 세션은 되살릴 수 없다(목표 설명도 첫 대사도 거기서 온다).
      // 반쪽을 보여주는 대신 왜 못 읽는지 적는다.
      setError(`${String(e)} — 시나리오가 지워진 세션은 되읽을 수 없습니다.`);
    } finally {
      setLoading(false);
    }
  }, []);

  // 턴이 0인 세션은 시작만 하고 아무 말도 하지 않은 것이다. 목록만 늘린다.
  const played = rows.filter((row) => row.turn_index > 0);

  return (
    <main className="archive">
      <aside className="archive-list">
        <h3>
          지난 역할극 <span className="muted">{played.length}개</span>
        </h3>
        <p className="muted small">
          서버를 재시작해도 DB 에 남은 기록은 여기서 읽힙니다. 턴이 없는 세션은 숨깁니다.
        </p>
        {played.map((row) => (
          <button
            key={row.session_id}
            className={picked === row.session_id ? "archive-row on" : "archive-row"}
            onClick={() => void open(row.session_id)}
          >
            <span className="mono">{row.session_id.slice(0, 8)}</span>
            <span className="tag subtle">턴 {row.turn_index}</span>
            {row.end_reason ? (
              <span className={row.end_reason === "SAFETY_HALT" ? "tag warn" : "tag ok"}>
                {END_REASON_LABEL[row.end_reason] ?? row.end_reason}
              </span>
            ) : (
              // "진행 중"이 아니다. 진행 상태는 프로세스 메모리에만 있어서 이어 갈 수
              // 없고, 종료 조건에 닿지 못한 채 남은 기록이다.
              <span className="tag subtle">안 끝남</span>
            )}
            <span className="muted small">
              {row.started_at.slice(0, 16).replace("T", " ")}
            </span>
          </button>
        ))}
        {played.length === 0 && !error && <p className="muted">아직 기록이 없습니다.</p>}
      </aside>

      <section className="archive-detail">
        {error && <p className="error">{error}</p>}
        {loading && <p className="muted">불러오는 중…</p>}
        {!picked && !error && !loading && <p className="muted">왼쪽에서 세션을 고르세요.</p>}
        {detail && (
          <>
            <h2>
              {detail.scenario.title}{" "}
              <span className="muted">[{detail.scenario.scenario_level}]</span>
            </h2>
            <p className="muted small">
              {detail.live
                ? "이 세션은 아직 메모리에 살아 있습니다 — 콘솔에서 이어 갈 수 있습니다."
                : "DB 에서 되살린 기록입니다. 읽기 전용이라 새 턴을 이어 갈 수 없습니다."}
            </p>
            <div className="row">
              <span className="tag subtle">
                턴 {detail.turn_index}/{detail.scenario.maximum_turn_count}
              </span>
              <span
                className={detail.end_reason === "SAFETY_HALT" ? "tag warn" : "tag ok"}
              >
                {detail.end_reason
                  ? (END_REASON_LABEL[detail.end_reason] ?? detail.end_reason)
                  : "안 끝남 — 종료 조건에 닿지 못했습니다"}
              </span>
              <span className="tag">
                목표 {detail.summary.achieved}/{detail.summary.total} · 스스로{" "}
                {detail.summary.achieved_without_answer_reveal} · 포기{" "}
                {detail.summary.abandoned}
              </span>
            </div>

            <div className="transcript archive-transcript">
              {detail.transcript.map((utterance, index) => (
                <div key={index} className={`bubble ${utterance.speaker}`}>
                  <span className="who">
                    {utterance.speaker === "CHILD"
                      ? "아이"
                      : utterance.speaker === "MASCOT"
                        ? "마스코트"
                        : detail.scenario.character_name}
                  </span>
                  <p>{utterance.text}</p>
                </div>
              ))}
            </div>

            <h3>턴 기록</h3>
            {detail.turns.map((turn) => (
              <div className="turn-card" key={turn.turn_id}>
                <button
                  className="turn-toggle"
                  onClick={() =>
                    setOpenTurn(openTurn === turn.turn_number ? null : turn.turn_number)
                  }
                >
                  <span>턴 {turn.turn_number}</span>
                  <span className="tag subtle">{turn.total_ms}ms</span>
                  {turn.intake && (
                    <span className="tag subtle">
                      {INTAKE_CATEGORY_LABEL[turn.intake.category] ?? turn.intake.category}
                    </span>
                  )}
                  {turn.mascot_mode !== "NONE" && (
                    <span className="tag warn">
                      {MASCOT_MODE_LABEL[turn.mascot_mode] ?? turn.mascot_mode}
                    </span>
                  )}
                  {turn.candidates.length > 1 && (
                    <span className="tag">시도 {turn.candidates.length}회</span>
                  )}
                  {turn.fallback_used && <span className="tag warn">기본 응답</span>}
                  {turn.degraded_stages.length > 0 && (
                    <span className="tag warn">
                      degraded {turn.degraded_stages.join(", ")}
                    </span>
                  )}
                </button>
                {openTurn === turn.turn_number && <TurnDetail turn={turn} />}
              </div>
            ))}
          </>
        )}
      </section>
    </main>
  );
}
