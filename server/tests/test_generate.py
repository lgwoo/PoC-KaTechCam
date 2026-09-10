"""생성 단계의 결정적 후처리 테스트."""

from __future__ import annotations

import pytest

from pipeline.generate import strip_speaker_prefix


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 실측 회귀: 대화 맥락을 "마스코트: ..." 형식으로 주니 캐릭터가 그 패턴을 이어받아
        # 마스코트를 흉내 냈다. 아이 화면에 화자 라벨이 그대로 노출됐다.
        ("마스코트: 어? 잠깐. 뭔가 이상하지 않아?", "어? 잠깐. 뭔가 이상하지 않아?"),
        ("친구: 훌쩍... 아파.", "훌쩍... 아파."),
        ("친구：훌쩍... 아파.", "훌쩍... 아파."),  # 전각 콜론
        ("아이: 나는 슬퍼", "나는 슬퍼"),
        ("  캐릭터:  ...음.  ", "...음."),
        # 라벨이 겹쳐 붙는 경우도 벗겨낸다.
        ("마스코트: 친구: 아야...", "아야..."),
    ],
)
def test_strips_speaker_labels(raw: str, expected: str):
    assert strip_speaker_prefix(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "훌쩍... 무릎이 아파.",
        "(무릎을 감싸며) 아야...",
        # 본문 중간의 콜론은 건드리면 안 된다.
        "음... 그게: 좀 그래.",
        # 화자 이름이 대사 안에 들어간 경우도 남겨야 한다.
        "마스코트가 도와줬어.",
    ],
)
def test_leaves_normal_lines_alone(text: str):
    assert strip_speaker_prefix(text) == text


def test_label_only_becomes_empty():
    """라벨만 남으면 빈 문자열이 되어 호출부가 재생성을 돌린다."""
    assert strip_speaker_prefix("친구:") == ""
