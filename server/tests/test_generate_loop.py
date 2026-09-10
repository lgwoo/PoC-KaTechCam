"""후보 생성 → 검열 → 판정 → 심판 루프.

아이에게 실제로 나가는 문장이 여기서 정해진다. 지금까지 이 파일이 없어서
strip_speaker_prefix 하나만 테스트돼 있었다 — 정작 "무엇을 안 내보내는가"를 정하는
열 개의 탈출 경로는 전부 비어 있었다.

핵심은 두 가지다. 판정을 못 받았으면 통과시키지 않는다(fail closed). 그리고 3회를
다 태우면 사전 검토된 고정 문구로 내려간다 — 마지막 시도는 심판이 막지 않는다.
"""

from __future__ import annotations

from llm.client import StageRecorder
from llm.contracts import JudgeContract, RevisionInstruction, TurnReplyContract
from pipeline import generate as gen
from state.models import MascotMode, ModerationSeverity
from tests.conftest import (
    CLEAN,
    FakeLlmClient,
    FakeModerationClient,
    TEST_SCENARIO,
    call_error,
    contract_error,
    flagged,
)

GOAL = TEST_SCENARIO.micro_goals[0]
REVEAL = TEST_SCENARIO.opening_prompts[
    next(k for k in TEST_SCENARIO.opening_prompts if k.value == "S3")
]

SAFE_LINE = "아야... 무릎이 좀 그래."


def reply(character: str = SAFE_LINE, mascot: str | None = None) -> TurnReplyContract:
    return TurnReplyContract(character_line=character, mascot_line=mascot)


def verdict(*, decision: str = "PASS", safe: bool = True,
            codes: list[str] | None = None,
            change: list[str] | None = None,
            avoid: list[str] | None = None) -> JudgeContract:
    return JudgeContract(
        safe_to_send=safe,
        decision=decision,
        failure_codes=codes or [],
        reason="테스트",
        revision_instruction=(
            RevisionInstruction(change=change or [], avoid=avoid or [])
            if (change or avoid)
            else None
        ),
    )


async def run(*, generates: list[object], judges: list[object] | None = None,
              moderation: FakeModerationClient | None = None,
              mascot_mode: MascotMode = MascotMode.NONE,
              recent_character_lines: list[str] | None = None,
              focus_goal=GOAL, answer_reveal_allowed: bool = False,
              **flags: object):
    """(결과, 클라이언트) 를 돌려준다 — 프롬프트에 무엇이 들어갔는지도 봐야 한다."""
    client = FakeLlmClient({
        "generate": generates,
        "judge": judges or [verdict()],
    })
    output = await gen.generate_approved_reply(
        client=client,
        moderation=moderation or FakeModerationClient(outcomes=[CLEAN]),
        recorder=StageRecorder(),
        scenario=TEST_SCENARIO,
        history="마스코트: 오늘은 친구 마음을 알아보자.\n친구: (무릎을 문지른다) ...아야.",
        child_input="친구가 넘어졌어",
        focus_goal=focus_goal,
        reveal_line=REVEAL,
        recent_character_lines=recent_character_lines or [],
        mascot_mode=mascot_mode,
        mascot_directive="이번 턴에는 마스코트가 나서지 않는다.",
        mascot_fallback_line="(마스코트가 살짝 끼어든다) 다시 물어볼까?",
        answer_reveal_allowed=answer_reveal_allowed,
        **flags,
    )
    return output, client


def outcomes(output) -> list[str]:
    return [c.outcome for c in output.candidates]


def prompts_for(client: FakeLlmClient, stage: str) -> list[str]:
    return [call["user"] for call in client.calls if call["stage"] == stage]


# ----------------------------------------------------------------- 통과


async def test_pass_delivers_the_first_candidate() -> None:
    output, client = await run(generates=[reply()])

    assert output.text == SAFE_LINE
    assert output.fallback_used is False
    assert outcomes(output) == ["PASS"]
    assert output.candidates[0].delivered is True
    assert len(prompts_for(client, "generate")) == 1


