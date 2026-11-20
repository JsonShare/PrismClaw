"""知识库种子数据 — 按「医学知识库」设计稿重建，幂等灌入。

内容：
- 文献 12 条（含中英文标题、研究类型、期刊标识、影响因子、发布时间）
- 药品目录 90+ 条（西药/中成药 → 一级分类 → 二级分类 → 药物组）

指南不在本模块：由 `backend/tools/ingest_guidelines.py` 解析真实 PDF 后写入
（source='import:pdf'），因此不受这里的种子清理影响。

ID 生成规则（保证幂等重跑）：
- 文献：DOI 小写去 ./  → 如 10.1056/NEJMoa2500123 → 101056nejmoa2500123
- 药品：有详情的沿用原 id（如 pembrolizumab），其余 name+路径哈希

每次灌入后会清理 source='seed' 且不在本次种子集合中的旧行，
避免旧版知识库数据残留在列表中。
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine

from ...models.tables import kb_drugs, kb_literatures

logger = logging.getLogger(__name__)


def _doi_to_id(doi: str) -> str:
    """DOI → 幂等 ID：小写、去非字母数字。"""
    return re.sub(r"[^a-z0-9]", "", doi.lower())


def _now() -> datetime:
    """当前 UTC 时间。"""
    return datetime.utcnow()


def _days_ago(days: int) -> datetime:
    return _now() - timedelta(days=days)


# ── 文献种子 ────────────────────────────────────────────────────

_LITERATURE_SEEDS: list[dict[str, Any]] = [
    {
        "id": _doi_to_id("10.1056/NEJMoa2500123"),
        "title": "Melanoma Prevention Among Children of Melanoma Survivors",
        "title_en": "Melanoma Prevention Among Children of Melanoma Survivors",
        "title_cn": "黑色素瘤幸存者子女的黑色素瘤预防",
        "study_type": "RCT",
        "journal": "N Engl J Med",
        "journal_tags_json": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 96.2,
        "authors": "Landi MT, et al.",
        "year": 2025,
        "category": "黑色素瘤",
        "abstract": "随机对照研究评估对黑色素瘤幸存者子女开展针对性防晒与筛查教育的保护效果，干预组新发痣数量与非典型痣比例显著低于对照组。",
        "keywords_json": ["黑色素瘤", "预防", "筛查"],
        "citations": 18,
        "doi": "10.1056/NEJMoa2500123",
        "published_at": _days_ago(1),
    },
    {
        "id": _doi_to_id("10.1001/jamadermatol.2025.2231"),
        "title": "Inflammatory Bowel Disease and Interleukin-17 Inhibitors in Hidradenitis Suppurativa",
        "title_en": "Inflammatory Bowel Disease and Interleukin-17 Inhibitors in Hidradenitis Suppurativa",
        "title_cn": "炎症性肠病和白介素-17抑制剂在化脓性汗腺炎中的应用",
        "study_type": "Meta分析",
        "journal": "JAMA Dermatology",
        "journal_tags_json": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 11.0,
        "authors": "Kimball AB, et al.",
        "year": 2025,
        "category": "皮肤病",
        "abstract": "汇总 14 项研究显示，IL-17 抑制剂在中重度化脓性汗腺炎中疗效确切，但合并炎症性肠病者需警惕肠道症状加重。",
        "keywords_json": ["化脓性汗腺炎", "IL-17", "炎症性肠病"],
        "citations": 9,
        "doi": "10.1001/jamadermatol.2025.2231",
        "published_at": _days_ago(1),
    },
    {
        "id": _doi_to_id("10.1001/jamadermatol.2025.2198"),
        "title": "Longitudinal Growth in Children With Epidermal Differentiation Disorders",
        "title_en": "Longitudinal Growth in Children With Epidermal Differentiation Disorders",
        "title_cn": "表皮分化障碍儿童的纵向生长",
        "study_type": "队列研究",
        "journal": "JAMA Dermatology",
        "journal_tags_json": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 11.0,
        "authors": "Paller AS, et al.",
        "year": 2025,
        "category": "儿科皮肤",
        "abstract": "多中心队列随访 8 年，提示重度鱼鳞病患儿生长迟缓风险显著升高，早期营养与皮肤屏障干预可改善身高结局。",
        "keywords_json": ["鱼鳞病", "儿童生长", "队列"],
        "citations": 6,
        "doi": "10.1001/jamadermatol.2025.2198",
        "published_at": _days_ago(1),
    },
    {
        "id": _doi_to_id("10.1001/jama.2025.11842"),
        "title": "Cardiovascular Magnetic Resonance to Guide Defibrillator Implantation for LVEF of 36% to 50%: The CMR GUIDE Randomized Clinical Trial",
        "title_en": "Cardiovascular Magnetic Resonance to Guide Defibrillator Implantation for LVEF of 36% to 50%: The CMR GUIDE Randomized Clinical Trial",
        "title_cn": "心血管磁共振引导除颤器植入术（左心室射血分数为 36% 至 50%）：CMR GUIDE 随机临床试验",
        "study_type": "RCT",
        "journal": "JAMA",
        "journal_tags_json": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 55.0,
        "authors": "Selvanayagam JB, et al.",
        "year": 2025,
        "category": "心血管",
        "abstract": "在 LVEF 36%–50% 人群中，以 CMR 检出瘢痕负荷指导 ICD 植入，较常规策略显著降低心律失常死亡风险。",
        "keywords_json": ["ICD", "心脏磁共振", "猝死预防"],
        "citations": 12,
        "doi": "10.1001/jama.2025.11842",
        "published_at": _days_ago(2),
    },
    {
        "id": _doi_to_id("10.1001/jama.2025.11567"),
        "title": "Histologic Features and Clinical Outcomes of Lean Metabolic Dysfunction–Associated Steatotic Liver Disease",
        "title_en": "Histologic Features and Clinical Outcomes of Lean Metabolic Dysfunction–Associated Steatotic Liver Disease",
        "title_cn": "瘦型代谢功能障碍相关脂肪肝的组织学特征和临床结局",
        "study_type": "队列研究",
        "journal": "JAMA",
        "journal_tags_json": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 55.0,
        "authors": "Younossi ZM, et al.",
        "year": 2025,
        "category": "肝病",
        "abstract": "瘦型 MASLD 患者肝纤维化进展速度与超重人群相近，提示不能以体重正常作为低风险评估依据。",
        "keywords_json": ["MASLD", "瘦型", "肝纤维化"],
        "citations": 7,
        "doi": "10.1001/jama.2025.11567",
        "published_at": _days_ago(2),
    },
    {
        "id": _doi_to_id("10.1001/jama.2025.11203"),
        "title": "Nicotinamide Riboside for Patients With Early-Stage Parkinson Disease: The NOPARK Randomized Clinical Trial",
        "title_en": "Nicotinamide Riboside for Patients With Early-Stage Parkinson Disease: The NOPARK Randomized Clinical Trial",
        "title_cn": "烟酰胺核糖用于早期帕金森病患者：NOPARK 随机临床试验",
        "study_type": "RCT",
        "journal": "JAMA",
        "journal_tags_json": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 55.0,
        "authors": "Schwarzschild MA, et al.",
        "year": 2025,
        "category": "神经",
        "abstract": "NOPARK 试验未达到主要终点，烟酰胺核糖未能延缓早期帕金森病运动症状进展，但安全性良好。",
        "keywords_json": ["帕金森病", "NAD+", "随机试验"],
        "citations": 5,
        "doi": "10.1001/jama.2025.11203",
        "published_at": _days_ago(2),
    },
    {
        "id": _doi_to_id("10.1038/s41392-025-02137-6"),
        "title": "Engineering signal transduction for closed-loop gene and cell therapies",
        "title_en": "Engineering signal transduction for closed-loop gene and cell therapies",
        "title_cn": "工程信号转导用于闭环基因和细胞疗法",
        "study_type": "综述",
        "journal": "Signal Transduction and Targeted Therapy",
        "journal_tags_json": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 52.7,
        "authors": "Lim WA, et al.",
        "year": 2025,
        "category": "基础研究",
        "abstract": "系统梳理合成受体与逻辑门设计，讨论闭环基因/细胞治疗从概念验证走向临床的关键工程瓶颈。",
        "keywords_json": ["合成生物学", "细胞治疗", "信号转导"],
        "citations": 21,
        "doi": "10.1038/s41392-025-02137-6",
        "published_at": _days_ago(2),
    },
    {
        "id": _doi_to_id("10.1136/gutjnl-2025-332145"),
        "title": "RNase5 enables islet β-cells to resist pancreatitis-induced apoptosis",
        "title_en": "RNase5 enables islet β-cells to resist pancreatitis-induced apoptosis",
        "title_cn": "RNase5 使胰岛 β 细胞能够抵抗胰腺炎诱导的细胞凋亡",
        "study_type": "动物",
        "journal": "Gut",
        "journal_tags_json": ["独家原文", "TOP期刊", "JCR Q1"],
        "impact_factor": 25.8,
        "authors": "Chen L, et al.",
        "year": 2025,
        "category": "消化",
        "abstract": "小鼠模型显示 RNase5 通过抑制 JNK 通路减轻胰腺炎相关 β 细胞凋亡，为胰腺源性糖尿病提供潜在靶点。",
        "keywords_json": ["RNase5", "β细胞", "胰腺炎"],
        "citations": 4,
        "doi": "10.1136/gutjnl-2025-332145",
        "published_at": _days_ago(2),
    },
    {
        "id": _doi_to_id("10.1136/bmj-2025-083412"),
        "title": "Effect of salt substitution on cardiovascular events and death: systematic review and meta-analysis",
        "title_en": "Effect of salt substitution on cardiovascular events and death: systematic review and meta-analysis",
        "title_cn": "食盐替代对心血管事件和死亡的影响：系统综述与 Meta 分析",
        "study_type": "系统综述",
        "journal": "BMJ",
        "journal_tags_json": ["TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 42.7,
        "authors": "Neal B, et al.",
        "year": 2025,
        "category": "心血管",
        "abstract": "纳入 21 项试验的汇总分析显示，低钠富钾盐替代可降低卒中与心血管死亡风险，肾功能不全人群需个体化评估。",
        "keywords_json": ["食盐替代", "低钠盐", "心血管"],
        "citations": 14,
        "doi": "10.1136/bmj-2025-083412",
        "published_at": _days_ago(3),
    },
    {
        "id": _doi_to_id("10.1016/S0140-6736(25)00891-2"),
        "title": "Tislelizumab plus chemotherapy as first-line treatment for advanced oesophageal squamous cell carcinoma: a randomised, open-label, phase 3 trial",
        "title_en": "Tislelizumab plus chemotherapy as first-line treatment for advanced oesophageal squamous cell carcinoma: a randomised, open-label, phase 3 trial",
        "title_cn": "替雷利珠单抗联合化疗一线治疗晚期食管鳞癌：随机、开放、III 期试验",
        "study_type": "RCT",
        "journal": "The Lancet",
        "journal_tags_json": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 98.4,
        "authors": "Xu J, et al.",
        "year": 2025,
        "category": "消化道肿瘤",
        "abstract": "III 期研究显示替雷利珠单抗联合化疗显著延长晚期食管鳞癌总生存，PD-L1 高表达人群获益更明显。",
        "keywords_json": ["食管鳞癌", "替雷利珠单抗", "III期"],
        "citations": 26,
        "doi": "10.1016/S0140-6736(25)00891-2",
        "published_at": _days_ago(3),
    },
    {
        "id": _doi_to_id("10.1056/NEJMoa2501877"),
        "title": "Antibody–Drug Conjugates in HER2-Low Breast Cancer: A Systematic Review and Meta-analysis",
        "title_en": "Antibody–Drug Conjugates in HER2-Low Breast Cancer: A Systematic Review and Meta-analysis",
        "title_cn": "抗体偶联药物用于 HER2 低表达乳腺癌：系统综述与 Meta 分析",
        "study_type": "Meta分析",
        "journal": "N Engl J Med",
        "journal_tags_json": ["TOP期刊", "JCR Q1", "中科院1区"],
        "impact_factor": 96.2,
        "authors": "Modi S, et al.",
        "year": 2025,
        "category": "乳腺癌",
        "abstract": "汇总 9 项研究提示，T-DXd 在 HER2 低表达转移性乳腺癌中带来一致的 PFS 与 OS 获益，间质性肺病需重点监测。",
        "keywords_json": ["HER2低表达", "ADC", "T-DXd"],
        "citations": 11,
        "doi": "10.1056/NEJMoa2501877",
        "published_at": _days_ago(4),
    },
    {
        "id": _doi_to_id("10.1001/jama.2025.10877"),
        "title": "Sleep Duration and Incident Hypertension Among Working Adults: A Prospective Cohort Study",
        "title_en": "Sleep Duration and Incident Hypertension Among Working Adults: A Prospective Cohort Study",
        "title_cn": "职业人群睡眠时长与新发高血压：前瞻性队列研究",
        "study_type": "病例对照",
        "journal": "JAMA",
        "journal_tags_json": ["TOP期刊", "JCR Q1"],
        "impact_factor": 55.0,
        "authors": "Grandner MA, et al.",
        "year": 2025,
        "category": "心血管",
        "abstract": "随访 6 年发现，睡眠时长＜6 小时与新发高血压风险升高相关，轮班工作者风险更高。",
        "keywords_json": ["睡眠", "高血压", "队列"],
        "citations": 8,
        "doi": "10.1001/jama.2025.10877",
        "published_at": _days_ago(4),
    },
]


# ── 药品种子 ────────────────────────────────────────────────────
#
# 设计稿为「西药 / 中成药 → 一级分类 → 二级分类 → 药物组 → 药品名」三级目录。
# 单个药物组时不再重复展示组说明（atc_group_desc 留空）。

_DRUG_CATALOG: list[dict[str, Any]] = [
    {
        "kind": "西药",
        "l1": "消化道及代谢",
        "l2": [
            {
                "name": "口腔部药物",
                "groups": [
                    ("口腔局部治疗用抗感染药和灭菌药", ["地喹氯铵", "西地碘", "那他霉素", "甲硝唑", "克霉唑", "氯己定", "度米芬", "四环素"]),
                    ("口腔局部治疗用皮质类固醇药", ["地塞米松", "曲安西龙", "氢化可的松", "泼尼松龙"]),
                    ("口腔局部治疗用其它药物", ["碘甘油", "苄达明", "肾上腺素"]),
                ],
            },
            {"name": "治疗与胃酸分泌相关疾病的药物", "groups": [("治疗与胃酸分泌相关疾病的药物", ["奥美拉唑", "泮托拉唑", "雷贝拉唑", "法莫替丁", "铝碳酸镁"])]},
            {"name": "糖尿病用药", "groups": [("糖尿病用药", ["二甲双胍", "格列美脲", "西格列汀", "达格列净", "胰岛素"])]},
            {"name": "维生素类", "groups": [("维生素类", ["维生素C", "维生素B1", "叶酸"])]},
        ],
    },
    {
        "kind": "西药",
        "l1": "血液和造血器官",
        "l2": [
            {"name": "抗血栓药", "groups": [("抗血栓药", ["阿司匹林", "氯吡格雷", "华法林", "利伐沙班", "低分子肝素"])]},
            {"name": "抗贫血药", "groups": [("抗贫血药", ["硫酸亚铁"])]},
        ],
    },
    {
        "kind": "西药",
        "l1": "心血管系统",
        "l2": [
            {"name": "心脏治疗药", "groups": [("心脏治疗药", ["美托洛尔", "比索洛尔", "地高辛", "胺碘酮"])]},
            {"name": "抗高血压药", "groups": [("抗高血压药", ["氨氯地平", "缬沙坦", "依那普利", "氢氯噻嗪"])]},
            {"name": "调脂药", "groups": [("调脂药", ["阿托伐他汀", "瑞舒伐他汀", "非诺贝特"])]},
        ],
    },
    {
        "kind": "西药",
        "l1": "系统用抗感染药",
        "l2": [
            {"name": "抗菌药", "groups": [("抗菌药", ["阿莫西林", "头孢呋辛", "左氧氟沙星", "阿奇霉素", "万古霉素"])]},
            {"name": "抗病毒药", "groups": [("抗病毒药", ["奥司他韦", "阿昔洛韦"])]},
        ],
    },
    {
        "kind": "西药",
        "l1": "抗肿瘤药和免疫机能调节药",
        "l2": [
            {"name": "细胞毒类药物", "groups": [("细胞毒类药物", ["顺铂", "紫杉醇", "吉西他滨", "氟尿嘧啶"])]},
            {"name": "靶向药物", "groups": [("靶向药物", ["奥希替尼", "吉非替尼", "曲妥珠单抗", "贝伐珠单抗"])]},
            {"name": "免疫治疗药物", "groups": [("免疫治疗药物", ["帕博利珠单抗", "卡瑞利珠单抗", "纳武利尤单抗"])]},
            {"name": "内分泌治疗药物", "groups": [("内分泌治疗药物", ["阿那曲唑", "他莫昔芬", "氟维司群"])]},
            {"name": "骨改良药物", "groups": [("骨改良药物", ["唑来膦酸", "地舒单抗"])]},
        ],
    },
    {
        "kind": "西药",
        "l1": "神经系统",
        "l2": [
            {"name": "镇痛药", "groups": [("镇痛药", ["对乙酰氨基酚", "布洛芬", "曲马多", "吗啡"])]},
            {"name": "抗癫痫药", "groups": [("抗癫痫药", ["卡马西平", "丙戊酸钠", "左乙拉西坦"])]},
        ],
    },
    {
        "kind": "西药",
        "l1": "呼吸系统",
        "l2": [
            {"name": "阻塞性气道疾病用药", "groups": [("阻塞性气道疾病用药", ["沙丁胺醇", "布地奈德", "孟鲁司特"])]},
            {"name": "鼻部用药", "groups": [("鼻部用药", ["糠酸莫米松", "氯雷他定"])]},
        ],
    },
    {
        "kind": "西药",
        "l1": "皮肤病用药",
        "l2": [
            {"name": "抗真菌药", "groups": [("抗真菌药", ["特比萘芬", "酮康唑", "咪康唑"])]},
            {"name": "皮质类固醇", "groups": [("皮质类固醇", ["卤米松", "曲安奈德"])]},
        ],
    },
    {
        "kind": "中成药",
        "l1": "内科用药",
        "l2": [
            {"name": "清热解毒剂", "groups": [("清热解毒剂", ["连花清瘟胶囊", "蓝芩口服液", "蒲地蓝消炎口服液"])]},
            {"name": "扶正剂", "groups": [("扶正剂", ["参一胶囊", "贞芪扶正颗粒"])]},
        ],
    },
    {
        "kind": "中成药",
        "l1": "肿瘤用药",
        "l2": [
            {"name": "抗肿瘤中成药", "groups": [("抗肿瘤中成药", ["康莱特注射液", "消癌平片"])]},
        ],
    },
]

# 保留原有的药品说明书详情（按药品名索引），目录中同名药品自动关联。
_DRUG_DETAILS: dict[str, dict[str, Any]] = {
    "pembrolizumab": {
        "name": "帕博利珠单抗",
        "generic_name": "Pembrolizumab (Keytruda)",
        "dosage": "200mg ivgtt q3w",
        "indications_json": ["NSCLC PD-L1≥50%一线", "NSCLC联合化疗", "黑色素瘤"],
        "mechanism": "PD-1人源化单克隆抗体，阻断PD-1与PD-L1/PD-L2的相互作用，恢复T细胞的抗肿瘤免疫应答。",
        "side_effects_json": ["免疫相关性肺炎", "甲状腺功能异常", "皮疹", "腹泻"],
        "contraindications_json": ["活动性自身免疫性疾病"],
        "interactions_json": ["避免与免疫抑制剂合用"],
        "pregnancy_grade": "D",
        "is_essential": True,
    },
    "osimertinib": {
        "name": "奥希替尼",
        "generic_name": "Osimertinib (Tagrisso)",
        "dosage": "80mg po qd",
        "indications_json": ["EGFR突变NSCLC一线", "T790M耐药后", "术后辅助"],
        "mechanism": "第三代EGFR-TKI，不可逆抑制EGFR敏感突变和T790M耐药突变。",
        "side_effects_json": ["皮疹", "腹泻", "甲沟炎"],
        "contraindications_json": ["QTc间期延长"],
        "interactions_json": ["避免与强效CYP3A4诱导剂合用"],
        "pregnancy_grade": "D",
        "is_essential": True,
    },
    "paclitaxel": {
        "name": "紫杉醇",
        "generic_name": "Paclitaxel",
        "dosage": "175mg/m² ivgtt q3w",
        "indications_json": ["卵巢癌", "乳腺癌", "NSCLC", "胃癌"],
        "mechanism": "促进微管聚合并抑制其解聚，阻断细胞有丝分裂，导致肿瘤细胞凋亡。",
        "side_effects_json": ["骨髓抑制", "神经毒性", "脱发", "过敏反应"],
        "contraindications_json": ["中性粒细胞<1.5×10⁹/L"],
        "interactions_json": ["与顺铂合用时先用紫杉醇"],
        "pregnancy_grade": "D",
        "is_essential": True,
    },
    "zoledronic-acid": {
        "name": "唑来膦酸",
        "generic_name": "Zoledronic Acid",
        "dosage": "4mg ivgtt q4w",
        "indications_json": ["骨转移", "骨质疏松", "多发性骨髓瘤"],
        "mechanism": "双膦酸盐类骨改良药物，抑制破骨细胞介导的骨吸收，减少骨相关事件。",
        "side_effects_json": ["发热", "肌痛", "低钙血症"],
        "contraindications_json": ["CrCl<30mL/min"],
        "interactions_json": ["与钙剂需间隔服用"],
        "pregnancy_grade": "D",
        "is_essential": True,
    },
    "camrelizumab": {
        "name": "卡瑞利珠单抗",
        "generic_name": "Camrelizumab",
        "dosage": "200mg ivgtt q2w/q3w",
        "indications_json": ["NSCLC一线联合", "食管鳞癌二线", "经典型霍奇金淋巴瘤", "肝癌二线"],
        "mechanism": "国产PD-1人源化单抗，阻断PD-1/PD-L1通路，恢复抗肿瘤T细胞免疫应答。",
        "side_effects_json": ["反应性皮肤毛细血管增生症（RCCEP）", "免疫性肺炎", "甲状腺功能异常", "肝功能损伤"],
        "contraindications_json": ["活动性自身免疫病", "器官移植后使用免疫抑制剂者"],
        "interactions_json": ["避免与全身性糖皮质激素及其他免疫抑制剂合用"],
        "pregnancy_grade": "D",
        "is_essential": False,
    },
    "gefitinib": {
        "name": "吉非替尼",
        "generic_name": "Gefitinib (Iressa)",
        "dosage": "250mg po qd",
        "indications_json": ["EGFR敏感突变NSCLC一线", "局部晚期或转移性NSCLC"],
        "mechanism": "第一代EGFR-TKI，可逆性抑制EGFR酪氨酸激酶活性，阻断下游信号传导。",
        "side_effects_json": ["皮疹", "腹泻", "肝功能异常", "间质性肺炎（罕见）"],
        "contraindications_json": ["严重肝功能不全", "间质性肺炎病史"],
        "interactions_json": ["避免与CYP3A4诱导剂（利福平）合用"],
        "pregnancy_grade": "D",
        "is_essential": True,
    },
    "cisplatin": {
        "name": "顺铂",
        "generic_name": "Cisplatin",
        "dosage": "75mg/m² ivgtt q3w",
        "indications_json": ["NSCLC", "食管癌", "膀胱癌", "宫颈癌", "睾丸肿瘤"],
        "mechanism": "铂类细胞毒药物，与DNA交叉联结抑制复制与转录，诱导细胞凋亡。",
        "side_effects_json": ["恶心呕吐（高度致吐）", "肾毒性", "耳毒性", "周围神经病变", "骨髓抑制"],
        "contraindications_json": ["CrCl<60mL/min需减量", "听力损伤", "严重骨髓抑制"],
        "interactions_json": ["与氨基糖苷类抗生素合用增加肾/耳毒性"],
        "pregnancy_grade": "D",
        "is_essential": True,
    },
    "anastrozole": {
        "name": "阿那曲唑",
        "generic_name": "Anastrozole",
        "dosage": "1mg po qd",
        "indications_json": ["绝经后HR+乳腺癌", "晚期乳腺癌内分泌治疗"],
        "mechanism": "第三代选择性非甾体芳香化酶抑制剂，抑制雄激素向雌激素转化，降低绝经后患者雌激素水平。",
        "side_effects_json": ["潮热", "关节痛", "骨质疏松", "血脂异常"],
        "contraindications_json": ["绝经前患者（除非联合卵巢功能抑制）", "严重肝功能损害"],
        "interactions_json": ["避免与含雌激素类药物合用"],
        "pregnancy_grade": "D",
        "is_essential": True,
    },
    "bevacizumab": {
        "name": "贝伐珠单抗",
        "generic_name": "Bevacizumab (Avastin)",
        "dosage": "7.5mg/kg 或 15mg/kg ivgtt q3w",
        "indications_json": ["转移性结直肠癌", "晚期NSCLC联合化疗", "复发性胶质母细胞瘤", "宫颈癌"],
        "mechanism": "抗VEGF人源化单克隆抗体，抑制肿瘤血管新生，阻断肿瘤供血。",
        "side_effects_json": ["高血压", "蛋白尿", "出血/咯血", "胃肠穿孔（罕见）", "伤口愈合延迟"],
        "contraindications_json": ["未控制的高血压", "近期出血史", "计划围手术期（术前6周）"],
        "interactions_json": ["与舒尼替尼合用增加微血管溶血性贫血风险"],
        "pregnancy_grade": "C",
        "is_essential": True,
    },
    "trastuzumab": {
        "name": "曲妥珠单抗",
        "generic_name": "Trastuzumab (Herceptin)",
        "dosage": "首剂8mg/kg，维持6mg/kg ivgtt q3w",
        "indications_json": ["HER2阳性乳腺癌", "HER2阳性胃癌"],
        "mechanism": "抗HER2人源化单抗，作用于HER2胞外结构域，介导ADCC效应抑制肿瘤增殖。",
        "side_effects_json": ["心脏毒性（LVEF下降）", "输液反应", "腹泻", "间质性肺炎（罕见）"],
        "contraindications_json": ["LVEF<50%慎用", "严重输液反应史"],
        "interactions_json": ["避免与蒽环类药物同期使用（增加心脏毒性）"],
        "pregnancy_grade": "D",
        "is_essential": True,
    },
}

_DETAIL_BY_NAME: dict[str, dict[str, Any]] = {
    d["name"]: d for d in _DRUG_DETAILS.values()
}


def _build_drug_seeds() -> list[dict[str, Any]]:
    """把目录树拍平成药品行，并合并说明书详情。"""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cat in _DRUG_CATALOG:
        kind: str = cat["kind"]
        l1: str = cat["l1"]
        for l2 in cat["l2"]:
            l2_name: str = l2["name"]
            multi_group = len(l2["groups"]) > 1
            for group_name, names in l2["groups"]:
                for name in names:
                    if name in seen:
                        continue
                    seen.add(name)
                    detail = _DETAIL_BY_NAME.get(name)
                    if detail:
                        drug_id = next(k for k, v in _DRUG_DETAILS.items() if v is detail)
                    else:
                        digest = hashlib.md5(f"{kind}|{l1}|{l2_name}|{name}".encode()).hexdigest()[:16]
                        drug_id = f"kb-{digest}"
                    row: dict[str, Any] = {
                        "id": drug_id,
                        "name": name,
                        "generic_name": detail["generic_name"] if detail else "",
                        "category": l1,
                        "drug_kind": kind,
                        "atc_l1": l1,
                        "atc_l2": l2_name,
                        "atc_group": group_name,
                        "atc_group_desc": group_name if multi_group else "",
                        "dosage": detail["dosage"] if detail else "",
                        "indications_json": detail["indications_json"] if detail else [],
                        "mechanism": detail["mechanism"] if detail else "",
                        "side_effects_json": detail["side_effects_json"] if detail else [],
                        "contraindications_json": detail["contraindications_json"] if detail else [],
                        "interactions_json": detail["interactions_json"] if detail else [],
                        "pregnancy_grade": detail["pregnancy_grade"] if detail else "",
                        "is_essential": detail["is_essential"] if detail else False,
                        "source": "seed",
                        "create_at": _now(),
                        "create_by": "default",
                        "update_at": _now(),
                        "update_by": "default",
                        "is_deleted": 0,
                    }
                    rows.append(row)
    return rows


_DRUG_SEEDS: list[dict[str, Any]] = _build_drug_seeds()


async def _prune_stale_seed_rows(engine: AsyncEngine, table: Any, keep_ids: list[str]) -> None:
    """删除旧版种子行（source='seed' 且不在本次种子集合中）。"""
    async with engine.begin() as conn:
        await conn.execute(
            delete(table).where(table.c.source == "seed", table.c.id.notin_(keep_ids))
        )


async def seed_kb_data(engine: AsyncEngine) -> None:
    """幂等灌入 KB 种子数据，并清理旧版残留。

    指南不走这里：指南来自 `backend/tools/ingest_guidelines.py` 解析真实 PDF
    （NCCN / CSCO / 中华医学会），写入 source='import:pdf'，与本函数的种子互不干扰。
    """
    from ..kb_store import KbStore

    store = KbStore(engine)

    for lit in _LITERATURE_SEEDS:
        await store.upsert_literature(lit)
    await _prune_stale_seed_rows(engine, kb_literatures, [r["id"] for r in _LITERATURE_SEEDS])
    logger.info("文献种子灌入完成: %d 条", len(_LITERATURE_SEEDS))

    for drug in _DRUG_SEEDS:
        await store.upsert_drug(drug)
    await _prune_stale_seed_rows(engine, kb_drugs, [r["id"] for r in _DRUG_SEEDS])
    logger.info("药品种子灌入完成: %d 条", len(_DRUG_SEEDS))
