"""OpenAI Moderation 클라이언트.

**base_url 을 명시적으로 못박는다.** openai SDK 는 baseURL 을 안 주면 환경변수
OPENAI_BASE_URL 을 자동으로 읽는다. 이 프로젝트는 Elice 엔드포인트를 쓰므로,
못박지 않으면 아동 발화가 Moderation 이랍시고 제3자 서버로 나간다.
poc/src/providers/moderation.ts:42-45 가 정확히 이 함정을 막아둔 코드다.

Elice 에는 /v1/moderations 가 없다(404). 그래서 진짜 OpenAI 키가 따로 필요하다.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from llm.client import StageRecorder
from safety.override import normalize_categories, severity_of
from state.models import ModerationOutcome

UNAVAILABLE = ModerationOutcome(available=False)


class ModerationClient:
    def __init__(self, *, api_key: str | None, model: str = "omni-moderation-latest") -> None:
        self._model = model
        self._client = (
            AsyncOpenAI(api_key=api_key, base_url="https://api.openai.com/v1")
            if api_key
            else None
        )

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def model(self) -> str:
        return self._model

    async def check(self, text: str, *, stage: str, recorder: StageRecorder) -> ModerationOutcome:
        """유해성 판정. 호출이 실패해도 예외를 올리지 않는다.

        Moderation 은 방어망의 한 겹이지 유일한 겹이 아니다. 여기서 예외를 던지면
        OpenAI 가 잠깐 죽었을 때 역할극 전체가 멈춘다 — LLM 분류기는 살아 있는데도.
        대신 available=False 로 돌려주고, 호출부가 "확인 못 했음"을 기록한다.
        """
        if self._client is None:
            return UNAVAILABLE

        outcomes = await self.check_many([text], stage=stage, recorder=recorder)
        return outcomes[0]

    async def check_many(
        self, texts: list[str], *, stage: str, recorder: StageRecorder
    ) -> list[ModerationOutcome]:
        """여러 문장을 한 번의 호출로 판정한다.

        Moderation API 는 input 배열을 받고 같은 순서로 results 를 준다. 캐릭터 대사와
        마스코트 대사를 따로 부르면 왕복이 두 번이라 턴이 그만큼 늘어난다 — 한 턴에
        최대 3회 시도하므로 왕복 3번이 통째로 줄어든다.
        """
        if self._client is None:
            return [UNAVAILABLE for _ in texts]

        try:
            with recorder.span(stage):
                response = await self._client.moderations.create(
                    model=self._model, input=texts
                )
        except Exception:
            return [ModerationOutcome(available=False) for _ in texts]

        # [snippet:screen-guard]
        # 순서가 어긋나면 마스코트 판정이 캐릭터 대사에 붙는다. 길이가 다르면 믿지 않는다.
        if len(response.results) != len(texts):
            return [ModerationOutcome(available=False) for _ in texts]

        return [_to_outcome(result) for result in response.results]
        # [/snippet:screen-guard]


def _to_outcome(result) -> ModerationOutcome:
    flagged_raw = [name for name, hit in result.categories.model_dump().items() if hit]
    # SDK 필드명(self_harm)을 API 표기(self-harm)로 되돌린다.
    # 안 하면 CRITICAL 티어 매칭이 조용히 실패한다.
    categories = sorted(set(normalize_categories(flagged_raw)))

    scores_raw = result.category_scores.model_dump()
    scores = {name: float(scores_raw.get(name, 0.0) or 0.0) for name in flagged_raw}

    return ModerationOutcome(
        available=True,
        flagged=bool(result.flagged),
        categories=categories,
        category_scores=scores,
        severity=severity_of(categories),
    )