async def test_speaker_label_is_stripped_before_delivery() -> None:
    """화자 이름이 붙은 대사가 아이에게 그대로 나간 적이 있다(session_report.md 에 남아 있다)."""
    output, _ = await run(generates=[reply("친구: 아야, 무릎이 아파.")])
    assert output.text == "아야, 무릎이 아파."


# --------------------------------------------------------- 생성이 실패


async def test_generate_call_failure_becomes_error_and_retries() -> None:
    output, client = await run(generates=[call_error("generate")])

    assert outcomes(output) == ["ERROR", "ERROR", "ERROR"]
    assert output.fallback_used is True
    assert output.text == gen.SAFE_FALLBACK_LINE
    assert len(prompts_for(client, "generate")) == 3


async def test_generate_contract_failure_is_the_same_path() -> None:
    output, _ = await run(generates=[contract_error("generate"), reply()])
    assert outcomes(output) == ["ERROR", "PASS"]


async def test_error_leaves_a_revision_note_for_the_next_attempt() -> None:
    _, client = await run(generates=[call_error("generate"), reply()])
    second = prompts_for(client, "generate")[1]
    assert "짧고 안전한 대사로 다시 써라" in second


async def test_empty_line_is_rejected() -> None:
    """화자 라벨만 보내오면 벗기고 나면 빈 문자열이다. 빈 대사가 나가면 아이는
    친구가 아무 말도 안 한 것으로 본다."""
    output, _ = await run(generates=[reply("마스코트:"), reply()])
    assert outcomes(output) == ["EMPTY", "PASS"]


async def test_empty_does_not_leave_a_revision_note() -> None:
    """지금 동작을 그대로 적어 둔다 — EMPTY 는 수정 지시를 남기지 않아서
    다음 시도가 무엇이 문제였는지 모른 채 같은 실수를 반복할 수 있다."""
    _, client = await run(generates=[reply("마스코트:"), reply()])
    second = prompts_for(client, "generate")[1]
    assert "[이전 후보 수정 지시]" not in second


# --------------------------------------------------------- 마스코트 대사


async def test_mascot_line_is_discarded_when_the_mascot_stays_out() -> None:
    """개입하지 않기로 이미 정한 턴이다. 판단 에이전트에 맡기면 확률에 기대는 것이고,
    여기서는 결정적으로 버릴 수 있다."""
    output, _ = await run(
        generates=[reply(mascot="굳이 끼어든 말")], mascot_mode=MascotMode.NONE
    )

    assert output.mascot_line is None
    assert outcomes(output) == ["PASS"]


async def test_missing_mascot_line_is_rejected_when_it_must_speak() -> None:
    """마스코트가 나서야 하는 턴에 대사가 비면 그 자리에 제지가 없다는 뜻이다."""
    output, client = await run(
        generates=[reply(mascot=None), reply(mascot="얘들아, 그런 말은 마음을 아프게 해.")],
        mascot_mode=MascotMode.CORRECT,
    )

    assert outcomes(output) == ["MASCOT_MISSING", "PASS"]
    assert "mascot_line 을 반드시 채워라" in prompts_for(client, "generate")[1]


async def test_mascot_that_says_the_answer_is_rejected() -> None:
    """S3 정답 공개가 허용된 턴이 아니면, 마스코트가 답을 그대로 말해서는 안 된다 —
    아이가 스스로 말할 기회를 없애면 달성 지표가 허구가 된다."""
    output, client = await run(
        generates=[reply(mascot=REVEAL), reply(mascot="친구 표정이 어때 보여?")],
        mascot_mode=MascotMode.SCAFFOLD,
    )

    assert outcomes(output) == ["MASCOT_ANSWER_LEAK", "PASS"]
    assert "아이가 스스로 말하도록" in prompts_for(client, "generate")[1]


