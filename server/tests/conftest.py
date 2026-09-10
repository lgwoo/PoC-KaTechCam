"""LLM 을 타는 경로를 테스트하려면 가짜 클라이언트가 필요하다.

지금까지 tests/ 는 LLM 옆의 순수 함수만 건드렸다. 그래서 turn.py·generate.py 처럼
정작 조합이 일어나는 곳이 테스트 0개였다. 실패 경로(호출 실패, 형식 위반, Moderation
미가동, 후보 3회 소진)는 실제 LLM 으로는 마음대로 만들 수 없으니 여기서 만든다.

세션 생성기도 여기 모은다. 지금 tests/test_support.py, scripts/smoke_turn.py,
api/routes_session.py 세 곳에 따로 있는데, 네 번째를 만들지 않는다.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from llm.client import StageRecorder
from llm.errors import LlmCallError, LlmContractError
from state.models import (
    GoalObservationStatus,
    GoalState,
    MicroGoal,
    ModerationOutcome,
    ModerationSeverity,
    Persona,
    Scenario,
    SessionState,
    Speaker,
    SupportLevel,
    Utterance,
)

# ------------------------------------------------------------ 시나리오/세션
#
# 테스트가 자기 시나리오를 들고 있어야 실제 시나리오 문구가 바뀌어도 흔들리지 않는다.
# (tests/test_support.py 가 같은 이유로 FALL_SCENARIO 를 따로 들고 있다.)

TEST_SCENARIO = Scenario(
    scenario_id="SCN-CONF",
    scenario_version=1,
    title="넘어져서 우는 친구",
    scenario_level="L1",
    scenario_facts=["친구가 뛰다가 넘어졌다", "무릎이 까졌다"],
    prohibited_inferences=["아이가 말하기 전에 캐릭터가 감정을 단정하지 않는다."],
    micro_goals=[
        MicroGoal(id="MG-01", description="무슨 일이 있었는지 알아챈다",
                  required_evidence=["넘어졌다는 것을 언급한다"]),
        MicroGoal(id="MG-02", description="친구의 기분을 말한다",
                  required_evidence=["아프다 또는 속상하다를 말한다"]),
        MicroGoal(id="MG-03", description="사건과 기분을 연결한다",
                  required_evidence=["넘어져서 아프다고 연결한다"]),
    ],
    persona=Persona(
        character_id="FRIEND",
        name="지민",
        personality="조용한 아이",
        speech_style="짧게 말한다",
        first_person_rule="항상 1인칭으로만 말한다",
    ),
    mascot_intro="멍멍! 오늘은 친구 마음을 알아보자.",
    character_opening_line="(무릎을 문지른다) ...아야.",
    opening_prompts={
        SupportLevel.S0: "친구 표정이 어때 보여?",
        SupportLevel.S1: "친구가 방금 뭘 했지?",
        SupportLevel.S2: "친구는 (1) 신났을까 (2) 아플까?",
        SupportLevel.S3: "친구는 넘어져서 무릎이 아프고 속상한 거야.",
    },
)


def make_session(
    scenario: Scenario = TEST_SCENARIO,
    *,
    session_id: str = "S-CONF",
    with_opening: bool = True,
    **goal_status: GoalObservationStatus,
) -> SessionState:
    """라우트가 하는 것과 같은 순서로 세션을 만든다 — 마스코트 소개 다음 캐릭터 첫 대사.
    첫 대사가 없으면 앵무새 관문과 반복 판정이 볼 게 없어져서 테스트가 실제와 달라진다."""
    session = SessionState(
        session_id=session_id,
        scenario_id=scenario.scenario_id,
        goals={
            goal.id: GoalState(
                goal_id=goal.id,
                status=goal_status.get(goal.id, GoalObservationStatus.NOT_OBSERVED),
            )
            for goal in scenario.micro_goals
        },
    )
    if with_opening:
        session.transcript.append(
            Utterance(speaker=Speaker.MASCOT, text=scenario.mascot_intro, turn_index=0)
        )
        session.transcript.append(
            Utterance(
                speaker=Speaker.CHARACTER,
                text=scenario.character_opening_line,
                turn_index=0,
            )
        )
    return session


# ------------------------------------------------------------------ 가짜 LLM


class FakeLlmClient:
    """단계 이름별로 응답을 큐에 넣어 두고 하나씩 꺼내 준다.

    큐에 넣을 수 있는 것:
      - contract 인스턴스 → 그대로 돌려준다
      - Exception 인스턴스 → raise 한다(LlmCallError/LlmContractError 로 실패 경로를 만든다)
      - 호출 가능 객체 → 넘어온 kwargs 를 주고 그 결과를 쓴다

    실제 LlmClient 는 형식 위반을 만나면 스스로 한 번 수리 재시도를 하고 그때
    '<단계>.repair' 스팬을 남긴다. 여기서는 그 안쪽을 흉내내지 않는다 — 수리 동작은
    tests/test_recorder.py 가 진짜 _validate 로 따로 본다.
    """

    def __init__(self, responses: dict[str, list[Any]] | None = None,
                 *, model: str = "fake-model") -> None:
        self._queues = {
            stage: deque(items) for stage, items in (responses or {}).items()
        }
        self._model = model
        self.calls: list[dict] = []

    @property
    def model(self) -> str:
        return self._model

    def _next(self, stage: str) -> Any:
        queue = self._queues.get(stage)
        if not queue:
            raise AssertionError(
                f"'{stage}' 단계에 준비된 응답이 없다. FakeLlmClient 에 넣어라."
            )
        item = queue.popleft() if len(queue) > 1 else queue[0]
        return item

    async def structured(self, *, stage: str, recorder: StageRecorder, system: str,
                         user: str, contract: Any, attempt_no: int | None = None) -> Any:
        self.calls.append(
            {"stage": stage, "system": system, "user": user,
             "contract": contract, "attempt_no": attempt_no}
        )
        item = self._next(stage)
        if callable(item) and not isinstance(item, BaseException):
            item = item(stage=stage, system=system, user=user, attempt_no=attempt_no)

        # 실제 클라이언트는 예외를 던지기 전에도 스팬을 남긴다(ok=False). 여기도 그렇게 한다.
        if isinstance(item, BaseException):
            try:
                with recorder.span(stage, attempt_no):
                    raise item
            except BaseException:
                raise
        with recorder.span(stage, attempt_no):
            pass
        return item

    async def text(self, *, stage: str, recorder: StageRecorder, system: str,
                   user: str, attempt_no: int | None = None) -> str:
        with recorder.span(stage, attempt_no):
            pass
        return str(self._next(stage))

    def stages_called(self) -> list[str]:
        return [call["stage"] for call in self.calls]

    def prompt_for(self, stage: str) -> str:
        """그 단계에 실제로 들어간 사용자 프롬프트. 안전 지침이 붙었는지 볼 때 쓴다."""
        for call in self.calls:
            if call["stage"] == stage:
                return call["user"]
        raise AssertionError(f"'{stage}' 단계는 호출되지 않았다")


def call_error(stage: str = "x") -> LlmCallError:
    """응답 자체가 안 온 경우 — 네트워크·인증·5xx."""
    return LlmCallError(stage, RuntimeError("연결 실패"))


def contract_error(stage: str = "x") -> LlmContractError:
    """응답은 왔지만 형식을 못 맞춘 경우 — 수리 재시도까지 실패한 뒤 나온다."""
    return LlmContractError(stage, '{"nope": 1}', "category: field required")


# ----------------------------------------------------------- 가짜 Moderation


class FakeModerationClient:
    """결과를 미리 정해 두거나, 예외를 던지게 하거나, 아예 미가동으로 둘 수 있다.

    미가동(available=False)은 severity 가 NONE 이라서 위험 발화를 못 잡는다.
    실제 클라이언트가 키 없이 뜰 때와 같은 상태 — 방어망 한 겹이 빠진 실행이다.
    """

    def __init__(self, *, outcomes: list[ModerationOutcome] | None = None,
                 available: bool = True, raises: BaseException | None = None,
                 model: str | None = "fake-moderation") -> None:
        self._queue = deque(outcomes or [])
        self._available = available
        self._raises = raises
        self._model = model
        self.checked: list[tuple[str, list[str]]] = []

    @property
    def available(self) -> bool:
        return self._available

    @property
    def model(self) -> str | None:
        return self._model if self._available else None

    def _one(self) -> ModerationOutcome:
        if not self._available:
            return ModerationOutcome(available=False)
        if self._queue:
            return self._queue.popleft() if len(self._queue) > 1 else self._queue[0]
        return ModerationOutcome(available=True, flagged=False,
                                 severity=ModerationSeverity.NONE)

    async def check(self, text: str, *, stage: str,
                    recorder: StageRecorder) -> ModerationOutcome:
        return (await self.check_many([text], stage=stage, recorder=recorder))[0]

    async def check_many(self, texts: list[str], *, stage: str,
                         recorder: StageRecorder) -> list[ModerationOutcome]:
        self.checked.append((stage, list(texts)))
        # 실제 클라이언트는 절대 raise 하지 않는다 — 예외를 삼키고 available=False 를
        # 돌려준다. 그래서 raises 를 줘도 스팬만 ok=False 로 남기고 그 값을 돌려준다.
        if self._raises is not None:
            try:
                with recorder.span(stage):
                    raise self._raises
            except BaseException:
                pass
            return [ModerationOutcome(available=False) for _ in texts]
        with recorder.span(stage):
            pass
        return [self._one() for _ in texts]


def flagged(*categories: str, severity: ModerationSeverity) -> ModerationOutcome:
    return ModerationOutcome(
        available=True,
        flagged=True,
        categories=sorted(categories),
        category_scores={c: 0.9 for c in categories},
        severity=severity,
    )


CLEAN = ModerationOutcome(available=True, flagged=False, severity=ModerationSeverity.NONE)
