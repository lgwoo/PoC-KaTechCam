"""심판 테스트 — 판단 에이전트가 PASS 시킨 후보의 결정적 재검사."""

from __future__ import annotations

from pipeline.referee import blocks_delivery, review, revision_note
from state.models import JudgeOutcome, MascotMode, MicroGoal, RefereeViolation

GOAL = MicroGoal(
    id="MG-03",
    description="감정과 넘어진 사건을 연결한다.",
    required_evidence=["넘어졌기 때문에 아프거나 속상하다고 표현한다."],
)
REVEAL = "친구는 넘어져서 아프고 속상한 기분이야."

PASSING = JudgeOutcome(safe_to_send=True, decision="PASS", reason="적절함")


def run(candidate: str, judge: JudgeOutcome = PASSING, **kwargs) -> list[RefereeViolation]:
    defaults = dict(
        focus_goal=GOAL,
        reveal_line=REVEAL,
        recent_character_lines=[],
        mascot_mode=MascotMode.NONE,
    )
    defaults.update(kwargs)
    return review(candidate, judge, **defaults)


def test_clean_candidate_has_no_violations():
    assert run("(훌쩍이며) ...너는 내가 어떤 기분일 것 같아?") == []


def test_detects_self_contradictory_judge():
    """PASS 인데 failure_codes 가 붙어 있으면 그 판정은 신뢰할 수 없다."""
    judge = JudgeOutcome(
        safe_to_send=True, decision="PASS", failure_codes=["MULTIPLE_QUESTIONS"]
    )
    assert RefereeViolation.JUDGE_INCOHERENT in run("괜찮아?", judge)


def test_detects_pass_with_unsafe_flag():
    judge = JudgeOutcome(safe_to_send=False, decision="PASS")
    assert RefereeViolation.JUDGE_INCOHERENT in run("괜찮아?", judge)


def test_detects_answer_leak_of_reveal_line():
    """S3 정답 문구를 캐릭터가 미리 흘리면 아이가 스스로 도달할 기회가 사라진다."""
    assert RefereeViolation.ANSWER_LEAK in run("친구는 넘어져서 아프고 속상한 기분이야")


def test_detects_repetition_of_recent_character_line():
    previous = "(훌쩍이며) 너는 내가 어떤 기분일 것 같아?"
    violations = run(previous, recent_character_lines=["딴 얘기", previous])
    assert RefereeViolation.REPETITION in violations


def test_no_steer_when_candidate_asks_nothing():
    assert RefereeViolation.NO_STEER in run("(무릎을 문지른다) ...아파.")


def test_scaffold_mode_exempts_character_from_steering():
    """S1 이상에서는 마스코트가 유도를 맡는다 — 캐릭터에게까지 질문을 요구하면
    한 턴에 질문이 두 개가 된다."""
    violations = run("(무릎을 문지른다) ...아파.", mascot_mode=MascotMode.SCAFFOLD)
    assert RefereeViolation.NO_STEER not in violations


def test_no_steer_alone_never_blocks():
    """권고 전용. 정당한 1인칭 대사에 오탐하면 후보 예산을 태우고
    SAFE_FALLBACK 이 나가는데, 그건 원래 후보보다 아이에게 나쁘다."""
    assert not blocks_delivery([RefereeViolation.NO_STEER], is_last_attempt=False)


def test_blocking_violations_block_early_attempts():
    assert blocks_delivery([RefereeViolation.ANSWER_LEAK], is_last_attempt=False)
    assert blocks_delivery([RefereeViolation.REPETITION], is_last_attempt=False)
    assert blocks_delivery([RefereeViolation.JUDGE_INCOHERENT], is_last_attempt=False)


def test_last_attempt_is_never_blocked():
    """여기서 막으면 남는 건 SAFE_FALLBACK 뿐이다. 실제 응답이 고정 문구보다 낫다."""
    assert not blocks_delivery([RefereeViolation.ANSWER_LEAK], is_last_attempt=True)


def test_no_focus_goal_suppresses_goal_rules():
    """목표를 다 끝냈으면 유출도 미유도도 따질 게 없다."""
    violations = run("친구는 넘어져서 아프고 속상한 기분이야", focus_goal=None, reveal_line=None)
    assert RefereeViolation.ANSWER_LEAK not in violations
    assert RefereeViolation.NO_STEER not in violations


def test_revision_note_mentions_each_violation():
    note = revision_note(
        [RefereeViolation.ANSWER_LEAK, RefereeViolation.NO_STEER], GOAL
    )
    assert "정답" in note
    assert "질문" in note