async def test_answer_reveal_turn_allows_the_same_line() -> None:
    """S3 은 정답을 알려 주는 턴이다. 여기서까지 막으면 사다리의 마지막 칸이 죽는다."""
    output, _ = await run(
        generates=[reply(mascot=REVEAL)],
        mascot_mode=MascotMode.SCAFFOLD,
        answer_reveal_allowed=True,
    )

    assert outcomes(output) == ["PASS"]
    assert output.mascot_line == REVEAL


# ------------------------------------------------------------- 판정 실패


async def test_judge_failure_never_delivers() -> None:
    """fail closed. 판정을 못 받은 문장을 내보내면 검열을 통과한 적이 없는 대사가 나간다."""
    output, _ = await run(generates=[reply()], judges=[call_error("judge")])

    assert outcomes(output) == ["JUDGE_ERROR"] * 3
    assert output.fallback_used is True
    assert all(not c.delivered for c in output.candidates)


async def test_judge_contract_failure_is_also_fail_closed() -> None:
    output, _ = await run(generates=[reply()], judges=[contract_error("judge")])
    assert outcomes(output) == ["JUDGE_ERROR"] * 3


async def test_unexpected_judge_exception_leaves_no_revision_note() -> None:
    """LlmError 계열이 아닌 예외는 수정 지시 없이 넘어간다 — 지금 동작이 그렇다."""
    output, client = await run(
        generates=[reply(), reply()], judges=[RuntimeError("생각 못 한 것"), verdict()]
    )

    assert outcomes(output)[0] == "JUDGE_ERROR"
    assert "[이전 후보 수정 지시]" not in prompts_for(client, "generate")[1]


async def test_judge_moderation_outcome_is_kept_even_on_judge_error() -> None:
    """검열 결과는 판정 실패보다 먼저 기록된다. 판정이 죽어도 검열 결과는 남는다."""
    output, _ = await run(
        generates=[reply()],
        judges=[call_error("judge")],
        moderation=FakeModerationClient(outcomes=[CLEAN]),
    )

    assert output.candidates[0].moderation is not None
    assert output.candidates[0].moderation.available is True


# --------------------------------------------------------------- 재생성


async def test_regenerate_uses_the_judge_decision_as_the_outcome() -> None:
    output, _ = await run(
        generates=[reply(), reply()],
        judges=[verdict(decision="REGENERATE", safe=False), verdict()],
    )

    assert outcomes(output) == ["REGENERATE", "PASS"]


async def test_pass_decision_with_unsafe_flag_is_not_delivered() -> None:
    """decision 은 자유 문자열이다. safe_to_send 와 어긋나면 통과시키지 않는다."""
    output, _ = await run(
        generates=[reply(), reply()],
        judges=[verdict(decision="PASS", safe=False), verdict()],
    )

    assert output.candidates[0].delivered is False
    assert outcomes(output)[1] == "PASS"


async def test_revision_instruction_reaches_the_next_prompt() -> None:
    _, client = await run(
        generates=[reply(), reply()],
        judges=[
            verdict(decision="REGENERATE", safe=False,
                    change=["더 짧게"], avoid=["정답 말하기"]),
            verdict(),
        ],
    )

    second = prompts_for(client, "generate")[1]
    assert "더 짧게" in second
    assert "정답 말하기" in second


async def test_empty_revision_instruction_becomes_a_dash() -> None:
    """지시가 없어도 프롬프트가 깨지지 않아야 한다."""
    _, client = await run(
        generates=[reply(), reply()],
        judges=[verdict(decision="REGENERATE", safe=False), verdict()],
    )
    assert "-" in prompts_for(client, "generate")[1]


# ---------------------------------------------------------- 출력 검열


async def test_high_severity_output_is_regenerated() -> None:
    output, client = await run(
        generates=[reply(), reply()],
        moderation=FakeModerationClient(
            outcomes=[
                flagged("violence/graphic", severity=ModerationSeverity.HIGH),
                CLEAN,
            ]
        ),
    )

    assert outcomes(output) == ["SAFETY_REGENERATE", "PASS"]
    assert "안전한 표현으로 다시 작성" in prompts_for(client, "generate")[1]


