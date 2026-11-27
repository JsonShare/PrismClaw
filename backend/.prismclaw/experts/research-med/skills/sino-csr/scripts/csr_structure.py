"""
ICH E3 章节结构定义模块

定义ICH E3《临床研究报告的结构与内容》指南的19个核心模块，
包含每个模块的元数据（标题、必填/选填、数据来源依赖、子章节）。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict


class AutomationLevel(Enum):
    """章节自动化程度"""
    FULL = "全自动"       # 完全从上游结果自动生成
    SEMI = "半自动"       # 部分自动 + 部分手动补充
    MANUAL = "手动"       # 需要用户手动填写


class FillingStatus(Enum):
    """填充状态"""
    FILLED = "已填充"     # 数据已从上游获取并填入
    PLACEHOLDER = "占位符"  # 使用占位符，需手动补充
    PARTIAL = "部分填充"   # 部分内容已填充


@dataclass
class SubSection:
    """子章节定义"""
    title: str                    # 子章节标题
    description: str = ""         # 子章节描述/内容要求（默认为空）
    placeholder_key: str = ""    # 模板中的占位符键（默认为空）
    data_source: Optional[str] = None  # 数据来源skill（如 "differential-analysis"）
    data_file: Optional[str] = None    # 数据文件名（如 "10_differential_results.json"）
    automation: AutomationLevel = AutomationLevel.MANUAL
    is_required: bool = True      # 是否为ICH E3必需


@dataclass
class CSRModule:
    """CSR模块定义（对应ICH E3的19个核心章节）"""
    number: int                   # 章节序号
    title_zh: str                 # 中文标题
    title_en: str                 # 英文标题
    description: str              # 章节描述
    is_required: bool = True      # ICH E3是否必需
    sub_sections: List[SubSection] = field(default_factory=list)
    data_sources: List[str] = field(default_factory=list)  # 依赖的上游skills
    automation: AutomationLevel = AutomationLevel.MANUAL
    citation_format_note: str = ""  # 引用格式说明


# ============================================================
# ICH E3 标准19个核心模块定义
# ============================================================

ICH_E3_STRUCTURE: List[CSRModule] = [
    # ---- 模块 1: 研究报告基本信息 ----
    CSRModule(
        number=1,
        title_zh="研究报告基本信息",
        title_en="Administrative Information",
        description="方案编号、版本、伦理委员会批准信息、试验注册号、知情同意书信息",
        is_required=True,
        automation=AutomationLevel.SEMI,
        sub_sections=[
            SubSection(
                title="方案及 amendments",
                description="方案编号、标题、版本号及日期、方案修正记录",
                placeholder_key="PROTOCOL_ID,PROTOCOL_TITLE,PROTOCOL_VERSION,PROTOCOL_DATE,AMENDMENTS_LIST",
                automation=AutomationLevel.MANUAL,
                is_required=True
            ),
            SubSection(
                title="伦理委员会信息",
                description="各中心伦理委员会名称、批准日期、批准编号",
                placeholder_key="IRB_1_NAME,IRB_1_DATE,IRB_1_NUMBER",
                automation=AutomationLevel.MANUAL,
                is_required=True
            ),
            SubSection(
                title="试验注册信息",
                description="临床试验注册号、注册平台、注册日期",
                placeholder_key="CLINICAL_TRIAL_ID,REGISTRY_PLATFORM,REGISTRATION_DATE",
                automation=AutomationLevel.MANUAL,
                is_required=True
            ),
            SubSection(
                title="知情同意",
                description="知情同意书版本、日期、伦理批准状态",
                placeholder_key="ICF_VERSION,ICF_DATE,ICF_APPROVED",
                automation=AutomationLevel.MANUAL,
                is_required=True
            ),
        ]
    ),

    # ---- 模块 2: 声明 ----
    CSRModule(
        number=2,
        title_zh="声明",
        title_en="Declarations",
        description="根据研究类型自动检测并推荐适用的报告声明指南",
        is_required=True,
        automation=AutomationLevel.FULL,
        data_sources=["data-preparation"],
        sub_sections=[
            SubSection(
                title="声明类型检测",
                description="根据研究设计自动选择：RCT→CONSORT、队列/病例对照→STROBE、系统综述→PRISMA",
                placeholder_key="DECLARATION_TYPE",
                automation=AutomationLevel.FULL,
                is_required=True
            ),
            SubSection(
                title="声明正文",
                description="声明指南的具体要求对照",
                placeholder_key="DECLARATION",
                automation=AutomationLevel.SEMI,
                is_required=True
            ),
        ]
    ),

    # ---- 模块 3: 研究者及研究机构 ----
    CSRModule(
        number=3,
        title_zh="研究者及研究机构",
        title_en="Investigators and Study Centers",
        description="主要研究者信息、各中心研究者列表及入组例数",
        is_required=True,
        automation=AutomationLevel.MANUAL,
        sub_sections=[
            SubSection(
                title="主要研究者",
                description="姓名、职称、机构、联系方式",
                placeholder_key="PI_NAME,PI_TITLE,PI_INSTITUTION,PI_CONTACT",
                automation=AutomationLevel.MANUAL,
                is_required=True
            ),
            SubSection(
                title="各中心研究者列表",
                description="中心编号、名称、主要研究者、入组例数",
                placeholder_key="CENTER_LIST",
                automation=AutomationLevel.MANUAL,
                is_required=True
            ),
        ]
    ),

    # ---- 模块 4: 引言 ----
    CSRModule(
        number=4,
        title_zh="引言",
        title_en="Introduction",
        description="研究背景、研究药物描述、适应症及现有治疗、研究理论基础、研究目的",
        is_required=True,
        automation=AutomationLevel.MANUAL,
        sub_sections=[
            SubSection(title="研究背景", description="疾病流行病学、未满足的临床需求",
                       placeholder_key="INTRODUCTION_BACKGROUND"),
            SubSection(title="研究药物", description="药物机制、临床前数据",
                       placeholder_key="DRUG_DESCRIPTION"),
            SubSection(title="适应症及现有治疗", description="当前标准治疗、药物局限性",
                       placeholder_key="INDICATION_AND_EXISTING_TREATMENT"),
            SubSection(title="研究理论基础", description="本次研究的科学依据",
                       placeholder_key="SCIENTIFIC_RATIONALE"),
            SubSection(title="研究目的（概要）", description="主要/次要/探索性目的概述",
                       placeholder_key="PRIMARY_OBJECTIVE,SECONDARY_OBJECTIVES,EXPLORATORY_OBJECTIVES"),
        ]
    ),

    # ---- 模块 5: 研究目的 ----
    CSRModule(
        number=5,
        title_zh="研究目的",
        title_en="Study Objectives",
        description="主要目的、次要目的、探索性目的、研究假设",
        is_required=True,
        automation=AutomationLevel.MANUAL,
        sub_sections=[
            SubSection(title="主要目的", description="", placeholder_key="PRIMARY_OBJECTIVE_DETAIL"),
            SubSection(title="次要目的", description="", placeholder_key="SECONDARY_OBJECTIVES_DETAIL"),
            SubSection(title="探索性目的", description="", placeholder_key="EXPLORATORY_OBJECTIVES_DETAIL"),
            SubSection(title="研究假设", description="", placeholder_key="STUDY_HYPOTHESIS"),
        ]
    ),

    # ---- 模块 6: 研究设计与方法 ----
    CSRModule(
        number=6,
        title_zh="研究设计与方法",
        title_en="Study Design and Methods",
        description="总体研究设计、研究人群、给药方案、疗效评价指标、统计方法",
        is_required=True,
        automation=AutomationLevel.SEMI,
        data_sources=["data-preparation", "power-analysis"],
        sub_sections=[
            SubSection(title="总体研究设计",
                       description="研究类型、设计、设盲、分期、分组",
                       placeholder_key="STUDY_TYPE,STUDY_DESIGN_DETAIL,BLINDING_METHOD,STUDY_PHASE,TREATMENT_ARMS"),
            SubSection(title="研究人群",
                       description="入选标准、排除标准、退出标准",
                       placeholder_key="INCLUSION_CRITERIA,EXCLUSION_CRITERIA,WITHDRAWAL_CRITERIA"),
            SubSection(title="给药方案",
                       description="各组给药详情",
                       placeholder_key="TREATMENT_REGIMEN_TABLE"),
            SubSection(title="疗效评价指标",
                       description="主要/次要/探索性终点定义",
                       placeholder_key="PRIMARY_ENDPOINT_DEFINITION,SECONDARY_ENDPOINTS_DEFINITION"),
            SubSection(title="安全性评价指标", placeholder_key="SAFETY_ENDPOINTS_DEFINITION"),
            SubSection(title="有效性评价时间窗", placeholder_key="EVALUATION_SCHEDULE"),
            SubSection(title="样本量估算",
                       description="从power-analysis获取",
                       placeholder_key="SAMPLE_SIZE_CALCULATION,POWER_ANALYSIS_REFERENCE",
                       data_source="power-analysis",
                       data_file="power_analysis_result.md",
                       automation=AutomationLevel.FULL),
            SubSection(title="分析人群定义",
                       description="FAS、PPS、SS定义",
                       placeholder_key="FAS_DEFINITION,PPS_DEFINITION,SS_DEFINITION"),
            SubSection(title="主要/次要/安全性分析方法",
                       placeholder_key="PRIMARY_ANALYSIS_METHOD,SECONDARY_ANALYSIS_METHOD,SAFETY_ANALYSIS_METHOD"),
            SubSection(title="缺失数据处理", placeholder_key="MISSING_DATA_HANDLING"),
            SubSection(title="亚组分析", placeholder_key="SUBGROUP_ANALYSIS"),
            SubSection(title="多重比较校正", placeholder_key="MULTIPLE_COMPARISON_CORRECTION"),
        ]
    ),

    # ---- 模块 7: 研究受试者 ----
    CSRModule(
        number=7,
        title_zh="研究受试者",
        title_en="Study Subjects",
        description="受试者处置（筛选、随机化、完成、退出）、CONSORT流程图、方案违背",
        is_required=True,
        automation=AutomationLevel.FULL,
        data_sources=["data-preparation"],
        sub_sections=[
            SubSection(title="受试者处置",
                       description="筛选→随机化→完成/退出的流程",
                       placeholder_key="SCREENED_EXP,RANDOMIZED_EXP,COMPLETED_EXP,DISCONTINUED_EXP",
                       data_source="data-preparation",
                       data_file="01_prepared_data.csv",
                       automation=AutomationLevel.FULL),
            SubSection(title="受试者流程图",
                       description="CONSORT流程图",
                       placeholder_key="CONSORT_FLOW_DIAGRAM",
                       automation=AutomationLevel.SEMI),
            SubSection(title="提前退出原因", placeholder_key="DISCONTINUATION_REASONS_TABLE"),
            SubSection(title="方案违背", placeholder_key="PROTOCOL_DEVIATIONS_TABLE"),
            SubSection(title="重要方案违背列表", placeholder_key="MAJOR_DEVIATIONS_DETAIL"),
        ]
    ),

    # ---- 模块 8: 有效性分析 ----
    CSRModule(
        number=8,
        title_zh="有效性分析",
        title_en="Efficacy Analysis",
        description="分析人群、人口学基线特征（Table 1）、主要终点、次要终点、亚组分析、多因素分析、生存分析",
        is_required=True,
        automation=AutomationLevel.FULL,
        data_sources=["differential-analysis", "regression-analysis", "survival-analysis", "result-interpreter"],
        sub_sections=[
            SubSection(title="分析人群", placeholder_key="PRIMARY_ANALYSIS_POP,SENSITIVITY_ANALYSIS_POP"),
            SubSection(title="人口学特征与基线特征（Table 1）",
                       description="从differential-analysis自动生成三线表",
                       placeholder_key="DEMOGRAPHICS_TABLE,DEMOGRAPHICS_COMPARISON_NOTE",
                       data_source="differential-analysis",
                       data_file="10_differential_results.json",
                       automation=AutomationLevel.FULL),
            SubSection(title="既往病史与合并用药", placeholder_key="MEDICAL_HISTORY_AND_CONMEDS"),
            SubSection(title="基线疾病特征", placeholder_key="BASELINE_DISEASE_CHARACTERISTICS"),
            SubSection(title="主要疗效终点分析（FAS）",
                       description="主要终点结果表格+效应量",
                       placeholder_key="PRIMARY_ENDPOINT_RESULTS,PRIMARY_EFFECT_SIZE",
                       automation=AutomationLevel.FULL),
            SubSection(title="主要疗效终点（PPS敏感性分析）", placeholder_key="PRIMARY_PPS_RESULTS"),
            SubSection(title="次要疗效终点分析",
                       description="从differential-analysis获取",
                       placeholder_key="SECONDARY_ENDPOINTS_RESULTS",
                       data_source="differential-analysis",
                       automation=AutomationLevel.FULL),
            SubSection(title="亚组分析", placeholder_key="SUBGROUP_ANALYSIS_RESULTS"),
            SubSection(title="多因素分析",
                       description="从regression-analysis获取",
                       placeholder_key="REGRESSION_RESULTS",
                       data_source="regression-analysis",
                       data_file="11_regression_results.json",
                       automation=AutomationLevel.FULL),
            SubSection(title="生存分析（如适用）",
                       description="从survival-analysis获取",
                       placeholder_key="SURVIVAL_RESULTS",
                       data_source="survival-analysis",
                       data_file="12_survival_results.json",
                       automation=AutomationLevel.FULL),
            SubSection(title="有效性结论",
                       description="包含Plain Language解释",
                       placeholder_key="EFFICACY_CONCLUSION,PLAIN_LANGUAGE_EFFICACY",
                       data_source="result-interpreter",
                       data_file="plain_language_interpretation.md",
                       automation=AutomationLevel.SEMI),
        ]
    ),

    # ---- 模块 9: 安全性分析 ----
    CSRModule(
        number=9,
        title_zh="安全性分析",
        title_en="Safety Analysis",
        description="安全性分析人群、暴露程度、AE/SAE、实验室检查、生命体征、心电图",
        is_required=True,
        automation=AutomationLevel.SEMI,
        data_sources=["data-preparation", "result-interpreter"],
        sub_sections=[
            SubSection(title="安全性分析人群", placeholder_key="N_SAFETY"),
            SubSection(title="暴露程度", placeholder_key="EXPOSED_EXP,EXPOSURE_DURATION_EXP,DOSE_INTENSITY_EXP"),
            SubSection(title="不良事件（AE）总体分析", placeholder_key="ANY_AE_EXP,RELATED_AE_EXP,SAE_EXP"),
            SubSection(title="不良事件列表", placeholder_key="AE_LIST_TABLE"),
            SubSection(title="严重不良事件（SAE）", placeholder_key="SAE_DETAIL_TABLE"),
            SubSection(title="导致退出研究的不良事件", placeholder_key="AE_DISCONTINUATION_TABLE"),
            SubSection(title="重要不良事件", placeholder_key="IMPORTANT_AE_TABLE"),
            SubSection(title="实验室检查", placeholder_key="LABORATORY_RESULTS"),
            SubSection(title="生命体征", placeholder_key="VITAL_SIGNS_RESULTS"),
            SubSection(title="体检", placeholder_key="PHYSICAL_EXAMINATION_RESULTS"),
            SubSection(title="心电图", placeholder_key="ECG_RESULTS"),
            SubSection(title="安全性结论",
                       description="包含Plain Language解释",
                       placeholder_key="SAFETY_CONCLUSION,PLAIN_LANGUAGE_SAFETY",
                       data_source="result-interpreter",
                       automation=AutomationLevel.SEMI),
        ]
    ),

    # ---- 模块 10: 个体受试者数据列表 ----
    CSRModule(
        number=10,
        title_zh="个体受试者数据列表",
        title_en="Individual Subject Data Listings",
        description="脱敏后的个体受试者数据，以附录形式提供",
        is_required=True,
        automation=AutomationLevel.SEMI,
        data_sources=["data-preparation"],
        sub_sections=[
            SubSection(title="个体数据列表",
                       description="建议以附录形式提供脱敏数据",
                       placeholder_key="INDIVIDUAL_SUBJECT_DATA"),
        ]
    ),

    # ---- 模块 11: 疗效分析详情 ----
    CSRModule(
        number=11,
        title_zh="疗效分析详情",
        title_en="Efficacy Results Detail",
        description="主要终点、次要终点的详细分析结果",
        is_required=True,
        automation=AutomationLevel.FULL,
        data_sources=["differential-analysis", "regression-analysis"],
        sub_sections=[
            SubSection(title="主要疗效终点分析详情",
                       placeholder_key="PRIMARY_EFFICACY_DETAIL",
                       data_source="differential-analysis",
                       automation=AutomationLevel.FULL),
            SubSection(title="次要疗效终点分析详情",
                       placeholder_key="SECONDARY_EFFICACY_DETAIL",
                       automation=AutomationLevel.SEMI),
            SubSection(title="其他疗效分析",
                       placeholder_key="ADDITIONAL_EFFICACY_ANALYSES"),
        ]
    ),

    # ---- 模块 12: 安全性分析详情 ----
    CSRModule(
        number=12,
        title_zh="安全性分析详情",
        title_en="Safety Results Detail",
        description="不良事件详情、实验室检查详情、其他安全性数据",
        is_required=True,
        automation=AutomationLevel.SEMI,
        sub_sections=[
            SubSection(title="不良事件详情", placeholder_key="AE_DETAIL"),
            SubSection(title="实验室检查详情", placeholder_key="LAB_DETAIL"),
            SubSection(title="其他安全性数据", placeholder_key="ADDITIONAL_SAFETY_DATA"),
        ]
    ),

    # ---- 模块 13: 讨论与结论 ----
    CSRModule(
        number=13,
        title_zh="讨论与结论",
        title_en="Discussion and Conclusions",
        description="疗效讨论、安全性讨论、研究局限性、总体结论",
        is_required=True,
        automation=AutomationLevel.SEMI,
        data_sources=["result-interpreter"],
        sub_sections=[
            SubSection(title="疗效讨论", placeholder_key="EFFICACY_DISCUSSION"),
            SubSection(title="安全性讨论", placeholder_key="SAFETY_DISCUSSION"),
            SubSection(title="研究局限性", placeholder_key="STUDY_LIMITATIONS"),
            SubSection(title="结论",
                       description="包含Plain Language解释",
                       placeholder_key="OVERALL_CONCLUSION,PLAIN_LANGUAGE_CONCLUSION",
                       data_source="result-interpreter",
                       automation=AutomationLevel.SEMI),
        ]
    ),

    # ---- 模块 14: 表格与图表列表 ----
    CSRModule(
        number=14,
        title_zh="表格与图表列表",
        title_en="List of Tables and Figures",
        description="报告中所有表格和图表的索引清单",
        is_required=True,
        automation=AutomationLevel.FULL,
        data_sources=["chart-generator"],
        sub_sections=[
            SubSection(title="列表清单", placeholder_key="LIST_OF_TABLES"),
            SubSection(title="图表清单",
                       description="从chart-generator输出自动生成",
                       placeholder_key="LIST_OF_FIGURES,CHART_GENERATOR_REFERENCE",
                       data_source="chart-generator",
                       data_file="30_charts/",
                       automation=AutomationLevel.FULL),
        ]
    ),

    # ---- 模块 15: 参考文献 ----
    CSRModule(
        number=15,
        title_zh="参考文献",
        title_en="References",
        description="中文NMPA提交使用GB/T 7714格式，英文FDA提交使用Vancouver格式",
        is_required=True,
        automation=AutomationLevel.MANUAL,
        citation_format_note="中文: GB/T 7714, 英文: Vancouver",
        sub_sections=[
            SubSection(title="参考文献列表", placeholder_key="REFERENCES"),
        ]
    ),

    # ---- 模块 16: 附录 ----
    CSRModule(
        number=16,
        title_zh="附录",
        title_en="Appendices",
        description="研究者信息、统计分析计划（SAP）、代码、CRF、知情同意书、原始数据、证书",
        is_required=True,
        automation=AutomationLevel.MANUAL,
        sub_sections=[
            SubSection(title="附录A：研究者信息", placeholder_key="APPENDIX_A"),
            SubSection(title="附录B：统计分析计划", placeholder_key="APPENDIX_SAP"),
            SubSection(title="附录C：统计分析程序代码", placeholder_key="APPENDIX_CODE"),
            SubSection(title="附录D：病例报告表（CRF）", placeholder_key="APPENDIX_CRF"),
            SubSection(title="附录E：知情同意书", placeholder_key="APPENDIX_ICF"),
            SubSection(title="附录F：原始数据", placeholder_key="APPENDIX_RAW_DATA"),
            SubSection(title="附录G：受试者个体数据", placeholder_key="APPENDIX_INDIVIDUAL_DATA"),
            SubSection(title="附录H：证书与声明", placeholder_key="APPENDIX_CERTIFICATES"),
        ]
    ),

    # ---- 模块 17: 补充信息 ----
    CSRModule(
        number=17,
        title_zh="补充信息",
        title_en="Supplementary Information",
        description="药物非临床研究摘要、药品说明书、临床药理学数据、受试者日记卡",
        is_required=False,
        automation=AutomationLevel.MANUAL,
        sub_sections=[
            SubSection(title="药物非临床研究摘要", placeholder_key="PRECLINICAL_SUMMARY"),
            SubSection(title="药品说明书", placeholder_key="PRODUCT_LABELING"),
            SubSection(title="临床药理学数据", placeholder_key="CLINICAL_PHARMACOLOGY"),
            SubSection(title="受试者日记卡/问卷", placeholder_key="PATIENT_DIARIES"),
        ]
    ),

    # ---- 模块 18: 数据质量与质量控制 ----
    CSRModule(
        number=18,
        title_zh="数据质量与质量控制",
        title_en="Data Quality and Quality Control",
        description="数据管理流程、数据质量报告、监查与稽查",
        is_required=True,
        automation=AutomationLevel.FULL,
        data_sources=["data-preparation"],
        sub_sections=[
            SubSection(title="数据管理", placeholder_key="DATA_MANAGEMENT"),
            SubSection(title="数据质量",
                       description="从data-preparation获取质量报告",
                       placeholder_key="DATA_QUALITY,DATA_PREPARATION_QUALITY_REPORT",
                       data_source="data-preparation",
                       data_file="04_quality_report.md",
                       automation=AutomationLevel.FULL),
            SubSection(title="监查与稽查", placeholder_key="MONITORING_AND_AUDITING"),
        ]
    ),

    # ---- 模块 19: 最终报告签署页 ----
    CSRModule(
        number=19,
        title_zh="最终报告签署页",
        title_en="Signatures",
        description="主要研究者、统计分析负责人、申办方代表签署",
        is_required=True,
        automation=AutomationLevel.MANUAL,
        sub_sections=[
            SubSection(title="签署页",
                       description="主要研究者、统计分析负责人、申办方代表",
                       placeholder_key="PI_NAME,STATISTICIAN_NAME,SPONSOR_REP_NAME"),
        ]
    ),
]


def get_module_by_number(module_number: int) -> Optional[CSRModule]:
    """根据章节序号获取模块定义"""
    for module in ICH_E3_STRUCTURE:
        if module.number == module_number:
            return module
    return None


def get_modules_by_automation(level: AutomationLevel) -> List[CSRModule]:
    """根据自动化程度筛选模块"""
    return [m for m in ICH_E3_STRUCTURE if m.automation == level]


def get_modules_by_data_source(source_skill: str) -> List[CSRModule]:
    """根据数据来源skill筛选模块"""
    return [m for m in ICH_E3_STRUCTURE if source_skill in m.data_sources]


def detect_declaration_type(study_design: str) -> str:
    """
    根据研究设计自动检测声明类型

    Parameters:
    -----------
    study_design : str
        研究设计类型（auto/rct/cohort/case_control/cross_sectional/systematic_review）

    Returns:
    --------
    str: 推荐的声明类型
    """
    design_map = {
        "rct": "CONSORT",
        "randomized": "CONSORT",
        "cohort": "STROBE",
        "case_control": "STROBE",
        "cross_sectional": "STROBE",
        "systematic_review": "PRISMA",
        "meta_analysis": "PRISMA",
    }
    return design_map.get(study_design.lower(), "CONSORT")


def get_phase_name(phase_code: str) -> str:
    """将分期代码转为可读名称"""
    phase_names = {
        "phase1": "I期临床试验",
        "phase2": "II期临床试验",
        "phase3": "III期临床试验",
        "phase4": "IV期临床试验",
    }
    return phase_names.get(phase_code, phase_code)
