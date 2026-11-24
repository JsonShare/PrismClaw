"""肿瘤指南 PDF → 知识库导入工具。

来源三路：
1. NCCN 正本（含文本层）      —— 抽取病种、版本、发布日期、Discussion Overview 摘要、category 1 推荐
2. CSCO 规范（扫描件）        —— 渲染 + OCR 封面/CIP/目录页，取书名、年份、编写组、章节
3. 中华医学会 / 专委会指南共识  —— 抽取中文标题、发布学会、摘要、关键词、核心推荐条目

用法：
    python backend/tools/ingest_guidelines.py --dry-run      # 只解析并打印，不写库
    python backend/tools/ingest_guidelines.py                # 解析 + 写入 pc_medical_kb_guidelines
    python backend/tools/ingest_guidelines.py --no-ocr       # 跳过 CSCO 扫描件

依赖：PyMuPDF(fitz)、asyncpg；CSCO 扫描件额外需要 tesseract（chi_sim）。
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from typing import Any

import fitz

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/prismclaw"
HOME = os.path.expanduser("~")
DEFAULT_SOURCES = [
    os.path.join(HOME, "Downloads"),
    os.path.join(HOME, "Downloads", "2026中华医学会指南"),
]

# ── 学科归类：按优先级匹配，命中即止（顺序影响结果）───────────────

SPECIALTY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("血液肿瘤", ("白血病", "淋巴瘤", "骨髓增生", "多发性骨髓", "MDS", "血液病", "血液学")),
    ("头颈肿瘤", ("鼻咽", "头颈", "口腔", "喉癌", "甲状腺", "下咽", "口咽")),
    ("神经肿瘤", ("胶质", "脑膜瘤", "颅内肿瘤", "中枢神经", "脑转移")),
    ("乳腺癌", ("乳腺",)),
    ("妇科肿瘤", ("卵巢", "宫颈", "子宫内膜", "子宫", "外阴", "妊娠滋养")),
    ("泌尿肿瘤", ("前列腺", "膀胱", "肾癌", "肾细胞", "尿路上皮", "睾丸")),
    (
        "消化肿瘤",
        (
            "胃",
            "结直肠",
            "结肠",
            "直肠",
            "食管",
            "肝癌",
            "肝细胞",
            "胆道",
            "胆管",
            "胰腺",
            "胃肠",
            "消化",
            "壶腹",
            "小肠",
        ),
    ),
    ("肺癌", ("肺", "呼吸", "NSCLC", "SCLC", "胸膜", "间皮瘤")),
]

# 非肿瘤主题过滤：命中即排除（本地 Downloads 混有大量病历/无关文档）
NON_ONCOLOGY = (
    "PETCT",
    "影像健康报告",
    "诊断报告",
    "股权转让",
    "起诉状",
    "聘用信",
    "对话_",
    "hello",
    "大模型配置",
    "QR_HB",
    "Answer-",
)


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def detect_specialty(*texts: str) -> str:
    blob = " ".join(t for t in texts if t)
    for name, keys in SPECIALTY_RULES:
        if any(k in blob for k in keys):
            return name
    return "肿瘤综合"


def doc_type_of(name: str) -> str:
    if "共识" in name:
        return "共识"
    if "规范" in name:
        return "规范"
    if "解读" in name:
        return "解读"
    return "指南"


def slug(*parts: str) -> str:
    raw = "-".join(p for p in parts if p)
    ascii_part = re.sub(r"[^0-9a-zA-Z]+", "-", raw).strip("-").lower()
    digest = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"{ascii_part[:48]}-{digest}" if ascii_part else f"kb-{digest}"


def make_date(
    year: int | None, month: int | None = None, day: int | None = None
) -> datetime | None:
    if not year:
        return None
    return datetime(year, month or 1, day or 1, tzinfo=UTC)


# ── NCCN ────────────────────────────────────────────────────

NCCN_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

# 病种英文 → 中文
NCCN_CN = {
    "Bladder Cancer": "膀胱癌",
    "Breast Cancer": "乳腺癌",
    "Colon Cancer": "结肠癌",
    "Rectal Cancer": "直肠癌",
    "Gastric Cancer": "胃癌",
    "Non-Small Cell Lung Cancer": "非小细胞肺癌",
    "Small Cell Lung Cancer": "小细胞肺癌",
    "Prostate Cancer": "前列腺癌",
    "Head and Neck Cancers": "头颈癌",
    "Cancer of the Nasopharynx": "鼻咽癌",
    "Kidney Cancer": "肾癌",
    "Hepatocellular Carcinoma": "肝细胞癌",
    "Pancreatic Adenocarcinoma": "胰腺癌",
    "Esophageal and Esophagogastric Junction Cancers": "食管癌",
    "Ovarian Cancer": "卵巢癌",
    "Cervical Cancer": "宫颈癌",
    "Multiple Myeloma": "多发性骨髓瘤",
    "Acute Lymphoblastic Leukemia": "急性淋巴细胞白血病",
    "Acute Myeloid Leukemia": "急性髓系白血病",
    "Chronic Lymphocytic Leukemia/Small Lymphocytic Lymphoma": "慢性淋巴细胞白血病",
    "B-Cell Lymphomas": "B 细胞淋巴瘤",
    "Hodgkin Lymphoma": "霍奇金淋巴瘤",
    "Melanoma: Cutaneous": "皮肤黑色素瘤",
    "Thyroid Carcinoma": "甲状腺癌",
    "Soft Tissue Sarcoma": "软组织肉瘤",
    "Bone Cancer": "骨肿瘤",
    "Central Nervous System Cancers": "中枢神经系统肿瘤",
    "Uterine Neoplasms": "子宫肿瘤",
}


def is_nccn(path: str) -> bool:
    try:
        with fitz.open(path) as d:
            head = d[0].get_text() if d.page_count else ""
    except Exception:
        return False
    return "NCCN Clinical Practice Guidelines in Oncology" in head


NCCN_NOISE = (
    "NCCN Clinical Practice Guidelines in Oncology",
    "NCCN Guidelines for Patients",
    "NCCN Guidelines Navigator",
    "NCCN recognizes the importance",
    "Trials should be designed",
    "©",
    "Version ",
    "NCCN.org",
    "续",
)


def _nccn_title(lines: list[str]) -> str:
    """从首页行序列中挑出病种名。

    NCCN 首页版式不统一：
      - 英文正本：病种在 "NCCN Guidelines®" 之后，可能断成两行（Non-Small Cell Lung / Cancer）
      - 中国版：病种在最前面（Head and Neck Cancers / (Cancer of the Nasopharynx)）
      - 中文译本：病种单独一行（乳腺癌 / 结肠癌）
    """
    candidates = [
        line
        for line in lines
        if line
        and not any(n in line for n in NCCN_NOISE)
        and not re.match(r"^版本?\s*[\d.]+", line)
        and len(line) <= 90
    ]
    if not candidates:
        return ""
    first = candidates[0]
    # 英文正本被断行时，与下一行拼接（仅当下一行不含版本号等噪声）
    if len(candidates) > 1:
        nxt = candidates[1]
        if (
            first.endswith(("Lung", "Cell", "and", "of", "Junction"))
            and len(nxt) <= 30
            or nxt.startswith("(")
            and ")" in nxt
        ):
            first = f"{first} {nxt}"
    return first


def _nccn_cn_name(title_en: str) -> str:
    """NCCN 英文病种名 → 中文病种名。

    按 key 长度降序匹配：'Head and Neck Cancers (Cancer of the Nasopharynx)' 应命中
    'Cancer of the Nasopharynx'（鼻咽癌）而不是 'Head and Neck Cancers'（头颈癌）。
    """
    if title_en in NCCN_CN:
        return NCCN_CN[title_en]
    low = title_en.lower()
    for en in sorted(NCCN_CN, key=len, reverse=True):
        if en.lower() in low:
            return NCCN_CN[en]
    return ""


def parse_nccn(path: str) -> dict[str, Any] | None:
    """解析 NCCN 正本 PDF。"""
    with fitz.open(path) as doc:
        if not doc.page_count:
            return None
        first = doc[0].get_text()
        lines = [normalize(ln) for ln in first.split("\n") if ln.strip()]

        title_en = _nccn_title(lines)
        if not title_en:
            return None
        # 中文译本首页病种为中文，标记以便不重复填 title_cn
        has_cn_title = bool(re.search(r"[\u4e00-\u9fa5]", title_en))

        ver_m = re.search(r"Version\s+(\d+\.\d{4})", first) or re.search(
            r"版本\s*(\d+\.\d{4})", first
        )
        version = ver_m.group(1) if ver_m else ""

        published = None
        dm = re.search(r"Version\s+\d+\.\d{4}\s*[—–-]\s*([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", first)
        if dm and dm.group(1) in NCCN_MONTHS:
            published = make_date(int(dm.group(3)), NCCN_MONTHS[dm.group(1)], int(dm.group(2)))
        else:
            dm2 = re.search(r"Version\s+\d+\.\d{4},\s*(\d{2})/(\d{2})/(\d{2,4})", first)
            if dm2:
                y = int(dm2.group(3))
                y = y + 2000 if y < 100 else y
                published = make_date(y, int(dm2.group(1)), int(dm2.group(2)))
        year = (
            published.year
            if published
            else (int(version.split(".")[1]) if "." in version else None)
        )

        # 中文译本没有独立 Discussion 目录页，直接在全文里找「概述」段
        if has_cn_title:
            cn_lines = []
            for i in range(doc.page_count):
                for line in doc[i].get_text().split("\n"):
                    s = line.strip()
                    if not s or re.search(r"[.·]{5,}", s):
                        continue
                    if "NCCN Guidelines 版本" in s or "保留所有权利" in s:
                        continue
                    cn_lines.append(s)
            whole = normalize(" ".join(cn_lines))
            m = re.search(r"概\s*述\s*(.{200,2500}?)(?:文献检索|指南更新方法|分期|风险评估)", whole)
            cn_abstract = m.group(1) if m else ""
            cn_points: list[str] = []
            if cn_abstract:
                for sent in re.split(r"(?<=。)", cn_abstract):
                    s = sent.strip()
                    if len(s) >= 40:
                        cn_points.append(s)
                    if len(cn_points) >= 5:
                        break
            return _nccn_row(
                path, first, title_en, True, version, published, year, cn_abstract, cn_points
            )

        # Discussion 目录页：含「Discussion」+「Table of Contents」+ 指向后续 MS 页的目录行。
        # 页码偏移不统一（多数是 MS-1，前列腺癌是 MS-2），所以不写死页脚。
        # UPDATES 页也带 Table of Contents，用 "Updates in Version" 排除。
        disc_idx = -1
        first_toc = -1
        for i in range(doc.page_count):
            t = doc[i].get_text()
            if "Table of Contents" not in t:
                continue
            if first_toc < 0:
                first_toc = i
            if (
                "Discussion" in t
                and "Updates in Version" not in t
                and re.search(r"MS-\d+\s*$", t, re.M)
            ):
                disc_idx = i
                break
        if disc_idx < 0:
            disc_idx = first_toc

        abstract = ""
        points: list[str] = []
        if disc_idx >= 0:
            body_pages = [
                doc[i].get_text() for i in range(disc_idx + 1, min(disc_idx + 40, doc.page_count))
            ]
            # 逐行剔除页眉/页脚/目录点线：每页都有版本声明与 MS-n 页脚
            kept: list[str] = []
            for page in body_pages:
                for line in page.split("\n"):
                    s = line.strip()
                    if not s:
                        continue
                    if "permission of NCCN" in s or "NCCN Guidelines Version" in s:
                        continue
                    if re.fullmatch(r"MS-\d+", s):
                        continue
                    # 目录行：标题 + 大量点 + 页码
                    if re.search(r"[.·]{5,}", s) or re.fullmatch(r"[\d.]+", s):
                        continue
                    if s in (
                        title_en,
                        "Discussion",
                        "Table of Contents",
                        "Continued",
                        "NCCN Guidelines Index",
                        "Overall Management of Cancer",
                    ):
                        continue
                    kept.append(s)
            body = normalize(" ".join(kept))

            # Overview 正文：从标题开始，到下一个一级小节为止
            ov = re.search(
                r"\bOverview\b\s*(?:[A-Z][^.]{0,80}\.)?(.{300,3000}?)"
                r"(?:Literature Search Criteria|Guidelines Update Methodology"
                r"|\bStaging\b|\bRisk Factors\b)",
                body,
            )
            abstract = normalize(ov.group(1)) if ov else body[:700]
            # 兜底：Overview 段落没定位到时，去掉首页索引/机构声明类噪声
            abstract = re.sub(
                r"^(?:NCCN Guidelines Index|Find an NCCN Member Institution"
                r"|NCCN Categories of Evidence).*?(?=[A-Z][a-z]{3,}\s)",
                "",
                abstract,
            ).strip()

            # category 1 推荐：取含 "category 1" 的完整句
            for sent in re.split(r"(?<=[.!])\s+", body):
                s = normalize(sent)
                if "category 1" in s and 60 <= len(s) <= 400:
                    points.append(s)
                if len(points) >= 5:
                    break

            # 部分病种（如结肠癌）Discussion 通篇 category 2A，无 category 1 表述，
            # 此时退化为「recommended / preferred」的核心推荐句。
            if not points:
                for sent in re.split(r"(?<=[.!])\s+", body):
                    s = normalize(sent)
                    if (
                        "is recommended" in s
                        or "are recommended" in s
                        or "is preferred" in s
                        or "are preferred" in s
                    ) and 80 <= len(s) <= 320:
                        points.append(s)
                    if len(points) >= 5:
                        break

        return _nccn_row(
            path, first, title_en, has_cn_title, version, published, year, abstract, points
        )


def _nccn_row(
    path: str,
    first: str,
    title_en: str,
    has_cn_title: bool,
    version: str,
    published: datetime | None,
    year: int | None,
    abstract: str,
    points: list[str],
) -> dict[str, Any]:
    """组装 NCCN 记录（英文正本 / 中文译本 / 中国版共用）。"""
    is_china = "for China" in first or "China Edition" in os.path.basename(path)
    cn = "" if has_cn_title else _nccn_cn_name(title_en)
    specialty = detect_specialty(title_en, cn or "", os.path.basename(path))
    # 中文译本标题已含中文病种，直接作为主标题
    title = (
        title_en if has_cn_title else f"NCCN Clinical Practice Guidelines in Oncology: {title_en}"
    )
    author_name = "中国专家委员会" if is_china else f"NCCN {title_en} Panel"

    return {
        "id": slug("nccn", title_en, version),
        "title": title[:512],
        "title_cn": (cn or "")[:512],
        "doc_type": "指南",
        "authors": author_name[:512],
        "specialty": specialty,
        "region": "中国" if is_china else "国际",
        "published_at": published,
        "org": "NCCN",
        "category": specialty,
        "year": year,
        "version": f"Version {version}"[:32] if version else "",
        "abstract": abstract[:4000],
        "points": points,
        "source": "import:pdf",
        "_file": os.path.basename(path),
        "_kind": "nccn",
    }


# ── CSCO（扫描件，需 OCR）─────────────────────────────────────


def ocr_page(doc: fitz.Document, index: int, dpi: int = 200) -> str:
    """渲染指定页并 OCR，返回带换行的文本。"""
    if not shutil.which("tesseract"):
        return ""
    png = os.path.join(tempfile.gettempdir(), f"kb_ocr_{os.getpid()}_{index}.png")
    try:
        doc[index].get_pixmap(dpi=dpi).save(png)
        proc = subprocess.run(
            ["tesseract", png, "stdout", "-l", "chi_sim", "--psm", "3"], capture_output=True
        )
        return proc.stdout.decode("utf-8", "replace")
    finally:
        if os.path.exists(png):
            os.remove(png)


def is_csco(path: str) -> bool:
    """判定是否为 CSCO 指南书籍。

    CSCO 正式出版的是整本书（上百页、扫描件），而中华医学会系列期刊共识
    正文里也会引用 "CSCO"，所以不能只靠全文命中关键词。
    """
    name = os.path.basename(path)
    if "CSCO" not in name.upper():
        return False
    try:
        with fitz.open(path) as doc:
            return doc.page_count >= 40
    except Exception:
        return False


def parse_csco(path: str, ocr_pages: int = 16) -> dict[str, Any] | None:
    """解析 CSCO 指南（多为扫描件，OCR 前若干页取元数据）。

    扫描件的版面顺序：封面(1-2) → CIP(3) → 编写组(4-9) → 前言(10) → 目录(11-13) → 正文。
    前言没有「前言」标题，靠首句特征识别；章节标题从目录页取。
    """
    with fitz.open(path) as doc:
        if not doc.page_count:
            return None
        text_pages = sum(1 for i in range(doc.page_count) if len(doc[i].get_text()) > 100)
        scanned = text_pages < 3

        if scanned:
            pages = [ocr_page(doc, i) for i in range(min(ocr_pages, doc.page_count))]
        else:
            pages = [doc[i].get_text() for i in range(min(16, doc.page_count))]
        head = "\n".join(pages)

        book = _csco_book_name(head, os.path.basename(path))

        year_m = re.search(r"(20\d{2})\s*[.．年]\s*(\d{1,2})?", head)
        year = int(year_m.group(1)) if year_m else None
        fy = re.search(r"(20\d{2})", os.path.basename(path))
        if not year and fy:
            year = int(fy.group(1))
        month = int(year_m.group(2)) if year_m and year_m.group(2) else None
        if month and not 1 <= month <= 12:
            month = None
        published = make_date(year, month)
        version = f"{year}版" if year else ""

        abstract = _csco_preface(pages)
        points = _csco_sections(pages)

        specialty = detect_specialty(book, os.path.basename(path))
        return {
            "id": slug("csco", book, version),
            "title": book[:512],
            "title_cn": "",
            "doc_type": "指南",
            "authors": "中国临床肿瘤学会指南工作委员会",
            "specialty": specialty,
            "region": "中国",
            "published_at": published,
            "org": "CSCO",
            "category": specialty,
            "year": year,
            "version": version[:32],
            "abstract": abstract[:4000],
            "points": points,
            "source": "import:pdf",
            "_file": os.path.basename(path),
            "_kind": "csco",
            "_scanned": scanned,
        }


def _csco_preface(pages: list[str]) -> str:
    """CSCO 前言：CSCO 系列固定以「基于循证医学证据、兼顾诊疗产品的可及性」开篇。"""
    for text in pages:
        flat = normalize(text)
        if "基于循证医学证据" in flat:
            return flat[:1600]
    # 退路：取最长的连续中文段落
    cands = [normalize(t) for t in pages if len(normalize(t)) > 200 and "目录" not in t]
    return max(cands, key=len)[:1600] if cands else ""


def _csco_sections(pages: list[str]) -> list[str]:
    """CSCO 章节目录：形如「1.1 诊断基本原则」「2.3 胃癌支持治疗」。

    扫描件库里目录页与 CIP/版权页混排，需剔除出版信息（印刷、组织编写、标准书号等），
    并优先保留带小数点的二级标题（信息量高于一级）。
    """
    noise = (
        "印刷",
        "组织编写",
        "标准书号",
        "出版",
        "发行",
        "定价",
        "字",
        "版次",
        "开本",
        "印张",
        "插页",
        "书号",
        "数据",
        "核字",
        "版权所有",
        "侵权",
    )
    primary: list[str] = []
    secondary: list[str] = []
    for text in pages:
        for m in re.finditer(r"(\d{1,2}(?:\.\d{1,2})?)\s*([\u4e00-\u9fa5]{3,24})", text):
            num, name = m.group(1), m.group(2)
            if any(ch in name for ch in "，。、（）()「」【】|*＊"):
                continue
            if any(k in name for k in noise):
                continue
            if any(k in name for k in ("页", "版", "目", "录", "证据", "推荐等级")):
                continue
            item = f"{num} {name}"
            bucket = secondary if "." in num else primary
            if item not in secondary and item not in primary:
                bucket.append(item)
    return (secondary or primary)[:6]


def _csco_book_name(head: str, filename: str) -> str:
    """CSCO 书名：优先扫描件里命中的正式书名，其次用文件名还原。

    OCR 对封面大字易错（如「胃癌」→「骨癌」），所以只要文件名带病种就以文件名为准。
    """
    stem = os.path.splitext(filename)[0]
    # 文件名形如 CSCO_胃癌诊疗指南2024 → 胃癌诊疗指南
    m = re.match(r"CSCO[_\-\s]*(.+?)(\d{4})?$", stem, re.I)
    from_name = normalize(m.group(1)) if m else ""
    if from_name:
        return (
            f"中国临床肿瘤学会（CSCO）{from_name}"
            if not from_name.startswith("中国")
            else from_name
        )

    m2 = re.search(
        r"中国临床肿瘤学会\s*[（(]?\s*CSCO\s*[)）]?\s*([\u4e00-\u9fa5]{2,30}?指南)", head
    )
    if m2:
        return f"中国临床肿瘤学会（CSCO）{m2.group(1)}"
    return normalize(stem)


# ── 中华医学会 / 专委会 ─────────────────────────────────────


def _cn_title(lines: list[str], stem: str) -> str:
    """抽取中文指南标题。

    版式：
      - 「脑胶质瘤放射治疗专家共识（2025版）」整行；
      - 「肝细胞癌术后辅助治疗专家共识」+ 次行「（2026版）」需拼接；
      - 「骨髓增生异常肿瘤诊断与治疗中国指南」+ 「（2026年版）」；
      - 有时首行为栏目名（「标准与规范」），真正标题在第 2-3 行。
    """
    skip = ("标准与规范", "指南与共识", "专家论坛", "述评", "论著", "综述")
    body = [ln for ln in lines if ln and ln not in skip]

    # 在文件名里找完整标题（文件名通常就是完整标题）
    stem_clean = re.sub(r"\s+", "", stem)
    if 8 <= len(stem_clean) <= 80 and ("指南" in stem_clean or "共识" in stem_clean):
        return stem_clean

    for i, line in enumerate(body[:8]):
        if not 6 <= len(line) <= 80:
            continue
        if "指南" not in line and "共识" not in line:
            continue
        # 末尾的「（2025版）」可能被拆到下一行
        if i + 1 < len(body) and re.fullmatch(r"[（(]20\d{2}\s*年?版?[)）]", body[i + 1]):
            return re.sub(r"\s+", "", f"{line}{body[i + 1]}")
        return re.sub(r"\s+", "", line)
    return stem_clean


def is_cn_guideline(path: str) -> bool:
    name = os.path.basename(path)
    if any(k in name for k in NON_ONCOLOGY):
        return False
    if not any(k in name for k in ("指南", "共识")):
        return False
    # 必须是肿瘤相关
    if not any(k in name for k in ("癌", "肿瘤", "白血病", "淋巴瘤", "胶质", "骨髓增生")):
        return False
    try:
        with fitz.open(path) as doc:
            return doc.page_count > 0 and len(doc[0].get_text()) > 100
    except Exception:
        return False


def parse_cn(path: str) -> dict[str, Any] | None:
    """解析中文指南/共识 PDF（有文本层）。"""
    with fitz.open(path) as doc:
        if not doc.page_count:
            return None
        first = doc[0].get_text()
        lines = [normalize(ln) for ln in first.split("\n") if ln.strip()]

        stem = os.path.splitext(os.path.basename(path))[0]
        title = _cn_title(lines, stem)

        # 发布机构
        org_line = ""
        for line in lines[1:12]:
            if any(k in line for k in ("分会", "专家委员会", "专业委员会", "学组", "协会")):
                org_line = line
                break
        org_name = (
            "中华医学会"
            if "中华医学会" in (org_line or first) or "中华" in org_line
            else "中华医学会"
        )
        if "中国抗癌协会" in (org_line or first):
            org_name = "中国抗癌协会"
        elif "中国医师协会" in (org_line or first):
            org_name = "中国医师协会"

        # 期刊页眉取年月：「中华XX杂志 2026 年1 月第 35 卷第 1 期」
        ym = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", first)
        year = int(ym.group(1)) if ym else None
        month = int(ym.group(2)) if ym else None
        if not year:
            fy = re.search(r"[（(](20\d{2})", stem)
            year = int(fy.group(1)) if fy else None
        published = make_date(year, month)

        # 摘要
        ab = ""
        m = re.search(r"【摘\s*要】(.+?)(?=【关键词】|【Key words】|基金项目|$)", first, re.S)
        if m:
            ab = normalize(m.group(1))
        if not ab:
            body_first = doc[1].get_text() if doc.page_count > 1 else ""
            ab = normalize(body_first)[:600]

        # 关键词
        kw = re.findall(r"【关键词】\s*(.+)", first)
        keywords = [k.strip() for k in re.split(r"[；;]", kw[0])] if kw else []

        # 核心推荐：专家共识 N：… / 推荐意见 N：…（正文里条目常跨行，先合并成段）
        body = "".join(doc[i].get_text() for i in range(min(doc.page_count, 10)))
        body = re.sub(r"中华[\u4e00-\u9fa5]{0,20}杂志[^\n]*\n", "", body)

        points: list[str] = []
        # 1) 显式编号条目（多数共识用「推荐意见N：」「专家共识N：」）
        for m in re.finditer(
            r"(专家共识|推荐意见|共识意见)\s*(\d{1,2})\s*[：:](.{10,400}?)(?=(?:专家共识|推荐意见|共识意见)\s*\d|$)",
            body,
            re.S,
        ):
            text = f"{m.group(1)}{m.group(2)}：{normalize(m.group(3))}"
            # 编号也可能出现在方法学段落里（「针对共识意见进行投票」），排除
            if any(k in text for k in ("投票", "共识框架", "制定流程", "初稿", "表决")):
                continue
            points.append(text[:300])
            if len(points) >= 6:
                break
        # 2) 没有编号条目时，只取带证据等级标记的推荐句（避免抓到前言与方法学）
        if not points:
            flat = normalize(body)
            start = flat.find("【关键词】")
            body_text = flat[start:] if start > 0 else flat
            for sent in re.split(r"(?<=[。])", body_text):
                s = sent.strip()
                if not (40 <= len(s) <= 300):
                    continue
                if not ("推荐" in s or "建议" in s):
                    continue
                if not any(
                    k in s
                    for k in (
                        "证据级别",
                        "推荐强度",
                        "证据等级",
                        "推荐级别",
                        "推荐意见",
                        "推荐采用",
                        "建议采用",
                        "不推荐",
                    )
                ):
                    continue
                points.append(s)
                if len(points) >= 6:
                    break
        # 3) 仍为空时，按「（证据等级X，推荐强度Y）」标记反推推荐句
        if not points:
            flat_all = normalize(body)
            for m in re.finditer(
                r"[（(][^（()）]{0,40}证据[级等][^（()）]{0,60}推荐[强级][^（()）]{0,40}[)）]",
                flat_all,
            ):
                seg = flat_all[max(0, m.start() - 240) : m.end()]
                cut = max(seg.rfind("。"), seg.rfind("；"))
                text = normalize(seg[cut + 1 :] if cut > 0 else seg)
                if len(text) < 30:
                    continue
                if text not in points:
                    points.append(text[:300])
                if len(points) >= 6:
                    break

        # 4) 统一清洗：剔除方法学/流程类句子（三条分支都可能命中）
        noise = (
            "投票",
            "共识框架",
            "制定流程",
            "初稿",
            "表决",
            "编写小组",
            "指导委员会",
            "利益冲突",
            "基金项目",
            "注册",
            "检索策略",
            "德尔菲",
            "审定共识发布",
        )
        points = [p for p in points if not any(k in p for k in noise)]

        if not points and keywords:
            points = [f"关键词：{'、'.join(keywords[:6])}"]

        ver = ""
        vm = re.search(r"[（(](20\d{2})\s*年?\s*版?[)）]", stem)
        if vm:
            ver = f"{vm.group(1)}版"

        specialty = detect_specialty(title, stem)
        return {
            "id": slug("cma", title, str(year or "")),
            "title": title[:512],
            "title_cn": "",
            "doc_type": doc_type_of(title),
            "authors": (org_line or org_name)[:512],
            "specialty": specialty,
            "region": "中国",
            "published_at": published,
            "org": org_name[:32],
            "category": specialty,
            "year": year,
            "version": ver[:32],
            "abstract": ab[:4000],
            "points": points,
            "source": "import:pdf",
            "_file": os.path.basename(path),
            "_kind": "cmg" if org_name == "中华医学会" else "assoc",
        }


# ── 采集 / 去重 ─────────────────────────────────────────────


def collect(sources: list[str], use_ocr: bool) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    skipped: list[str] = []
    seen_hash: dict[str, str] = {}

    files: list[str] = []
    for d in sources:
        files.extend(sorted(glob.glob(os.path.join(d, "*.pdf"))))

    for path in files:
        name = os.path.basename(path)
        if any(k in name for k in NON_ONCOLOGY):
            continue
        try:
            with open(path, "rb") as fh:
                digest = hashlib.md5(fh.read()).hexdigest()
        except OSError:
            continue
        if digest in seen_hash:
            skipped.append(f"{name}（与 {seen_hash[digest]} 内容相同）")
            continue

        row: dict[str, Any] | None = None
        try:
            if is_nccn(path):
                row = parse_nccn(path)
            elif use_ocr and is_csco(path):
                row = parse_csco(path)
            elif is_cn_guideline(path):
                row = parse_cn(path)
        except Exception as exc:  # 单文件失败不阻断整体
            skipped.append(f"{name}（解析失败：{type(exc).__name__}: {exc}）")
            continue

        if row:
            seen_hash[digest] = name
            rows.append(row)

    # 同 org + 版本线只保留最新版本（NCCN 多版本共存时取最新的）
    # 中国版与正本并存：region 参与分组，避免中国版被正本去掉
    latest: dict[tuple, dict] = {}
    for r in rows:
        key = (r["org"], r.get("region", ""), r.get("title_cn") or r["title"])
        if r["org"] == "NCCN":
            prev = latest.get(key)
            cur_v = (r.get("year") or 0, r.get("version") or "")
            prev_v = (prev.get("year") or 0, prev.get("version") or "") if prev else (-1, "")
            if prev is None or cur_v > prev_v:
                if prev is not None:
                    skipped.append(f"{prev['_file']}（被更新版本 {r['version']} 取代）")
                latest[key] = r
            else:
                skipped.append(f"{r['_file']}（已有更新版本 {prev.get('version')}）")
        else:
            latest[(r["org"], r["id"])] = r

    return list(latest.values()), skipped


# ── 入库 ────────────────────────────────────────────────────

UPSERT = """
INSERT INTO pc_medical_kb_guidelines
    (id, title, title_cn, doc_type, authors, specialty, region, published_at,
     org, category, year, version, abstract, points_json, source,
     create_at, create_by, update_at, update_by, is_deleted)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb, $15,
        NOW(), 'default', NOW(), 'default', 0)
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title, title_cn = EXCLUDED.title_cn, doc_type = EXCLUDED.doc_type,
    authors = EXCLUDED.authors, specialty = EXCLUDED.specialty, region = EXCLUDED.region,
    published_at = EXCLUDED.published_at, org = EXCLUDED.org, category = EXCLUDED.category,
    year = EXCLUDED.year, version = EXCLUDED.version, abstract = EXCLUDED.abstract,
    points_json = EXCLUDED.points_json, source = EXCLUDED.source,
    update_at = NOW(), is_deleted = 0
