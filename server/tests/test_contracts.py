"""LLM 구조화 출력 검증 테스트.

여기 있는 케이스는 전부 실제로 겪은 것이다. 특히 래퍼 키 침묵 실패는
"목표 스캐너가 3턴 내내 아무것도 인정하지 않는" 증상으로 나타나서 원인을 찾기 어려웠다.
"""

from __future__ import annotations

import json

from llm.client import _unwrap_envelope, _validate
from llm.contracts import GoalScanContract, IntakeContract, JudgeContract


def test_plain_payload_validates():
    raw = json.dumps({"category": "NORMAL", "reason": "안전한 관찰 발화"}, ensure_ascii=False)
    parsed, detail = _validate(raw, IntakeContract)
    assert parsed is not None, detail
    assert parsed.category == "NORMAL"


def test_unwraps_output_envelope():
    """실측: claude-haiku-4-5 가 {"output": {...}} 로 감싸서 보낸다."""
    raw = json.dumps(
        {"output": {"category": "PROFANITY", "reason": "욕설"}}, ensure_ascii=False
    )
    parsed, detail = _validate(raw, IntakeContract)
    assert parsed is not None, detail
    assert parsed.category == "PROFANITY"


def test_unwraps_parameter_envelope():
    raw = json.dumps(
        {"parameter": {"category": "PII", "reason": "이름 포함", "masked_text": "친구가 울어"}},
        ensure_ascii=False,
    )
    parsed, detail = _validate(raw, IntakeContract)
    assert parsed is not None, detail
    assert parsed.masked_text == "친구가 울어"


def test_wrapped_goal_scan_no_longer_passes_as_empty():
    """이게 이 파일의 존재 이유다.

    GoalScanContract 는 필드가 achieved 하나뿐이고 기본값이 있어서, 예전에는
    래퍼로 감싼 payload 가 "달성된 목표 없음"으로 조용히 통과했다. 스캐너가
    제대로 찾아냈는데도 결과가 통째로 사라졌다.
    """
    raw = json.dumps(
        {
            "output": {
                "achieved": [
                    {
                        "micro_goal_id": "MG-01",
                        "evidence": "친구가 넘어졌어",
                        "why_this_satisfies": "넘어짐을 언급했다",
                    }
                ]
            }
        },
        ensure_ascii=False,
    )
    parsed, detail = _validate(raw, GoalScanContract)
    assert parsed is not None, detail
    assert [c.micro_goal_id for c in parsed.achieved] == ["MG-01"]


def test_unknown_key_in_goal_scan_fails_loudly():
    """언랩으로도 못 살리는 형태면 조용히 빈 결과가 되면 안 된다."""
    raw = json.dumps({"achieved": [], "extra_notes": "..."}, ensure_ascii=False)
    parsed, detail = _validate(raw, GoalScanContract)
    assert parsed is None
    assert "extra_notes" in detail


def test_does_not_unwrap_a_real_single_field_payload():
    """achieved 하나만 담긴 정상 payload 를 래퍼로 오인하면 안 된다."""
    payload = {"achieved": []}
    assert _unwrap_envelope(payload, GoalScanContract) is payload


def test_strips_code_fence():
    raw = '```json\n{"category": "NORMAL", "reason": "ok"}\n```'
    parsed, detail = _validate(raw, IntakeContract)
    assert parsed is not None, detail


def test_reports_readable_detail_on_missing_field():
    raw = json.dumps({"reason": "카테고리가 없다"}, ensure_ascii=False)
    parsed, detail = _validate(raw, IntakeContract)
    assert parsed is None
    assert "category" in detail


def test_judge_contract_accepts_revision_instruction():
    raw = json.dumps(
        {
            "safe_to_send": False,
            "decision": "REGENERATE",
            "failure_codes": ["MULTIPLE_QUESTIONS"],
            "reason": "질문이 두 개다",
            "revision_instruction": {"change": ["질문을 하나로"], "avoid": ["여러 질문"]},
        },
        ensure_ascii=False,
    )
    parsed, detail = _validate(raw, JudgeContract)
    assert parsed is not None, detail
    assert parsed.revision_instruction is not None
    assert parsed.revision_instruction.change == ["질문을 하나로"]


def test_invalid_json_is_reported_not_raised():
    parsed, detail = _validate("이건 JSON이 아니다", IntakeContract)
    assert parsed is None
    assert "JSON 파싱 실패" in detail