async def test_moderate_output_is_delivered() -> None:
    """넘어지고 다치는 이야기는 정상적으로 violence 를 건드린다. 여기서 막으면
    시나리오 절반이 기본 응답으로 떨어진다."""
    output, _ = await run(
        generates=[reply()],
        moderation=FakeModerationClient(
            outcomes=[flagged("violence", severity=ModerationSeverity.MODERATE)]
        ),
    )

    assert outcomes(output) == ["PASS"]


async def test_output_screening_failure_fails_open() -> None:
    """출력 검열은 실패하면 열린다 — 판정 에이전트가 이미 한 겹 봤기 때문이다.
    입력 검열과 방향이 반대라서 명시해 둔다."""
    output, _ = await run(
        generates=[reply()],
        moderation=FakeModerationClient(raises=RuntimeError("OpenAI 죽음")),
    )

    assert outcomes(output) == ["PASS"]
    assert output.candidates[0].moderation.available is False


async def test_mascot_line_is_screened_together_with_the_character_line() -> None:
    output, _ = await run(
        generates=[reply(mascot="같이 물어볼까?")],
        mascot_mode=MascotMode.SCAFFOLD,
        focus_goal=None,
    )
    assert outcomes(output) == ["PASS"]


async def test_two_lines_go_to_moderation_in_one_call() -> None:
    moderation = FakeModerationClient(outcomes=[CLEAN])
    await run(
        generates=[reply(mascot="같이 물어볼까?")],
        moderation=moderation,
        mascot_mode=MascotMode.SCAFFOLD,
        focus_goal=None,
    )

    stage, texts = moderation.checked[0]
    assert stage == "moderation.output"
    assert len(texts) == 2


# ----------------------------------------------------------------- 심판


async def test_referee_blocks_a_self_contradicting_pass() -> None:
    """판정이 PASS 라면서 실패 코드를 같이 달아 보내는 일이 있다. 그걸 그대로
    믿으면 판정기가 스스로 걸러낸 문장이 나간다."""
    output, client = await run(
        generates=[reply(), reply()],
        judges=[verdict(codes=["DIALOGUE_FLOW"]), verdict()],
    )

    assert outcomes(output) == ["REFEREE_BLOCKED", "PASS"]
    assert "JUDGE_INCOHERENT" in output.candidates[0].referee_violations
    assert "안전하고 짧은 1인칭 대사로 다시 쓸 것" in prompts_for(client, "generate")[1]


async def test_repetition_is_blocked() -> None:
    output, _ = await run(
        generates=[reply(SAFE_LINE), reply("무릎에 반창고 붙여야 할까?")],
        recent_character_lines=[SAFE_LINE],
    )

    assert outcomes(output) == ["REFEREE_BLOCKED", "PASS"]


async def test_last_attempt_is_never_blocked_by_the_referee() -> None:
    """3회를 다 태우고 기본 응답으로 내려가는 것보다, 심판이 흠을 잡은 대사라도
    보내는 게 낫다는 판단이다. 위반은 그대로 기록에 남는다."""
    output, _ = await run(
        generates=[reply(SAFE_LINE)],
        recent_character_lines=[SAFE_LINE],
    )

    assert outcomes(output) == ["REFEREE_BLOCKED", "REFEREE_BLOCKED", "PASS"]
    assert output.candidates[-1].delivered is True
    assert output.candidates[-1].referee_violations != []


async def test_answer_leak_in_the_character_line_is_blocked() -> None:
    output, _ = await run(
        generates=[reply(REVEAL), reply()],
    )

    assert outcomes(output)[0] == "REFEREE_BLOCKED"
    assert "ANSWER_LEAK" in output.candidates[0].referee_violations


