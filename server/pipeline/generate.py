"""후보 생성 → 판단 → 심판 루프.

TS 판(chat.ts:103-196)의 구조를 유지하되 두 가지를 더한다.
  - 심판(referee): 판단이 PASS 시킨 후보를 결정적으로 재검사한다.
  - 계측: 후보별 생성/판단 시간이 전부 남는다. TS 판은 이걸 버렸다.

판단 호출이 구조 검증에 실패하면 REGENERATE 로 간주한다 — fail closed.
"실패"를 PASS 쪽으로 해석하면 검증되지 않은 대사가 아이에게 나간다.
"""

from __future__ import annotations

import asyncio
import json
import re

from llm.client import LlmClient, StageRecorder
from llm.contracts import JudgeContract, TurnReplyContract
from llm.errors import LlmCallError, LlmContractError
from pipeline import prompts, referee
from safety.moderation import ModerationClient
from state.models import (
    CandidateRecord,
    JudgeOutcome,
    MascotMode,
    MicroGoal,
    ModerationOutcome,
    ModerationSeverity,
    RefereeViolation,
    Scenario,
)

MAX_CANDIDATES = 3

# SAFE-05: 안전 대응 문구는 사전에 검토된 고정 문구를 쓴다.
SAFE_FALLBACK_LINE = "(친구가 잠시 멈칫한다) 음... 나 잠깐 다른 얘기 하고 싶어. 다시 물어봐 줄래?"

# 대화 맥락을 "아이: / 친구: / 마스코트:" 형식으로 넘기다 보니 모델이 그 패턴을 이어받아
# 화자 라벨을 붙여서 답한다. 실측: 캐릭터가 "마스코트: 어? 잠깐..." 이라고 시작해서
# 마스코트를 흉내 냈다. 프롬프트로도 막지만 결정적으로 한 번 더 벗겨낸다.
_SPEAKER_PREFIX = re.compile(r"^\s*(마스코트|친구|아이|캐릭터|assistant)\s*[:：]\s*")

# 마스코트가 유도를 맡은 턴에 캐릭터까지 물으면 한 턴에 질문이 두 개가 되어 아이가
# 무엇에 답할지 모른다. 판정 에이전트도 이걸 잡지만(CHARACTER_STEERS_ANYWAY) 그건
# 확률이다 — 실측에서 프롬프트를 두 번 조여도 6건에서 4건까지만 줄었다.
# 물음표는 결정적으로 볼 수 있으니 판정 왕복(p50 4.3초) 전에 거른다.
#
# 물음표만 본다. referee._STEERING 같은 느슨한 패턴을 쓰면 "그랬으니까" 처럼 평범한
# 서술을 질문으로 잡아 후보 예산을 태운다 — 여기서는 거짓 양성이 곧 기본 응답이다.
_QUESTION_MARK = re.compile(r"[?？]")


def strip_speaker_prefix(text: str) -> str:
    previous = None
    current = text.strip()
    while previous != current:
        previous = current
        current = _SPEAKER_PREFIX.sub("", current).strip()
    return current


class GenerationOutput:
    __slots__ = ("text", "mascot_line", "fallback_used", "candidates")

    def __init__(
        self,
        text: str,
        mascot_line: str | None,
        fallback_used: bool,
        candidates: list[CandidateRecord],
    ) -> None:
        self.text = text
        self.mascot_line = mascot_line
        self.fallback_used = fallback_used
        self.candidates = candidates


# 마스코트 대사 유출 검사에 쓰는 더미 판정. 심판의 ANSWER_LEAK 규칙만 빌려 쓴다.
_ALWAYS_PASS = JudgeOutcome(safe_to_send=True, decision="PASS")

# 등급이 여기 들면 대사를 버린다. 단순 flagged 로 막던 때는 "넘어졌어", "무릎이 아파" 같은
# 시나리오 그대로의 대사가 violence(MODERATE) 로 걸려서 매 턴 시도를 하나씩 태웠다.
# 다치는 이야기를 다루는 시나리오에서는 MODERATE 가 정상 신호다.
_OUTPUT_BLOCKING_SEVERITY = frozenset({ModerationSeverity.HIGH, ModerationSeverity.CRITICAL})


def _blocks_output(outcome: ModerationOutcome) -> bool:
    return outcome.severity in _OUTPUT_BLOCKING_SEVERITY


