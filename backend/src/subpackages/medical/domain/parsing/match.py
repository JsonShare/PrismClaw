"""患者归属匹配——把抽取出的身份对上档案里的某个人。

判定顺序：姓名精确 → 加性别 → 加年龄。任一步能收敛到唯一就归档，
收敛不了（重名、档案里没有）就交回给医生，绝不猜。

纯函数：只吃候选列表和身份字段，不碰数据库。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

# 匹配到同名但无法收敛时，把候选也带回去，UI 可以直接提示
MAX_CANDIDATES = 5

_NAME_NOISE = re.compile(r"[\s·・.,，、\-_()（）【】\[\]]+")

# 脱敏姓名模式：单姓+先生/女士（如"李女士""孟先生"）
# 这类姓名来自脱敏数据，不应与档案中的真实姓名匹配
_DESENSITIZED_PAT = re.compile(r"^[\u4e00-\u9fa5]{1,2}(先生|女士)$")


def normalize_name(value: str) -> str:
    """姓名归一：去空白与常见分隔符，全角转半角。"""
    text = str(value or "").strip()
    text = text.translate(str.maketrans("０１２３４５６７８９Ａ-Ｚａ-ｚ", "0123456789A-Za-z"))
    return _NAME_NOISE.sub("", text)


def age_from_birth(birth: str, *, today: date | None = None) -> int | None:
    """出生日期字符串 → 周岁。解析不出返回 None。"""
    text = str(birth or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y-%m", "%Y/%m", "%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        base = today or date.today()
        years = base.year - parsed.year
        if (base.month, base.day) < (parsed.month, parsed.day):
            years -= 1
        return years
    return None


def match_patient(
    candidates: list[dict[str, Any]],
    *,
    name: str,
    gender: str = "",
    age: str = "",
) -> dict[str, Any]:
    """在候选档案里找出唯一归属。

    返回 dict：
    - patient_id: 命中时非空；未命中为 ""
    - reason    : exact / exact_gender / exact_age / anonymous / no_match / ambiguous
    - candidates: 歧义或重名时的候选摘要（最多 MAX_CANDIDATES 条）
    """
    target = normalize_name(name)
    if not target:
        return {"patient_id": "", "reason": "anonymous", "candidates": []}

    # 脱敏姓名（"李女士""孟先生"等）：不能与真实姓名匹配，直接交人工
    if _DESENSITIZED_PAT.match(target):
        return {"patient_id": "", "reason": "desensitized", "candidates": []}

    same_name = [
        c for c in candidates if normalize_name(str(c.get("name") or "")) == target
    ]
    if not same_name:
        return {"patient_id": "", "reason": "no_match", "candidates": []}
    if len(same_name) == 1:
        return {"patient_id": str(same_name[0].get("id") or ""), "reason": "exact", "candidates": []}

    # 重名：先用性别收敛
    if gender and gender[0] in ("男", "女"):
        by_gender = [
            c for c in same_name if str(c.get("gender") or "").startswith(gender[0])
        ]
        if len(by_gender) == 1:
            return {
                "patient_id": str(by_gender[0].get("id") or ""),
                "reason": "exact_gender",
                "candidates": [],
            }
        if by_gender:
            same_name = by_gender

    # 再用年龄收敛（±2 岁容忍 OCR 与记忆误差）
    age_int = int(age) if str(age or "").isdigit() else None
    if age_int is not None:
        by_age = [
            c
            for c in same_name
            if (a := age_from_birth(str(c.get("birth_date") or ""))) is not None
            and abs(a - age_int) <= 2
        ]
        if len(by_age) == 1:
            return {
                "patient_id": str(by_age[0].get("id") or ""),
                "reason": "exact_age",
                "candidates": [],
            }

    return {
        "patient_id": "",
        "reason": "ambiguous",
        "candidates": [
            {"id": str(c.get("id") or ""), "name": str(c.get("name") or "")}
            for c in same_name[:MAX_CANDIDATES]
        ],
    }


MATCH_REASON_TEXT = {
    "exact": "姓名唯一匹配，已自动归档",
    "exact_gender": "同名，按性别匹配后归档",
    "exact_age": "同名，按年龄匹配后归档",
    "no_name": "未识别到患者姓名，待人工指派",
    "anonymous": "匿名报告，已归入匿名档案",
    "no_match": "档案中没有同名患者，待人工指派",
    "ambiguous": "存在多名同名患者，待人工指派",
    "desensitized": "患者姓名已脱敏（先生/女士），无法自动匹配，待人工指派",
    # 页内上传（上传时已选定患者）：归属不由识别结果决定，这两个值是「对账」结论
    "bound_anonymous": "未识别到患者姓名，已按上传时选择的患者归档",
    "bound_conflict": "报告内姓名与所选患者不一致，已按所选患者归档，建议核验",
}
