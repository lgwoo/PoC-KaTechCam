"""마이크로 목표 근거 검증. 순수 함수.

LLM 스캐너가 "이 목표는 달성됐다"고 주장하면, 그 근거를 코드가 다시 검증한다.
프롬프트 지시만으로는 안 지켜진다는 것이 TS 판에서 실측으로 확인됐다 —
why_this_satisfies 설명을 프롬프트에 추가했더니 스캐너가 "친구:"(캐릭터) 대사를
근거로 드는 회귀가 발생했다.

TS 판(chat.ts:352-371) 대비 조인 부분:

1. **단방향 포함.** TS 는 `evidence.includes(line) || line.includes(evidence)` 였다.
   뒤쪽 OR 때문에 "응" 한 글자를 포함한 긴 근거 문자열이 통과한다. 아이 발화가
   근거를 품고 있어야지, 근거가 아이 발화를 품는 건 아무 의미가 없다.
2. **필러 배제.** "응", "몰라" 만으로는 어떤 목표의 증거도 될 수 없다.
3. **앵무새 가드.** 직전 턴들의 캐릭터·마스코트 대사와 많이 겹치는 근거는 기각한다.
   이게 없으면 S3 가 정답을 말하고 → 아이가 따라 하고 → 목표가 달성 처리되어
   성공률 지표 자체가 허구가 된다. 이 파일에서 가장 중요한 검사다.
"""

from __future__ import annotations

from pipeline import textmatch
from state.models import GoalScanRecord, RejectedEvidence

# 근거가 직전 에이전트 발화와 이만큼 겹치면 아이가 따라 말한 것으로 본다.
# 녹화된 실제 턴으로 튜닝할 값이다 — 지금은 보수적으로 잡았다.
PARROT_THRESHOLD = 0.70

# 앵무새 판정에 포함할 직전 턴 수.
PARROT_LOOKBACK_TURNS = 2


class ScanClaim:
    """스캐너가 주장한 달성 하나. LLM 계약(contracts.py)에서 넘어온다."""

    __slots__ = ("goal_id", "evidence", "why_this_satisfies")

    def __init__(self, goal_id: str, evidence: str, why_this_satisfies: str) -> None:
        self.goal_id = goal_id
        self.evidence = evidence.strip()
        self.why_this_satisfies = why_this_satisfies.strip()


def check_claim(
    claim: ScanClaim,
    *,
    open_goal_ids: set[str],
    child_lines: list[str],
    recent_agent_lines: list[str],
    parrot_threshold: float = PARROT_THRESHOLD,
) -> str | None:
    """근거를 기각할 사유. 통과하면 None.

    child_lines 는 아이 발화 원문만. recent_agent_lines 는 최근 캐릭터·마스코트 대사.
    """
    if claim.goal_id not in open_goal_ids:
        return f"열려 있지 않은 목표({claim.goal_id})를 달성으로 주장함"

    if not claim.evidence:
        return "근거 문자열이 비어 있음"

    if not claim.why_this_satisfies:
        return "why_this_satisfies 가 비어 있음 — 스스로 설명하지 못한 주장"

    # 아이 발화가 근거를 품고 있어야 한다. 방향을 뒤집으면 안 된다.
    if not any(textmatch.contains_normalized(line, claim.evidence) for line in child_lines):
        return "근거가 아이 발화에 실제로 없음 (캐릭터 대사를 인용했을 가능성)"

    if textmatch.is_filler_only(claim.evidence):
        return f"근거가 필러뿐임({claim.evidence!r}) — 어떤 목표의 증거도 될 수 없음"

    for agent_line in recent_agent_lines:
        overlap = textmatch.containment(claim.evidence, agent_line)
        if overlap >= parrot_threshold:
            return (
                f"직전 에이전트 발화를 따라 말한 것으로 보임 "
                f"(겹침 {overlap:.2f} >= {parrot_threshold:.2f}): {agent_line!r}"
            )

    return None


def evaluate_claims(
    claims: list[ScanClaim],
    *,
    open_goal_ids: set[str],
    child_lines: list[str],
    recent_agent_lines: list[str],
    parrot_threshold: float = PARROT_THRESHOLD,
) -> GoalScanRecord:
    """스캐너 주장 전체를 검증해 관측용 기록으로 만든다.

    기각된 것도 사유와 함께 남긴다 — 과소 인정을 디버깅하려면 무엇이 왜 떨어졌는지
    콘솔에서 보여야 한다.
    """
    record = GoalScanRecord(scanned_goal_ids=sorted(open_goal_ids))
    seen: set[str] = set()

    for claim in claims:
        if claim.goal_id in seen:
            continue
        reason = check_claim(
            claim,
            open_goal_ids=open_goal_ids,
            child_lines=child_lines,
            recent_agent_lines=recent_agent_lines,
            parrot_threshold=parrot_threshold,
        )
        if reason is None:
            record.accepted.append(claim.goal_id)
            seen.add(claim.goal_id)
        else:
            record.rejected.append(
                RejectedEvidence(goal_id=claim.goal_id, evidence=claim.evidence, reason=reason)
            )

    return record
