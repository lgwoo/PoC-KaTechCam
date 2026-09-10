"""LLM 이 지켜야 할 JSON 구조.

Elice 엔드포인트는 `response_format={"type":"json_object"}` 를 받아준다(2026-09-09 실측).
다만 strict `json_schema` 는 검증하지 못했으므로 쓰지 않는다 — 대신 프롬프트에 스키마를
싣고 Pydantic 으로 받는 쪽에서 강제한다.

대사 생성도 JSON 으로 받는다. 한 턴에 두 화자(마스코트·캐릭터)가 말하므로 평문으로는
어디까지가 누구 대사인지 가를 수 없다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from state.models import GoalProgress

# HIGH_CONCERN 을 일부러 뺐다. 그 값은 Moderation severity 로 코드가 합성하는 것이지
# 모델이 고르는 값이 아니다. 스키마에 노출하면 모델이 자기 판단으로 쓰기 시작한다.
IntakeCategoryLiteral = Literal["NORMAL", "PROFANITY", "PII", "RISK", "OFF_TOPIC"]


class IntakeContract(BaseModel):
    """인테이크 — 아동 입력의 안전 분류와 상황 신호.

    마스코트 대사는 여기서 받지 않는다. 개입할지 말지는 severity 오버라이드가 끝난
    뒤에야 결정되는데, 인테이크는 그 전에 도는 단계라 무엇을 제지해야 할지 모른 채
    대사를 써야 했다. 대사는 모드가 정해진 뒤 캐릭터 대사와 같은 콜에서 만든다.
    """

    category: IntakeCategoryLiteral = Field(description="아래 다섯 중 하나")
    reason: str = Field(description="그렇게 분류한 이유 한 문장")
    masked_text: str | None = Field(
        default=None, description="category 가 PII 일 때만. 개인정보를 지운 같은 뜻의 문장"
    )
    misunderstanding: bool = Field(
        default=False, description="아이가 시나리오 사실을 잘못 알고 말했는가"
    )
    character_bind: bool = Field(
        default=False,
        description="캐릭터가 1인칭으로 답하면 곤란해지는 발화인가(캐릭터에게 직접 답을 요구 등)",
    )
    goal_progress: GoalProgress = Field(
        default=GoalProgress.NEUTRAL, description="이번 발화가 현재 목표 쪽으로 가는가"
    )
    # HIGH_CONCERN 은 LLM 이 고르지 않는다 — Moderation severity 로 코드가 합성한다.
    # 모델이 굳이 그 값을 뱉으면 검증에서 떨어뜨리는 게 맞다.


class TurnReplyContract(BaseModel):
    """마스코트 대사와 캐릭터 대사를 한 콜에서 함께 받는다.

    나눠 부르면 두 화자가 서로를 모른 채 말한다 — 마스코트가 제지하는 턴에 캐릭터가
    그 사실을 모르고 딴소리를 하거나, 둘 다 질문을 던져 아이가 답을 못 만든다.
    """

    model_config = ConfigDict(extra="forbid")

    mascot_line: str | None = Field(
        default=None, description="마스코트가 할 말 한 문장. 개입할 이유가 없으면 null"
    )
    character_line: str = Field(description="캐릭터가 1인칭으로 할 대사. 한두 문장")


class JudgeContract(BaseModel):
    """응답 판단 에이전트. 워크플로우 문서 §12.4 의 축약판."""

    safe_to_send: bool
    decision: str = Field(description="PASS | REGENERATE | SAFETY_REGENERATE")
    failure_codes: list[str] = Field(default_factory=list)
    reason: str = Field(default="", description="한 문장. 기준별 나열 금지 — 길면 아이가 기다린다")
    revision_instruction: "RevisionInstruction | None" = None


class RevisionInstruction(BaseModel):
    change: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class AchievedClaim(BaseModel):
    micro_goal_id: str
    evidence: str = Field(description="근거가 된 아이 발화 원문 그대로")
    why_this_satisfies: str = Field(
        description="그 발화가 왜 이 목표의 증거 설명을 충족하는지 한 문장"
    )


class GoalScanContract(BaseModel):
    """원인 판단 에이전트 — 아직 안 끝난 목표를 매 턴 전체 대화로 다시 스캔한다.

    extra="forbid" 가 중요하다. 필드가 achieved 하나뿐이고 기본값이 있어서, 모르는 키만
    담긴 payload 도 "빈 결과"로 조용히 통과해 버린다. 실제로 모델이 결과를 래퍼 키로
    감싸 보냈을 때 목표가 하나도 인정되지 않는 침묵 실패가 났다. 이제는 시끄럽게 깨진다.
    """

    model_config = ConfigDict(extra="forbid")

    achieved: list[AchievedClaim] = Field(default_factory=list)


class MicroGoalDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(description="아이가 무엇을 파악하면 되는지 한 문장")
    required_evidence: list[str] = Field(
        description="이 목표를 인정할 근거 설명 1~2개. 아이가 말할 법한 내용을 서술한다"
    )


class PersonaDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="캐릭터 이름 겸 화면 표시명. 짧은 한국어")
    personality: str = Field(description="성격 한 줄")
    speech_style: str = Field(description="말투 한 줄. 아동 화자이므로 반말")


class ScenarioDraftContract(BaseModel):
    """강사가 검토할 시나리오 초안. 아이에게 그대로 노출되는 문장이 섞여 있다.

    scenario_id·version·목표 번호·1인칭 규칙은 코드가 붙인다 — 모델이 정할 값이 아니다.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="시나리오 제목. 상황이 드러나는 짧은 한국어")
    scenario_level: str = Field(description="난이도 L1(단순) / L2 / L3(복합 감정) 중 하나")
    scenario_facts: list[str] = Field(
        description="캐릭터에게 일어난 일 2~4개. 3인칭으로, 감정 해석 없이 사건만 적는다"
    )
    prohibited_inferences: list[str] = Field(
        description="캐릭터가 단정하면 안 되는 것 1~3개. 정답을 미리 못 박는 추론을 막는다"
    )
    micro_goals: list[MicroGoalDraft] = Field(
        description=(
            "아이가 순서대로 밟을 학습 목표 3개. 캐릭터에게 무슨 일이 있었는지 파악 → "
            "캐릭터가 어떤 기분일지 파악 → 둘을 연결. 아이 자신의 감정을 묻지 않는다"
        )
    )
    persona: PersonaDraft
    mascot_intro: str = Field(description="마스코트가 장면을 여는 첫 대사. 3인칭, '멍멍!'으로 시작")
    character_opening_line: str = Field(
        description="캐릭터의 첫 대사. 1인칭, 감정을 말로 설명하지 말고 행동·의성어로 드러낸다"
    )
    opening_prompt_s0: str = Field(description="선택지 없는 개방형 질문")
    opening_prompt_s1: str = Field(description="상황·표정 단서를 담은 질문")
    opening_prompt_s2: str = Field(description="두 개의 선택지를 주는 질문")
    opening_prompt_s3: str = Field(description="정답을 알려주는 문장. 질문이 아니다")


