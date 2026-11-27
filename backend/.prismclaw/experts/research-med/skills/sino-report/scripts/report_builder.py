"""
报告构建器 - 组装报告内容
"""

import json
from typing import Dict, Any, Optional
from datetime import datetime


class ReportBuilder:
    """报告构建器，负责组装报告的各个部分"""

    def __init__(self, target_journal: str = 'Lancet', citation_style: str = 'Vancouver'):
        """
        初始化报告构建器

        Parameters:
        -----------
        target_journal : str
            目标期刊
        citation_style : str
            引用格式 ('Vancouver', 'APA', 'GB/T 7714')
        """
        self.target_journal = target_journal
        self.citation_style = citation_style

    def build_report(self, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建完整报告

        Parameters:
        -----------
        report_data : Dict[str, Any]
            报告数据

        Returns:
        --------
        Dict containing 'markdown', 'docx_content', and metadata
        """
        session_data = report_data.get('session_data', {})
        report_type = report_data.get('report_type', 'full')

        sections = []

        # 1. 标题和摘要
        sections.append(self._build_title_section(report_data))

        # 2. 方法部分
        if report_data.get('include_methods', True):
            sections.append(self._build_methods_section(session_data, report_data))

        # 3. 结果部分
        if report_data.get('include_results', True):
            sections.append(self._build_results_section(session_data, report_data))

        # 4. 结果解释
        if report_data.get('include_interpretation', True):
            sections.append(self._build_interpretation_section(session_data))

        # 5. 图表
        if report_data.get('include_charts', True):
            sections.append(self._build_figures_section(session_data))

        # 组装Markdown
        markdown = '\n\n'.join(sections)

        return {
            'markdown': markdown,
            'sections': sections,
            'metadata': {
                'session_id': report_data.get('session_id'),
                'target_journal': self.target_journal,
                'report_type': report_type,
                'created_at': datetime.now().isoformat(),
            }
        }

    def _build_title_section(self, report_data: Dict[str, Any]) -> str:
        """构建标题和摘要部分"""
        lines = []

        # 标题
        title = report_data.get('title', '数据分析报告')
        lines.append(f"# {title}\n")

        # 作者信息
        if report_data.get('authors'):
            lines.append(f"\n**作者**: {report_data['authors']}")

        if report_data.get('affiliation'):
            lines.append(f"\n**单位**: {report_data['affiliation']}")

        # 摘要
        if report_data.get('abstract'):
            lines.append(f"\n## 摘要\n{report_data['abstract']}")
        else:
            lines.append(f"\n## 摘要\n")
            lines.append(f"本报告基于会话 {report_data.get('session_id', 'N/A')} 生成。")
            lines.append(f"目标期刊: {self.target_journal}。")

        return '\n'.join(lines)

    def _build_methods_section(self, session_data: Dict, report_data: Dict) -> str:
        """构建方法部分"""
        lines = []
        lines.append("## 研究方法\n")

        # 研究对象
        lines.append("### 研究对象\n")
        if 'quality_report' in session_data:
            lines.append("本研究数据来源于小龙虾医学科研数据分析平台。")
            lines.append("数据在分析前经过质量评估和预处理。\n")
        else:
            lines.append("数据来源及研究对象信息详见数据分析流程。\n")

        # 统计分析方法
        lines.append("### 统计分析方法\n")

        methods_used = []

        if '10_differential_results' in session_data:
            methods_used.append("差异分析（t检验、卡方检验等）")

        if '11_regression_results' in session_data:
            methods_used.append("回归分析（线性回归）")

        if '12_survival_results' in session_data:
            methods_used.append("生存分析（Cox回归）")

        if methods_used:
            lines.append("本研究所用的统计分析方法包括：")
            for method in methods_used:
                lines.append(f"- {method}")
            lines.append("")
        else:
            lines.append("统计分析方法详见各分析模块的输出结果。\n")

        # 方法说明规范
        journal_note = self._get_journal_methods_note()
        if journal_note:
            lines.append(f"\n{journal_note}\n")

        return '\n'.join(lines)

    def _build_results_section(self, session_data: Dict, report_data: Dict) -> str:
        """构建结果部分"""
        lines = []
        lines.append("## 研究结果\n")

        # 差异分析结果
        if '10_differential_results' in session_data:
            lines.append("### 差异分析结果\n")
            results = session_data['10_differential_results']

            if isinstance(results, dict) and 'results' in results:
                for i, result in enumerate(results.get('results', [])[:5], 1):
                    variable = result.get('variable', 'Unknown')
                    method = result.get('method', 'N/A')
                    groups = result.get('groups', [])

                    lines.append(f"**{i}. {variable}比较**\n")
                    lines.append(f"分析方法: {method}\n")

                    if 'means' in result:
                        lines.append("\n各组统计量:")
                        means = result['means']
                        for group, stats in means.items():
                            if isinstance(stats, dict):
                                mean = stats.get('mean', 0)
                                sd = stats.get('sd')
                                n = stats.get('n')
                                if sd is not None and n is not None:
                                    lines.append(f"- {group}: {mean:.2f} ± {sd:.2f} (n={n})")
                                else:
                                    lines.append(f"- {group}: {mean}")
                            else:
                                # 上游可能将样本量 n 直接放在 means 下（标量）
                                lines.append(f"- {group}: n={stats}")

                    if 'statistics' in result:
                        stats = result['statistics']
                        if 'p_raw' in stats and stats['p_raw'] is not None:
                            p_val = stats['p_raw']
                            if isinstance(p_val, str):
                                p_str = f"p {p_val}"
                            else:
                                p_str = "p < 0.001" if p_val < 0.001 else f"p = {p_val:.3f}"
                            lines.append(f"\np值: {p_str}")

                        cohens_d = stats.get('cohens_d')
                        if cohens_d is not None:
                            lines.append(f"效应量 (Cohen's d): {cohens_d:.2f}")
                        ef = stats.get('effect_size')
                        if ef is not None:
                            lines.append(f"效应量: {ef}")

                    lines.append("")
            else:
                lines.append("差异分析结果详见输出文件。\n")

        # 回归分析结果
        if '11_regression_results' in session_data:
            lines.append("### 回归分析结果\n")
            lines.append("多因素回归分析结果详见输出文件。\n")

        # 生存分析结果
        if '12_survival_results' in session_data:
            lines.append("### 生存分析结果\n")
            lines.append("生存分析结果详见输出文件。\n")

        return '\n'.join(lines)

    def _build_interpretation_section(self, session_data: Dict) -> str:
        """构建结果解释部分"""
        lines = []
        lines.append("## 结果解释\n")

        if '20_interpretation' in session_data:
            interpretation = session_data['20_interpretation']
            lines.append(interpretation)
        else:
            lines.append("结果解释详见 sino-interpreter 模块的输出。")

        return '\n'.join(lines)

    def _build_figures_section(self, session_data: Dict) -> str:
        """构建图表部分"""
        lines = []
        lines.append("## 图表\n")

        if 'charts' in session_data and session_data['charts']:
            lines.append("本研究生成的图表包括：\n")
            for i, chart in enumerate(session_data['charts'], 1):
                chart_name = chart.name if hasattr(chart, 'name') else str(chart)
                lines.append(f"- 图{i}: {chart_name}")
            lines.append("")
            lines.append("图表文件详见输出目录的 30_charts/ 文件夹。")
        else:
            lines.append("本研究未生成图表，或图表文件位于输出目录的 30_charts/ 文件夹中。")

        return '\n'.join(lines)

    def _get_journal_methods_note(self) -> str:
        """获取期刊特定的方法说明"""
        notes = {
            'Lancet': "本研究的报告遵循CONSORT声明（随机对照试验）或STROBE声明（观察性研究）的相关要求。",
            'NEJM': "本研究的报告遵循ICMJE建议和CONSORT/STROBE声明的要求。",
            'JAMA': "本研究的报告遵循JAMA期刊的报告规范和相关声明要求。",
            'BMJ': "本研究的报告遵循BMJ期刊的报告规范和相关声明要求。",
            'CMJ': "本研究的报告遵循中华医学杂志的报告规范要求。",
        }
        return notes.get(self.target_journal, "")


class ContentAssembler:
    """内容组装器，用于组装不同来源的内容"""

    @staticmethod
    def assemble_table(header: list, rows: list, caption: str = None) -> str:
        """
        组装Markdown表格

        Parameters:
        -----------
        header : list
            表头
        rows : list
            数据行
        caption : str
            表格标题

        Returns:
        --------
        Markdown格式的表格
        """
        lines = []

        if caption:
            lines.append(f"**{caption}**\n")

        # 表头
        lines.append("| " + " | ".join(str(h) for h in header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")

        # 数据行
        for row in rows:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")

        return '\n'.join(lines)

    @staticmethod
    def format_statistical_result(
        method: str,
        statistic: float,
        df: int = None,
        p_value: float = None,
        effect_size: float = None,
        ci: tuple = None,
    ) -> str:
        """
        格式化统计结果

        Parameters:
        -----------
        method : str
            统计方法名称
        statistic : float
            统计量值
        df : int
            自由度
        p_value : float
            P值
        effect_size : float
            效应量
        ci : tuple
            置信区间 (lower, upper)

        Returns:
        --------
        格式化后的统计结果字符串
        """
        parts = [f"{method}"]

        if df is not None:
            parts.append(f"(df={df})")

        parts.append(f"统计量={statistic:.3f}")

        if p_value is not None:
            if p_value < 0.001:
                parts.append("p<0.001")
            else:
                parts.append(f"p={p_value:.3f}")

        if effect_size is not None:
            parts.append(f"效应量={effect_size:.3f}")

        if ci is not None:
            parts.append(f"95%CI: [{ci[0]:.2f}, {ci[1]:.2f}]")

        return ", ".join(parts)