"""公告生命周期与采购方式的确定性分类。

生命周期优先读取标题和明确公告标签；采购方式独立识别，二者不得互相覆盖。
"""

from __future__ import annotations

import re
from typing import Any

LIFECYCLE_OPPORTUNITY = "机会公告"
LIFECYCLE_CHANGE = "更正/澄清"
LIFECYCLE_RESULT = "结果公告"
LIFECYCLE_TERMINATED = "终止/废标"
LIFECYCLE_REVIEW = "待复核"

LIFECYCLE_STAGES = {
    LIFECYCLE_OPPORTUNITY,
    LIFECYCLE_CHANGE,
    LIFECYCLE_RESULT,
    LIFECYCLE_TERMINATED,
    LIFECYCLE_REVIEW,
}

_TERMINATED_MARKERS = (
    "终止公告",
    "终止招标",
    "招标终止",
    "采购终止",
    "项目终止",
    "废标公告",
    "废标公示",
    "流标公告",
    "流标公示",
    "采购失败",
    "招标失败",
)
_CHANGE_MARKERS = (
    "更正公告",
    "更正公示",
    "澄清公告",
    "澄清公示",
    "澄清或变更公告",
    "变更公告",
    "变更公示",
    "补充公告",
    "延期公告",
)
_RESULT_MARKERS = (
    "中标(成交)结果公告",
    "中标（成交）结果公告",
    "中标成交结果公告",
    "中标结果公告",
    "成交结果公告",
    "中标公告",
    "成交公告",
    "中标公示",
    "成交公示",
    "结果公告",
    "结果公示",
    "中标候选人公示",
    "中标候选人公告",
)
_OPPORTUNITY_MARKERS = (
    "招标公告",
    "采购公告",
    "询价公告",
    "磋商公告",
    "谈判公告",
    "资格预审公告",
    "单一来源采购公示",
    "单一来源采购公告",
    "征集公告",
    "竞价公告",
)

_PROCUREMENT_METHODS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("公开招标", ("公开招标",)),
    ("邀请招标", ("邀请招标",)),
    ("竞争性磋商", ("竞争性磋商", "磋商采购")),
    ("竞争性谈判", ("竞争性谈判", "谈判采购")),
    ("询价", ("询价采购", "询价公告", "询比采购", "询比价")),
    ("单一来源", ("单一来源",)),
    ("框架协议", ("框架协议", "框架采购")),
    ("电子竞价", ("电子竞价", "竞价采购")),
)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def classify_lifecycle(
    title: str,
    *,
    explicit_label: str | None = None,
    content: str = "",
) -> str:
    """按标题/公告标签优先级识别生命周期。

    正文只作为最后兜底，避免结果公告正文中的历史采购方式覆盖标题语义。
    """

    title_probe = _compact(title)
    label_probe = _compact(explicit_label)
    primary = f"{title_probe} {label_probe}"
    for stage, markers in (
        (LIFECYCLE_TERMINATED, _TERMINATED_MARKERS),
        (LIFECYCLE_CHANGE, _CHANGE_MARKERS),
        (LIFECYCLE_RESULT, _RESULT_MARKERS),
        (LIFECYCLE_OPPORTUNITY, _OPPORTUNITY_MARKERS),
    ):
        if any(marker in primary for marker in markers):
            return stage

    content_probe = _compact(content[:4000])
    for stage, markers in (
        (LIFECYCLE_TERMINATED, _TERMINATED_MARKERS),
        (LIFECYCLE_CHANGE, _CHANGE_MARKERS),
        (LIFECYCLE_RESULT, _RESULT_MARKERS),
        (LIFECYCLE_OPPORTUNITY, _OPPORTUNITY_MARKERS),
    ):
        if any(marker in content_probe for marker in markers):
            return stage
    return LIFECYCLE_REVIEW


def classify_procurement_method(
    title: str,
    *,
    explicit_value: str | None = None,
    content: str = "",
) -> str | None:
    """独立识别采购方式；结果/更正等生命周期词不属于采购方式。"""

    probes = (_compact(explicit_value), _compact(title), _compact(content[:8000]))
    for method, markers in _PROCUREMENT_METHODS:
        if any(marker in probe for probe in probes for marker in markers):
            return method
    if explicit_value and _compact(explicit_value) not in {
        _compact(marker)
        for marker in (*_RESULT_MARKERS, *_CHANGE_MARKERS, *_TERMINATED_MARKERS)
    }:
        return str(explicit_value).strip() or None
    return None


def classify_announcement(
    *, title: str, content: str = "", explicit_type: str | None = None
) -> tuple[str, str | None]:
    return (
        classify_lifecycle(title, explicit_label=explicit_type, content=content),
        classify_procurement_method(
            title, explicit_value=explicit_type, content=content
        ),
    )


def is_opportunity(stage: str | None) -> bool:
    return stage == LIFECYCLE_OPPORTUNITY