"""


async def write_rows(rows: list[dict], dsn: str) -> int:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            for r in rows:
                await conn.execute(
                    UPSERT,
                    r["id"],
                    r["title"],
                    r["title_cn"],
                    r["doc_type"],
                    r["authors"],
                    r["specialty"],
                    r["region"],
                    r["published_at"],
                    r["org"],
                    r["category"],
                    r["year"],
                    r["version"],
                    r["abstract"],
                    json.dumps(r["points"], ensure_ascii=False),
                    r["source"],
                )
        return len(rows)
    finally:
        await conn.close()


def print_table(rows: list[dict]) -> None:
    print(f"\n{'=' * 100}")
    print(f"解析结果：{len(rows)} 条\n")
    for r in rows:
        date = r["published_at"].strftime("%Y-%m-%d") if r["published_at"] else "-"
        print(
            f"[{r['_kind']:<6}] {r['org']:<10} {date:<11} {r['specialty']:<6} "
            f"{r['version']:<14} {r['doc_type']}  {r['title'][:62]}"
        )
        if r["title_cn"]:
            print(f"         cn: {r['title_cn']}")
        print(
            f"         authors: {r['authors'][:60]} | points={len(r['points'])} "
            f"| abstract={len(r['abstract'])}字"
        )
    print("=" * 100)


def main() -> int:
    ap = argparse.ArgumentParser(description="肿瘤指南 PDF → 医疗知识库")
    ap.add_argument("--sources", nargs="*", default=DEFAULT_SOURCES, help="PDF 目录")
    ap.add_argument("--dsn", default=os.environ.get("PRISMCLAW_PG_DSN", DEFAULT_DSN))
    ap.add_argument("--dry-run", action="store_true", help="只解析不写库")
    ap.add_argument("--no-ocr", action="store_true", help="跳过 CSCO 扫描件 OCR")
    args = ap.parse_args()

    rows, skipped = collect(args.sources, use_ocr=not args.no_ocr)
    rows.sort(key=lambda r: (r["org"] != "NCCN", r["org"], r["specialty"], -(r["year"] or 0)))
    print_table(rows)

    if skipped:
        print(f"\n跳过 {len(skipped)} 个文件：")
        for s in skipped:
            print("  -", s)

    if args.dry_run:
        print("\n[dry-run] 未写入数据库")
        return 0
    if not rows:
        print("没有可导入的记录")
        return 1

    count = asyncio.run(write_rows(rows, args.dsn))
    print(f"\n已写入 pc_medical_kb_guidelines：{count} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
