"""심판 — 판단 에이전트가 PASS 시킨 후보를 결정적으로 재검사한다. 순수 함수.

**PASS → REGENERATE 강등만 한다.** 절대 REGENERATE 를 PASS 로 올리지 않으므로
안전성을 낮출 수 없다.

이게 존재하는 이유: 판단 에이전트도 LLM 이라 놓친다. 그렇다고 LLM 을 하나 더
붙이는 건 답이 아니다 — 그것도 놓친다. 결정적으로 잡을 수 있는 것만 여기서 잡고,
나머지는 판단 에이전트에게 남긴다.

**결정적으로 못 잡는 것 (판단 에이전트 소관으로 남음):**
  - 어휘가 안 겹치게 바꿔 말한 정답 유출
  - 선생님 톤으로의 페르소나 이탈
  - 부드럽게 표현된 금지 추론
  - *엉뚱한* 목표로 유도하는 경우
  - BIF 아동에 대한 발달 적합성

진짜 백스톱은 관측 콘솔이다 — referee_override_rate 를 사람이 보고 판단 품질을 안다.
"""

from __future__ import annotations

import re

from pipeline import textmatch
from state.models import (
    BLOCKING_VIOLATIONS,
    JudgeOutcome,
    MascotMode,
    MicroGoal,
    RefereeViolation,
)

# 후보가 목표의 정답 문구를 이만큼 품고 있으면 유출로 본다.
ANSWER_LEAK_THRESHOLD = 0.75

# 후보가 캐릭터의 직전 대사와 이만큼 겹치면 반복으로 본다.
REPETITION_THRESHOLD = 0.65

REPETITION_LOOKBACK = 2

# 유도형 종결. NO_STEER 는 권고 전용이라 놓쳐도(false negative) 비용이 작다 —
# 반대로 과발동하면 후보 예산을 태우고 SAFE_FALLBACK 이 나가므로 넉넉하게 잡는다.
_STEERING = re.compile(
    r"(\?|？|까|까요|니|나요|을래|ㄹ래|래\b|어때|어떨까|얘기해|말해|알려|보여|해볼|볼까|같이)"
)


def _leaks_answer(candidate: str, goal: MicroGoal, reveal_line: str | None) -> bool:
    targets = list(goal.required_evidence)
    if reveal_line:
        targets.append(reveal_line)
    return any(
        textmatch.containment(target, candidate) >= ANSWER_LEAK_THRESHOLD for target in targets
    )


def _repeats(candidate: str, recent_character_lines: list[str]) -> bool:
    return any(
        textmatch.jaccard(candidate, line) >= REPETITION_THRESHOLD
        for line in recent_character_lines[-REPETITION_LOOKBACK:]
    )


def review(
    candidate: str,
    judge: JudgeOutcome,
    *,
    focus_goal: MicroGoal | None,
    reveal_line: str | None,
    recent_character_lines: list[str],
    mascot_mode: MascotMode,
) -> list[RefereeViolation]:
    """PASS 판정을 받은 후보의 위반 목록. 비어 있으면 그대로 전달해도 된다.

    reveal_line 은 현재 목표의 S3 정답 문구. 도움 세기가 아직 S3 가 아니어도
    후보가 그 문구를 미리 흘리면 안 되므로 항상 넘긴다.
    """
    violations: list[RefereeViolation] = []

    # [snippet:referee]
    # 판단 결과 자체가 자기모순인 경우. 파싱은 됐지만 신뢰할 수 없다.
    if judge.decision == "PASS" and (judge.failure_codes or not judge.safe_to_send):
        violations.append(RefereeViolation.JUDGE_INCOHERENT)

    if focus_goal and _leaks_answer(candidate, focus_goal, reveal_line):
        violations.append(RefereeViolation.ANSWER_LEAK)

    if _repeats(candidate, recent_character_lines):
        violations.append(RefereeViolation.REPETITION)
    # [/snippet:referee]

    # 목표가 남았는데 캐릭터가 유도를 안 한다. 단 SCAFFOLD 모드에서는 마스코트가
    # 유도를 맡으므로 캐릭터에게 요구하지 않는다.
    if (
        focus_goal
        and mascot_mode is not MascotMode.SCAFFOLD
        and not _STEERING.search(candidate)
    ):
        violations.append(RefereeViolation.NO_STEER)

    return violations


def blocks_delivery(violations: list[RefereeViolation], is_last_attempt: bool) -> bool:
    """이 위반들 때문에 후보를 버려야 하는가.

    NO_STEER 는 권고 전용이라 절대 단독으로 막지 않는다. 정당한 1인칭 대사
    ("(무릎을 문지르며) 아... 아파...")에 오탐하면 후보 3개를 다 태우고
    SAFE_FALLBACK 이 나가는데, 그건 원래 후보보다 아이에게 나쁘다.

    마지막 시도에서는 차단 위반이라도 통과시킨다 — 여기서 막으면 남는 건
    SAFE_FALLBACK 뿐이고, 실제 응답이 고정 문구보다는 낫다. 대신 위반은 기록에 남는다.
    """
    if is_last_attempt:
        return False
    return any(v in BLOCKING_VIOLATIONS for v in violations)


def revision_note(violations: list[RefereeViolation], focus_goal: MicroGoal | None) -> str:
    """다음 생성 시도에 붙일 수정 지시."""
    notes: list[str] = []
    if RefereeViolation.ANSWER_LEAK in violations and focus_goal:
        notes.append("정답을 직접 말하지 말 것 — 아이가 스스로 말하도록 되물어라")
    if RefereeViolation.REPETITION in violations:
        notes.append("직전에 한 말과 같은 말을 반복하지 말 것")
    if RefereeViolation.NO_STEER in violations:
        notes.append("아이가 대답할 수 있는 질문이나 권유를 한 개 포함할 것")
    if RefereeViolation.JUDGE_INCOHERENT in violations:
        notes.append("안전하고 짧은 1인칭 대사로 다시 쓸 것")
    return " / ".join(notes)