async def test_no_steer_alone_does_not_block() -> None:
    """질문이 없다는 건 흠이지만 안전 문제가 아니다. 이걸로 후보를 태우면
    3회 예산이 금방 바닥난다."""
    output, _ = await run(generates=[reply("응.")])

    assert outcomes(output) == ["PASS"]
    assert "NO_STEER" in output.candidates[0].referee_violations


# ------------------------------------------------------------ 기본 응답


async def test_fallback_carries_the_preapproved_mascot_line() -> None:
    """대사가 기본 응답으로 내려가도 마스코트 자리는 비우지 않는다."""
    output, _ = await run(
        generates=[reply()], judges=[call_error("judge")], mascot_mode=MascotMode.CORRECT
    )

    assert output.fallback_used is True
    assert output.text == gen.SAFE_FALLBACK_LINE
    assert output.mascot_line == "(마스코트가 살짝 끼어든다) 다시 물어볼까?"


async def test_all_three_candidates_are_kept_for_the_record() -> None:
    """무엇을 왜 안 보냈는지가 리포트의 핵심이다. 버리면 재현이 불가능해진다."""
    output, _ = await run(generates=[reply()], judges=[call_error("judge")])
    assert [c.attempt_no for c in output.candidates] == [1, 2, 3]


async def test_max_candidates_is_configurable_for_tests() -> None:
    output, client = await run(generates=[call_error("generate")], max_candidates=1)

    assert outcomes(output) == ["ERROR"]
    assert len(prompts_for(client, "generate")) == 1


# ------------------------------------------------------------ 안전 지침


async def test_every_safety_flag_adds_its_own_block() -> None:
    """여섯 지침은 동시에 걸릴 수 있다. 하나가 다른 것을 덮으면 그 방어가 사라진다."""
    _, client = await run(
        generates=[reply(mascot="그런 말은 마음을 아프게 해.")],
        mascot_mode=MascotMode.CORRECT,
        profanity=True,
        off_topic=True,
        high_concern=True,
        character_bind=True,
        misunderstanding=True,
        pii=True,
    )

    prompt = prompts_for(client, "generate")[0]
    assert prompt.count("[안전 지침]") == 6


async def test_goal_hint_disabled_hands_the_question_to_the_mascot() -> None:
    _, client = await run(
        generates=[reply(mascot="친구 표정이 어때 보여?")],
        mascot_mode=MascotMode.SCAFFOLD,
        goal_hint_enabled=False,
    )

    assert "마스코트" in prompts_for(client, "generate")[0]


async def test_stage_timings_cover_every_attempt() -> None:
    """리포트에서 '어느 시도가 느렸나'를 보려면 시도마다 기록이 있어야 한다."""
    client = FakeLlmClient({
        "generate": [reply(), reply(), reply()],
        "judge": [verdict(decision="REGENERATE", safe=False),
                  verdict(decision="REGENERATE", safe=False),
                  verdict()],
    })
    recorder = StageRecorder()

    await gen.generate_approved_reply(
        client=client,
        moderation=FakeModerationClient(outcomes=[CLEAN]),
        recorder=recorder,
        scenario=TEST_SCENARIO,
        history="",
        child_input="친구가 넘어졌어",
        focus_goal=GOAL,
        reveal_line=REVEAL,
        recent_character_lines=[],
        mascot_mode=MascotMode.NONE,
        mascot_directive="",
        mascot_fallback_line=None,
    )

    attempts = sorted(t.attempt_no for t in recorder.timings if t.stage == "generate")
    assert attempts == [1, 2, 3]
    assert sum(1 for t in recorder.timings if t.stage == "moderation.output") == 3


# ------------------------------------------- 질문 소유권 (결정적 차단)


