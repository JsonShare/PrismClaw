"""
章节内容构建器模块

负责根据上游分析结果自动构建CSR各章节的具体内容。
对于全自动章节，直接从数据生成内容；
对于半自动/手动章节，生成结构化的占位符说明。
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from csr_structure import (
    ICH_E3_STRUCTURE, CSRModule, AutomationLevel,
    detect_declaration_type, get_phase_name
)


class SectionBuilder:
    """
    CSR章节内容构建器

    Attributes:
    -----------
    context_path : Path
        数据桩路径
    lang : str
        语言（zh/en）
    study_phase : str
        研究分期
    report_type : str
        报告类型
    study_design : str
        研究设计
    """

    def __init__(
        self,
        context_path: Path,
        lang: str = "zh",
        study_phase: str = "phase3",
        report_type: str = "csr",
        study_design: str = "auto",
        protocol_id: str = "",
        available_data: Optional[Dict] = None,
    ):
        self.context_path = Path(context_path)
        self.lang = lang
        self.study_phase = study_phase
        self.report_type = report_type
        self.study_design = study_design
        self.protocol_id = protocol_id
        self.available_data = available_data or {}

    def build_all(self) -> Dict[int, str]:
        """
        构建所有章节内容

        Returns:
        --------
        Dict[int, str]: 章节序号 → 章节Markdown内容
        """
        sections = {}

        for module in ICH_E3_STRUCTURE:
            content = self._build_module(module)
            sections[module.number] = content

        return sections

    def _build_module(self, module: CSRModule) -> str:
        """构建单个模块的内容"""
        # 模块标题（根据语言选择）
        title = module.title_zh if self.lang == "zh" else module.title_en
        required_mark = "" if module.is_required else "（可选）"

        lines = [
            f"## {module.number}. {module.title_zh}{required_mark}" if self.lang == "zh"
            else f"## {module.number}. {module.title_en}",
            "",
            module.description,
            "",
        ]

        # 根据模块类型构建具体内容
        builder_map = {
            1: self._build_administrative_info,
            2: self._build_declaration,
            3: self._build_investigators,
            4: self._build_introduction,
            5: self._build_objectives,
            6: self._build_study_design,
            7: self._build_study_subjects,
            8: self._build_efficacy,
            9: self._build_safety,
            10: self._build_individual_data,
            11: self._build_efficacy_detail,
            12: self._build_safety_detail,
            13: self._build_discussion,
            14: self._build_tables_figures,
            15: self._build_references,
            16: self._build_appendices,
            17: self._build_supplementary,
            18: self._build_data_quality,
            19: self._build_signatures,
        }

        builder = builder_map.get(module.number)
        if builder:
            module_content = builder(module)
            lines.extend(module_content)
        else:
            lines.append(f"<!-- 模块 {module.number} 内容待填充 -->")
            lines.append("")

        return "\n".join(lines)

    def _build_administrative_info(self, module: CSRModule) -> List[str]:
        """模块1：研究报告基本信息"""
        return [
            "### 1.1 方案及 amendments",
            "",
            f"- 方案编号：{{{{PROTOCOL_ID}}}}{'（已提供：' + self.protocol_id + '）' if self.protocol_id else '（待补充）'}",
            "- 方案标题：{{PROTOCOL_TITLE}}",
            "- 方案版本：{{PROTOCOL_VERSION}}（日期：{{PROTOCOL_DATE}}）",
            "- 方案 amendments 列表：{{AMENDMENTS_LIST}}",
            "",
            "### 1.2 伦理委员会信息",
            "",
            "| 伦理委员会名称 | 批准日期 | 批准编号 |",
            "|--------------|---------|---------|",
            "| {{IRB_1_NAME}} | {{IRB_1_DATE}} | {{IRB_1_NUMBER}} |",
            "",
            "### 1.3 试验注册信息",
            "",
            "- 临床试验注册号：{{CLINICAL_TRIAL_ID}}",
            "- 注册平台：{{REGISTRY_PLATFORM}}（如中国临床试验注册中心 ChiCTR）",
            "",
            "### 1.4 知情同意",
            "",
            "- 知情同意书版本：{{ICF_VERSION}}（日期：{{ICF_DATE}}）",
            "",
        ]

    def _build_declaration(self, module: CSRModule) -> List[str]:
        """模块2：声明（自动检测）"""
        design = self.study_design if self.study_design != "auto" else "rct"
        declaration = detect_declaration_type(design)

        lines = [
            f"**声明类型：{declaration}**",
            "",
        ]

        if declaration == "CONSORT":
            lines.extend([
                "本研究为随机对照试验（RCT），报告遵循 **CONSORT 2010** 声明指南。",
                "",
                "> CONSORT（Consolidated Standards of Reporting Trials）声明是国际公认的",
                "> 随机对照试验报告规范，确保研究报告的透明性和可重复性。",
                "",
            ])
        elif declaration == "STROBE":
            lines.extend([
                "本研究为观察性研究，报告遵循 **STROBE** 声明指南。",
                "",
                "> STROBE（Strengthening the Reporting of Observational Studies in Epidemiology）",
                "> 是观察性流行病学研究报告的国际规范。",
                "",
            ])
        elif declaration == "PRISMA":
            lines.extend([
                "本研究为系统综述/Meta分析，报告遵循 **PRISMA 2020** 声明指南。",
                "",
                "> PRISMA（Preferred Reporting Items for Systematic Reviews and Meta-Analyses）",
                "> 是系统综述和Meta分析报告的国际规范。",
                "",
            ])

        lines.append("{{DECLARATION_BODY}}")
        lines.append("")
        return lines

    def _build_investigators(self, module: CSRModule) -> List[str]:
        """模块3：研究者及研究机构"""
        return [
            "### 3.1 主要研究者",
            "",
            "| 姓名 | 职称 | 机构 | 联系方式 |",
            "|------|------|------|---------|",
            "| {{PI_NAME}} | {{PI_TITLE}} | {{PI_INSTITUTION}} | {{PI_CONTACT}} |",
            "",
            "### 3.2 各中心研究者列表",
            "",
            "| 中心编号 | 中心名称 | 主要研究者 | 入组例数 |",
            "|---------|---------|-----------|---------|",
            "| {{CENTER_LIST}} |",
            "",
        ]

    def _build_introduction(self, module: CSRModule) -> List[str]:
        """模块4：引言"""
        return [
            "### 4.1 研究背景",
            "",
            "{{INTRODUCTION_BACKGROUND}}",
            "",
            "### 4.2 研究药物",
            "",
            "{{DRUG_DESCRIPTION}}",
            "",
            "### 4.3 适应症及现有治疗",
            "",
            "{{INDICATION_AND_EXISTING_TREATMENT}}",
            "",
            "### 4.4 研究理论基础",
            "",
            "{{SCIENTIFIC_RATIONALE}}",
            "",
        ]

    def _build_objectives(self, module: CSRModule) -> List[str]:
        """模块5：研究目的"""
        return [
            "### 5.1 主要目的",
            "",
            "{{PRIMARY_OBJECTIVE_DETAIL}}",
            "",
            "### 5.2 次要目的",
            "",
            "{{SECONDARY_OBJECTIVES_DETAIL}}",
            "",
            "### 5.3 研究假设",
            "",
            "{{STUDY_HYPOTHESIS}}",
            "",
        ]

    def _build_study_design(self, module: CSRModule) -> List[str]:
        """模块6：研究设计与方法"""
        phase_name = get_phase_name(self.study_phase)

        lines = [
            "### 6.1 总体研究设计",
            "",
            f"- **研究分期**：{phase_name}",
            "- **研究类型**：{{STUDY_TYPE}}（如随机对照、前瞻性、多中心）",
            "- **研究设计**：{{STUDY_DESIGN_DETAIL}}",
            "- **设盲方式**：{{BLINDING_METHOD}}",
            "- **研究分组**：{{TREATMENT_ARMS}}",
            "",
            "### 6.2 研究人群",
            "",
            "#### 入选标准",
            "{{INCLUSION_CRITERIA}}",
            "",
            "#### 排除标准",
            "{{EXCLUSION_CRITERIA}}",
            "",
            "### 6.3 给药方案",
            "",
            "| 组别 | 药物 | 剂量 | 给药途径 | 给药频率 | 疗程 |",
            "|------|------|------|---------|---------|------|",
            "| {{TREATMENT_REGIMEN_TABLE}} |",
            "",
            "### 6.4 疗效评价指标",
            "",
            "#### 主要疗效终点",
            "{{PRIMARY_ENDPOINT_DEFINITION}}",
            "",
            "#### 次要疗效终点",
            "{{SECONDARY_ENDPOINTS_DEFINITION}}",
            "",
            "### 6.7 统计分析方法",
            "",
            "#### 样本量估算",
            "{{SAMPLE_SIZE_CALCULATION}}",
            "",
        ]

        # 如果有power-analysis结果，引用
        if "power_analysis" in self.available_data:
            lines.append(f"> 数据来源：power_analysis_result.md（power-analysis skill输出）")
            lines.append("")

        lines.extend([
            "#### 分析人群定义",
            "- **全分析集（FAS）**：{{FAS_DEFINITION}}",
            "- **符合方案集（PPS）**：{{PPS_DEFINITION}}",
            "- **安全性分析集（SS）**：{{SS_DEFINITION}}",
            "",
            "#### 主要疗效分析方法",
            "{{PRIMARY_ANALYSIS_METHOD}}",
            "",
            "#### 多重比较校正",
            "{{MULTIPLE_COMPARISON_CORRECTION}}",
            "",
        ])

        return lines

    def _build_study_subjects(self, module: CSRModule) -> List[str]:
        """模块7：研究受试者"""
        return [
            "### 7.1 受试者处置",
            "",
            "> 数据来源：data-preparation skill 输出的 01_prepared_data.csv",
            "",
            "| | 试验组 (N={{N_EXP}}) | 对照组 (N={{N_CTRL}}) | 合计 (N={{N_TOTAL}}) |",
            "|---|---|---|---|",
            "| 筛选 | {{SCREENED_EXP}} | {{SCREENED_CTRL}} | {{SCREENED_TOTAL}} |",
            "| 随机化 | {{RANDOMIZED_EXP}} | {{RANDOMIZED_CTRL}} | {{RANDOMIZED_TOTAL}} |",
            "| 完成 | {{COMPLETED_EXP}} | {{COMPLETED_CTRL}} | {{COMPLETED_TOTAL}} |",
            "| 提前退出 | {{DISCONTINUED_EXP}} | {{DISCONTINUED_CTRL}} | {{DISCONTINUED_TOTAL}} |",
            "",
            "#### 受试者流程图",
            "",
            "{{CONSORT_FLOW_DIAGRAM}}",
            "",
            "> 建议附CONSORT流程图，符合CONSORT声明要求。",
            "",
            "### 7.2 方案违背",
            "",
            "{{PROTOCOL_DEVIATIONS_TABLE}}",
            "",
        ]

    def _build_efficacy(self, module: CSRModule) -> List[str]:
        """模块8：有效性分析"""
        lines = [
            "### 8.2 受试者人口学特征与基线特征（Table 1）",
            "",
        ]

        if "differential_results" in self.available_data:
            lines.append("> 数据来源：differential-analysis skill 输出的 10_differential_results.json")
            lines.append("")

            # 尝试读取差异分析结果
            diff_path = self.available_data["differential_results"]
            if diff_path.exists():
                try:
                    with open(diff_path, "r", encoding="utf-8") as f:
                        diff_data = json.load(f)
                    lines.append("{{DEMOGRAPHICS_TABLE}} <!-- 从差异分析结果自动生成 -->")
                    lines.append("")
                    if isinstance(diff_data, dict) and "results" in diff_data:
                        lines.append("{{DEMOGRAPHICS_COMPARISON_NOTE}} <!-- 基线比较P值 -->")
                        lines.append("")
                except Exception:
                    lines.append("{{DEMOGRAPHICS_TABLE}} <!-- 需要从差异分析结果生成 -->")
                    lines.append("")
        else:
            lines.append("> **注意**：未找到差异分析结果。请先执行 `/differential-analysis` 生成基线比较数据。")
            lines.append("{{DEMOGRAPHICS_TABLE}}")
            lines.append("")

        lines.extend([
            "### 8.5 主要疗效终点分析",
            "",
            "| 统计量 | 试验组 | 对照组 | 组间差异 (95% CI) | P值 |",
            "|--------|--------|--------|-------------------|-----|",
            "| {{PRIMARY_ENDPOINT_RESULTS}} |",
            "",
        ])

        if "regression_results" in self.available_data:
            lines.extend([
                "### 8.8 多因素分析",
                "",
                "> 数据来源：regression-analysis skill 输出的 11_regression_results.json",
                "",
                "{{REGRESSION_RESULTS}}",
                "",
            ])

        if "survival_results" in self.available_data:
            lines.extend([
                "### 8.9 生存分析",
                "",
                "> 数据来源：survival-analysis skill 输出的 12_survival_results.json",
                "",
                "{{SURVIVAL_RESULTS}}",
                "",
            ])

        if "plain_language" in self.available_data:
            lines.extend([
                "### 8.10 有效性结论",
                "",
                "> 以下为Plain Language解释（result-interpreter skill输出）：",
                "> {{PLAIN_LANGUAGE_EFFICACY}}",
                "",
            ])

        return lines

    def _build_safety(self, module: CSRModule) -> List[str]:
        """模块9：安全性分析"""
        return [
            "> **注意**：本章为CSR必需章节，不可省略。",
            "",
            "### 9.1 安全性分析人群",
            "",
            "- 安全性分析集例数：{{N_SAFETY}}",
            "",
            "### 9.2 暴露程度",
            "",
            "| | 试验组 | 对照组 |",
            "|---|---|---|",
            "| 暴露例数 | {{EXPOSED_EXP}} | {{EXPOSED_CTRL}} |",
            "| 暴露时间（中位数） | {{EXPOSURE_DURATION_EXP}} | {{EXPOSURE_DURATION_CTRL}} |",
            "",
            "### 9.3 不良事件（AE）总体分析",
            "",
            "{{AE_LIST_TABLE}}",
            "",
            "### 9.11 安全性结论",
            "",
            "{{SAFETY_CONCLUSION}}",
            "",
        ]

    def _build_individual_data(self, module: CSRModule) -> List[str]:
        """模块10：个体受试者数据列表"""
        return [
            "{{INDIVIDUAL_SUBJECT_DATA}}",
            "",
            "> 建议以附录形式提供脱敏个体数据。",
            "",
        ]

    def _build_efficacy_detail(self, module: CSRModule) -> List[str]:
        """模块11：疗效分析详情"""
        return [
            "{{PRIMARY_EFFICACY_DETAIL}}",
            "",
        ]

    def _build_safety_detail(self, module: CSRModule) -> List[str]:
        """模块12：安全性分析详情"""
        return [
            "{{AE_DETAIL}}",
            "",
        ]

    def _build_discussion(self, module: CSRModule) -> List[str]:
        """模块13：讨论与结论"""
        lines = [
            "### 13.1 疗效讨论",
            "",
            "{{EFFICACY_DISCUSSION}}",
            "",
            "### 13.2 研究局限性",
            "",
            "{{STUDY_LIMITATIONS}}",
            "",
            "### 13.3 结论",
            "",
            "{{OVERALL_CONCLUSION}}",
            "",
        ]

        if "plain_language" in self.available_data:
            lines.extend([
                "> 以下为Plain Language解释（result-interpreter skill输出）：",
                "> {{PLAIN_LANGUAGE_CONCLUSION}}",
                "",
            ])

        return lines

    def _build_tables_figures(self, module: CSRModule) -> List[str]:
        """模块14：表格与图表列表"""
        lines = [
            "### 14.1 列表清单",
            "",
            "| 编号 | 标题 | 页码 |",
            "|------|------|------|",
            "| {{LIST_OF_TABLES}} |",
            "",
            "### 14.2 图表清单",
            "",
        ]

        if "charts_dir" in self.available_data:
            charts_dir = self.available_data["charts_dir"]
            if charts_dir.is_dir():
                chart_files = sorted(charts_dir.glob("*"))
                if chart_files:
                    lines.append("> 图表来源：chart-generator skill 输出")
                    lines.append("")
                    lines.append("| 编号 | 标题 | 页码 |")
                    lines.append("|------|------|------|")
                    for i, f in enumerate(chart_files, 1):
                        lines.append(f"| 图{i} | {f.name} | |")
                    lines.append("")

        return lines

    def _build_references(self, module: CSRModule) -> List[str]:
        """模块15：参考文献"""
        if self.lang == "zh":
            return [
                "> 引用格式：GB/T 7714（中文NMPA提交）",
                "",
                "{{REFERENCES}}",
                "",
            ]
        else:
            return [
                "> 引用格式：Vancouver（英文FDA提交）",
                "",
                "{{REFERENCES}}",
                "",
            ]

    def _build_appendices(self, module: CSRModule) -> List[str]:
        """模块16：附录"""
        return [
            "### 附录A：研究者信息",
            "{{APPENDIX_A}}",
            "",
            "### 附录B：统计分析计划（SAP）",
            "{{APPENDIX_SAP}}",
            "",
            "### 附录C：统计分析程序代码",
            "{{APPENDIX_CODE}}",
            "",
            "### 附录D：病例报告表（CRF）",
            "{{APPENDIX_CRF}}",
            "",
        ]

    def _build_supplementary(self, module: CSRModule) -> List[str]:
        """模块17：补充信息"""
        return [
            "{{PRECLINICAL_SUMMARY}}",
            "",
        ]

    def _build_data_quality(self, module: CSRModule) -> List[str]:
        """模块18：数据质量与质量控制"""
        lines = [
            "### 18.1 数据管理",
            "",
            "{{DATA_MANAGEMENT}}",
            "",
            "### 18.2 数据质量",
            "",
        ]

        if "quality_report" in self.available_data:
            lines.append("> 数据质量报告来源：data-preparation skill 输出的 04_quality_report.md")
            lines.append("")
            try:
                qr_path = self.available_data["quality_report"]
                with open(qr_path, "r", encoding="utf-8") as f:
                    lines.append(f.read())
            except Exception:
                lines.append("{{DATA_QUALITY}} <!-- 从质量报告自动读取 -->")
        else:
            lines.append("{{DATA_QUALITY}}")

        lines.append("")
        return lines

    def _build_signatures(self, module: CSRModule) -> List[str]:
        """模块19：最终报告签署页"""
        return [
            "| 角色 | 姓名 | 签名 | 日期 |",
            "|------|------|------|------|",
            "| 主要研究者 | {{PI_NAME}} | | |",
            "| 统计分析负责人 | {{STATISTICIAN_NAME}} | | |",
            "| 申办方代表 | {{SPONSOR_REP_NAME}} | | |",
            "",
        ]