def _mascot_leaks_answer(
    line: str,
    *,
    focus_goal: MicroGoal | None,
    reveal_line: str | None,
    answer_reveal_allowed: bool,
) -> bool:
    """마스코트가 정답을 흘렸는가. S3(정답 제공) 턴에서는 흘리는 게 임무라 면제한다."""
    if not focus_goal or answer_reveal_allowed:
        return False
    violations = referee.review(
        line,
        _ALWAYS_PASS,
        focus_goal=focus_goal,
        reveal_line=reveal_line,
        recent_character_lines=[],
        # 마스코트는 유도가 본업이라 NO_STEER 를 면제한다.
        mascot_mode=MascotMode.SCAFFOLD,
    )
    return RefereeViolation.ANSWER_LEAK in violations


def _to_outcome(contract: JudgeContract) -> JudgeOutcome:
    revision = contract.revision_instruction
    return JudgeOutcome(
        safe_to_send=contract.safe_to_send,
        decision=contract.decision,
        failure_codes=list(contract.failure_codes),
        reason=contract.reason,
        revision_change=list(revision.change) if revision else [],
        revision_avoid=list(revision.avoid) if revision else [],
    )


async def generate_approved_reply(
    *,
    client: LlmClient,
    moderation: ModerationClient,
    recorder: StageRecorder,
    scenario: Scenario,
    history: str,
    child_input: str,
    focus_goal: MicroGoal | None,
    reveal_line: str | None,
    recent_character_lines: list[str],
    mascot_mode: MascotMode,
    mascot_directive: str,
    mascot_fallback_line: str | None,
    answer_reveal_allowed: bool = False,
    profanity: bool = False,
    off_topic: bool = False,
    high_concern: bool = False,
    character_bind: bool = False,
    misunderstanding: bool = False,
    pii: bool = False,
    goal_hint_enabled: bool = True,
    max_candidates: int = MAX_CANDIDATES,
) -> GenerationOutput:
    generation_system = prompts.generation_system(scenario)
    judge_system = prompts.judge_system(scenario)
    candidates: list[CandidateRecord] = []
    revision_note = ""

    for attempt in range(1, max_candidates + 1):
        is_last = attempt == max_candidates

        user_prompt = prompts.generation_user(
            history=history,
            profanity=profanity,
            off_topic=off_topic,
            high_concern=high_concern,
            character_bind=character_bind,
            misunderstanding=misunderstanding,
            pii=pii,
            goal_hint=focus_goal,
            goal_hint_enabled=goal_hint_enabled,
            mascot_directive=mascot_directive,
            revision_note=revision_note,
        )

        try:
            # [snippet:one-call]
            reply = await client.structured(
                stage="generate",
                recorder=recorder,
                system=generation_system,
                user=user_prompt,
                contract=TurnReplyContract,
                attempt_no=attempt,
            )
        except (LlmCallError, LlmContractError):
            candidates.append(
                CandidateRecord(attempt_no=attempt, text="", outcome="ERROR", delivered=False)
            )
            revision_note = "이전 시도가 실패했다. 짧고 안전한 대사로 다시 써라."
            continue

        candidate_text = strip_speaker_prefix(reply.character_line)
        mascot_text = strip_speaker_prefix(reply.mascot_line or "") or None
        # 개입하지 않기로 이미 정한 턴이다. 모델이 굳이 써 보내도 버린다 —
        # 판단 에이전트에게만 맡기면 확률에 기대는 것이고, 이건 결정적으로 막을 수 있다.
        if mascot_mode is MascotMode.NONE:
            mascot_text = None
        # [/snippet:one-call]
        if not candidate_text:
            candidates.append(
                CandidateRecord(attempt_no=attempt, text="", outcome="EMPTY", delivered=False)
            )
            continue

        record = CandidateRecord(attempt_no=attempt, text=candidate_text, mascot_text=mascot_text)
        candidates.append(record)

        # 마스코트가 나서야 하는 턴인데 대사가 비어 있으면 그 자리에 제지가 없다는 뜻이다.
        if mascot_mode is not MascotMode.NONE and not mascot_text:
            record.outcome = "MASCOT_MISSING"
            revision_note = "mascot_line 을 반드시 채워라 — 이번 턴은 마스코트가 나서야 한다."
            continue

        # 이번 턴 질문은 마스코트가 맡았는데 캐릭터도 물었다. 판정에 보내기 전에 되돌린다.
        # 마지막 시도는 막지 않는다 — 질문이 두 개인 대사라도 사전 승인된 고정 문구보다는
        # 낫다는 판단이고, 심판이 마지막 시도를 막지 않는 것과 같은 이유다.
        if not goal_hint_enabled and not is_last and _QUESTION_MARK.search(candidate_text):
            record.outcome = "CHARACTER_STEERS"
            revision_note = (
                "캐릭터 대사에서 질문을 빼라 — 이번 턴 질문은 마스코트가 맡는다. "
                "물음표 없이 아이의 말에 짧게 반응만 해라."
            )
            continue

        # AI 출력도 아동 입력과 같은 안전 검사 대상이다(SAFE-01).
        # 서로 독립적이라 동시에 부른다.
        judge_payload = json.dumps(
            {
                "recentDialogue": history,
                "currentChildInput": child_input,
                "targetGoal": (
                    {
                        "description": focus_goal.description,
                        "requiredEvidence": focus_goal.required_evidence,
                    }
                    if focus_goal
                    else None
                ),
                # 이번 턴 유도를 마스코트가 맡았는지. 맡았다면 캐릭터가 유도하지 않는 게 정상이라
                # 판단 에이전트가 목표 정렬로 감점하면 안 된다 — 실측에서 이것 때문에 후보 3개를
                # 모두 태우고 SAFE_FALLBACK 이 나갔다.
                "mascotSteering": not goal_hint_enabled,
                "candidateText": candidate_text,
                "mascotLine": mascot_text,
                "mascotDuty": mascot_directive,
            },
            ensure_ascii=False,
        )

        # 마스코트 대사도 캐릭터 대사와 같은 검열을 받는다(SAFE-01). 다만 한 콜에 같이 보낸다 —
        # 따로 부르면 왕복이 두 번이고, 재생성까지 겹치면 그 차이가 턴 지연으로 그대로 쌓인다.
        # [snippet:screen-once]
        to_screen = [candidate_text] + ([mascot_text] if mascot_text else [])
        screen_result, judge_result = await asyncio.gather(
            moderation.check_many(to_screen, stage="moderation.output", recorder=recorder),
            client.structured(
                stage="judge",
                recorder=recorder,
                system=judge_system,
                user=judge_payload,
                contract=JudgeContract,
                attempt_no=attempt,
            ),
            return_exceptions=True,
        )

        screened: list[ModerationOutcome] = (
            screen_result
            if isinstance(screen_result, list)
            else [ModerationOutcome(available=False) for _ in to_screen]
        )
        record.moderation = screened[0]
        flagged = any(_blocks_output(outcome) for outcome in screened)
        # [/snippet:screen-once]

        if isinstance(judge_result, (LlmCallError, LlmContractError)):
            # fail closed — 판정을 못 받았으면 통과시키지 않는다.
            record.outcome = "JUDGE_ERROR"
            revision_note = "짧고 안전한 1인칭 대사로 다시 써라."
            continue
        if isinstance(judge_result, BaseException):
            record.outcome = "JUDGE_ERROR"
            continue

        record.judge = _to_outcome(judge_result)

        if flagged:
            record.outcome = "SAFETY_REGENERATE"
            revision_note = (
                "변경할 것: 안전한 표현으로 다시 작성 / 피할 것: 폭력적·위협적·조롱하는 표현"
            )
            continue

        if not (record.judge.safe_to_send and record.judge.decision == "PASS"):
            record.outcome = record.judge.decision or "REGENERATE"
            change = ", ".join(record.judge.revision_change) or "-"
            avoid = ", ".join(record.judge.revision_avoid) or "-"
            revision_note = f"변경할 것: {change} / 피할 것: {avoid}"
            continue

        # 판단은 통과했다. 이제 결정적 재검사.
        if mascot_text and _mascot_leaks_answer(
            mascot_text,
            focus_goal=focus_goal,
            reveal_line=reveal_line,
            answer_reveal_allowed=answer_reveal_allowed,
        ):
            record.outcome = "MASCOT_ANSWER_LEAK"
            revision_note = (
                "마스코트가 정답을 그대로 말했다. 아이가 스스로 말하도록 묻기만 해라."
            )
            continue

        violations = referee.review(
            candidate_text,
            record.judge,
            focus_goal=focus_goal,
            reveal_line=reveal_line,
            recent_character_lines=recent_character_lines,
            mascot_mode=mascot_mode,
        )
        record.referee_violations = violations

        if referee.blocks_delivery(violations, is_last_attempt=is_last):
            record.outcome = "REFEREE_BLOCKED"
            revision_note = referee.revision_note(violations, focus_goal)
            continue

        record.outcome = "PASS"
        record.delivered = True
        return GenerationOutput(candidate_text, mascot_text, False, candidates)

    # 후보를 다 태웠다. 여기서만 사전 검토된 고정 문구로 떨어진다.
    return GenerationOutput(SAFE_FALLBACK_LINE, mascot_fallback_line, True, candidates)