async def test_character_question_is_blocked_when_the_mascot_owns_the_question() -> None:
    """마스코트가 유도를 맡은 턴에 캐릭터까지 물으면 한 턴에 질문이 두 개다.
    판정 에이전트도 잡지만 그건 확률이다 — 프롬프트를 두 번 조여도 6건에서 4건까지만
    줄었다. 물음표는 결정적으로 볼 수 있으니 여기서 막는다."""
    output, client = await run(
        generates=[reply("음... 부끄러웠나?", mascot="친구 표정이 어때 보여?"), reply("응... 좀 그랬어.", mascot="친구 표정이 어때 보여?")],
        judges=[verdict()],
        mascot_mode=MascotMode.SCAFFOLD,
        goal_hint_enabled=False,
    )

    assert outcomes(output) == ["CHARACTER_STEERS", "PASS"]
    assert output.text == "응... 좀 그랬어."


async def test_blocked_before_the_judge_round_trip() -> None:
    """판정은 가장 느린 단계다(실측 p50 4.3초). 어차피 되돌릴 후보에 그 왕복을 쓰지 않는다."""
    output, client = await run(
        generates=[reply("그랬어?", mascot="친구 표정이 어때 보여?"), reply("...맞아.", mascot="친구 표정이 어때 보여?")],
        judges=[verdict()],
        mascot_mode=MascotMode.SCAFFOLD,
        goal_hint_enabled=False,
    )

    assert outcomes(output) == ["CHARACTER_STEERS", "PASS"]
    # 첫 시도는 판정을 부르지 않았다 — judge 호출이 한 번뿐이다.
    assert len(prompts_for(client, "judge")) == 1


async def test_revision_note_tells_the_model_to_drop_the_question() -> None:
    _, client = await run(
        generates=[reply("부끄러웠나?", mascot="친구 표정이 어때 보여?"), reply("응.", mascot="친구 표정이 어때 보여?")],
        mascot_mode=MascotMode.SCAFFOLD,
        goal_hint_enabled=False,
    )

    assert "질문을 빼라" in prompts_for(client, "generate")[1]


async def test_last_attempt_is_not_blocked() -> None:
    """마지막 시도까지 막으면 사전 승인된 고정 문구로 떨어진다. 질문이 두 개인 대사라도
    아이에게는 그게 낫다 — 심판이 마지막 시도를 막지 않는 것과 같은 판단이다."""
    output, _ = await run(
        generates=[reply("그랬어?", mascot="친구 표정이 어때 보여?")],
        mascot_mode=MascotMode.SCAFFOLD,
        goal_hint_enabled=False,
    )

    assert outcomes(output) == ["CHARACTER_STEERS", "CHARACTER_STEERS", "PASS"]
    assert output.fallback_used is False
    assert output.text == "그랬어?"


async def test_question_is_fine_when_the_character_owns_it() -> None:
    """마스코트가 안 나서는 턴에는 캐릭터가 되물어야 대화가 이어진다."""
    output, _ = await run(
        generates=[reply("너는 어땠어?")], mascot_mode=MascotMode.NONE
    )

    assert outcomes(output) == ["PASS"]


async def test_fullwidth_question_mark_counts() -> None:
    output, _ = await run(
        generates=[reply("그랬어？", mascot="친구 표정이 어때 보여?"), reply("응.", mascot="친구 표정이 어때 보여?")],
        mascot_mode=MascotMode.SCAFFOLD,
        goal_hint_enabled=False,
    )
    assert outcomes(output)[0] == "CHARACTER_STEERS"


async def test_plain_statement_is_not_mistaken_for_a_question() -> None:
    """referee._STEERING 같은 느슨한 패턴을 쓰면 '그랬으니까' 를 질문으로 잡아
    후보 예산을 태운다. 여기서는 거짓 양성이 곧 기본 응답이다."""
    output, _ = await run(
        generates=[reply("그랬으니까 좀 속상했어. 나도 그랬을 거래.", mascot="친구 표정이 어때 보여?")],
        mascot_mode=MascotMode.SCAFFOLD,
        goal_hint_enabled=False,
    )

    assert outcomes(output) == ["PASS"]
