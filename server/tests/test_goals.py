"""목표 근거 검증 테스트.

가장 중요한 것은 앵무새 가드다. 그게 없으면 S3 가 정답을 말하고 → 아이가 따라 하고 →
목표가 달성 처리되어 PoC 성공률 지표 전체가 허구가 된다.
"""

from __future__ import annotations

from pipeline.goals import ScanClaim, check_claim, evaluate_claims

OPEN = {"MG-01", "MG-02", "MG-03"}

# 시나리오 SCN-001 의 S3 정답 공개 문구
S3_REVEAL = "친구는 넘어져서 아프고 속상한 기분이야."


def claim(goal_id: str, evidence: str, why: str = "감정을 사건과 연결해 표현했다.") -> ScanClaim:
    return ScanClaim(goal_id=goal_id, evidence=evidence, why_this_satisfies=why)


def test_accepts_evidence_actually_in_child_line():
    child = ["친구가 넘어져서 아팠을 것 같아"]
    assert (
        check_claim(
            claim("MG-03", "친구가 넘어져서 아팠을 것 같아"),
            open_goal_ids=OPEN,
            child_lines=child,
            recent_agent_lines=[],
        )
        is None
    )


def test_rejects_evidence_that_came_from_the_character():
    """TS 판에서 실제로 발생했던 회귀 — 스캐너가 캐릭터 대사를 근거로 들었다."""
    reason = check_claim(
        claim("MG-02", "아야... 나 무릎 아파"),
        open_goal_ids=OPEN,
        child_lines=["응"],
        recent_agent_lines=["아야... 나 무릎 아파"],
    )
    assert reason is not None
    assert "아이 발화에 실제로 없음" in reason


def test_rejects_reversed_containment():
    """TS 의 `evidence.includes(line) || line.includes(evidence)` 허점.

    아이는 "응" 한 마디만 했는데, 그 한 글자를 품은 긴 근거 문자열이 통과했었다.
    단방향으로 바꿨으므로 이제 떨어져야 한다.
    """
    reason = check_claim(
        claim("MG-02", "응 친구가 넘어져서 아프고 속상할 것 같다고 아이가 이해했다"),
        open_goal_ids=OPEN,
        child_lines=["응"],
        recent_agent_lines=[],
    )
    assert reason is not None
    assert "아이 발화에 실제로 없음" in reason


def test_rejects_filler_only_evidence():
    reason = check_claim(
        claim("MG-02", "몰라"),
        open_goal_ids=OPEN,
        child_lines=["몰라"],
        recent_agent_lines=[],
    )
    assert reason is not None
    assert "필러" in reason


def test_rejects_parroted_answer_after_s3_reveal():
    """핵심 케이스. S3 에서 정답을 알려준 바로 다음 턴에 아이가 그대로 따라 말했다.

    이걸 인정하면 "아이가 스스로 도달했다"는 기록이 거짓이 된다.
    """
    parroted = "친구는 넘어져서 아프고 속상한 기분이야"
    reason = check_claim(
        claim("MG-03", parroted),
        open_goal_ids=OPEN,
        child_lines=[parroted],
        recent_agent_lines=[S3_REVEAL],
    )
    assert reason is not None
    assert "따라 말한 것" in reason


def test_accepts_own_words_even_after_s3_reveal():
    """정답을 들은 뒤라도 자기 말로 다시 표현하면 인정한다.
    앵무새 가드가 과발동해서 이것까지 막으면 도움 세기 조정 자체가 무의미해진다."""
    own_words = "무릎 까져서 눈물 났나 봐"
    reason = check_claim(
        claim("MG-03", own_words),
        open_goal_ids=OPEN,
        child_lines=[own_words],
        recent_agent_lines=[S3_REVEAL],
    )
    assert reason is None


def test_rejects_claim_without_explanation():
    reason = check_claim(
        ScanClaim("MG-01", "친구가 넘어졌어", ""),
        open_goal_ids=OPEN,
        child_lines=["친구가 넘어졌어"],
        recent_agent_lines=[],
    )
    assert reason is not None
    assert "why_this_satisfies" in reason


def test_rejects_already_closed_goal():
    reason = check_claim(
        claim("MG-99", "친구가 넘어졌어"),
        open_goal_ids=OPEN,
        child_lines=["친구가 넘어졌어"],
        recent_agent_lines=[],
    )
    assert reason is not None
    assert "열려 있지 않은 목표" in reason


def test_evaluate_records_both_sides():
    """기각된 것도 사유와 함께 남아야 콘솔에서 과소 인정을 디버깅할 수 있다."""
    child = ["친구가 넘어졌어"]
    record = evaluate_claims(
        [
            claim("MG-01", "친구가 넘어졌어", "넘어짐을 언급했다."),
            claim("MG-02", "친구가 슬퍼 보여", "감정을 표현했다."),  # 아이가 한 말이 아님
        ],
        open_goal_ids=OPEN,
        child_lines=child,
        recent_agent_lines=[],
    )
    assert record.accepted == ["MG-01"]
    assert len(record.rejected) == 1
    assert record.rejected[0].goal_id == "MG-02"
    assert record.scanned_goal_ids == sorted(OPEN)


def test_duplicate_claims_counted_once():
    child = ["친구가 넘어졌어"]
    record = evaluate_claims(
        [claim("MG-01", "친구가 넘어졌어"), claim("MG-01", "친구가 넘어졌어")],
        open_goal_ids=OPEN,
        child_lines=child,
        recent_agent_lines=[],
    )
    assert record.accepted == ["MG-01"]
