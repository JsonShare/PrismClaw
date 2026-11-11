"""T1-5 验收（SDD D9）：curator 毕业双路径裁决逻辑。"""

from prismclaw.persistence.curator.repository import _graduation_candidates

_AGED = [("sop-a", "内容A"), ("sop-b", "内容B"), ("sop-c", "内容C")]


def test_use_count_path_graduates() -> None:
    """use_count 路径命中（归因开启时）即可毕业。"""
    assert _graduation_candidates(_AGED, {"sop-a"}, {}) == [("sop-a", "内容A")]


def test_validation_signal_path_graduates_without_attribution() -> None:
    """验证信号路径：positive≥2 且 negative==0，不依赖 use_count。"""
    counts = {"sop-b": {"positive": 3, "negative": 0}}
    assert _graduation_candidates(_AGED, set(), counts) == [("sop-b", "内容B")]


def test_negative_signal_blocks_graduation() -> None:
    """存在反证（negative>0）即使 positive 达标也不毕业。"""
    counts = {"sop-a": {"positive": 4, "negative": 1}}
    assert _graduation_candidates(_AGED, set(), counts) == []


def test_below_threshold_fails() -> None:
    """positive 只有 1 次不足阈值。"""
    counts = {"sop-a": {"positive": 1, "negative": 0}}
    assert _graduation_candidates(_AGED, set(), counts) == []


def test_both_paths_merge() -> None:
    counts = {"sop-c": {"positive": 2, "negative": 0}}
    result = _graduation_candidates(_AGED, {"sop-a"}, counts)
    assert {(entry_id, content) for entry_id, content in result} == {
        ("sop-a", "内容A"),
        ("sop-c", "内容C"),
    }