JudgeContract.model_rebuild()


def schema_hint(contract: type[BaseModel]) -> str:
    """프롬프트에 실을 스키마 설명.

    model_json_schema() 원본은 $defs 와 anyOf 로 장황해서 작은 모델이 오히려 헷갈린다.
    필드명·타입·설명만 납작하게 뽑는다.
    """
    schema = contract.model_json_schema()
    defs = schema.get("$defs", {})
    entries = [
        (f'  "{name}": {_type_of(spec, defs)}', _doc_of(spec))
        for name, spec in schema.get("properties", {}).items()
    ]

    lines: list[str] = []
    for index, (body, comment) in enumerate(entries):
        separator = "," if index < len(entries) - 1 else ""
        lines.append(f"{body}{separator}{comment}")
    return "{\n" + "\n".join(lines) + "\n}"


def _type_of(spec: dict, defs: dict) -> str:
    if "enum" in spec:
        return " | ".join(repr(v) for v in spec["enum"])
    if ref := spec.get("$ref"):
        return _type_of(defs.get(ref.rsplit("/", 1)[-1], {}), defs)
    if options := spec.get("anyOf"):
        return " | ".join(_type_of(o, defs) for o in options)
    kind = spec.get("type", "any")
    if kind == "array":
        return f"[{_type_of(spec.get('items', {}), defs)}, ...]"
    if kind == "object" and (props := spec.get("properties")):
        inner = ", ".join(f'"{k}": {_type_of(v, defs)}' for k, v in props.items())
        return "{" + inner + "}"
    return {"string": "string", "boolean": "boolean", "integer": "number", "null": "null"}.get(
        kind, kind
    )


def _doc_of(spec: dict) -> str:
    description = spec.get("description")
    return f"   // {description}" if description else ""
