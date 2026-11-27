"""
表格生成器 - 生成符合期刊要求的三线表
"""

from typing import List, Dict, Any, Optional, Tuple
import numpy as np


class TableGenerator:
    """表格生成器，用于生成符合期刊格式的统计表格"""

    def __init__(self, style: str = 'three_line'):
        """
        初始化表格生成器

        Parameters:
        -----------
        style : str
            表格样式 ('three_line', 'grid', 'simple')
        """
        self.style = style

    def create_baseline_table(
        self,
        data: List[Dict],
        groups: List[str],
        variables: List[str],
        precision: int = 2,
    ) -> Dict[str, Any]:
        """
        创建基线特征表（Table 1）

        通常包含：
        - 连续变量：均值 ± 标准差 或 中位数 (四分位距)
        - 分类变量：n (%)

        Parameters:
        -----------
        data : List[Dict]
            数据列表
        groups : List[str]
            分组名称
        variables : List[str]
            变量名称
        precision : int
            小数位数

        Returns:
        --------
        Dict containing table structure
        """
        header = ['Variable'] + groups + ['Total', 'P value']

        rows = []

        for var in variables:
            row = [var]

            # 计算各组统计量
            for group in groups:
                values = [d.get(var) for d in data if d.get('group') == group and d.get(var) is not None]

                if self._is_continuous(values):
                    mean, sd = self._calc_mean_sd(values)
                    cell = f"{mean:.{precision}f} ± {sd:.{precision}f}"
                else:
                    n, pct = self._calc_n_percent(values, len(values))
                    cell = f"{n} ({pct:.{precision}f}%)"

                row.append(cell)

            # Total列
            all_values = [d.get(var) for d in data if d.get(var) is not None]
            if self._is_continuous(all_values):
                mean, sd = self._calc_mean_sd(all_values)
                row.append(f"{mean:.{precision}f} ± {sd:.{precision}f}")
            else:
                n, pct = self._calc_n_percent(sum(1 for d in data if d.get(var) is not None), len(data))
                row.append(f"{n}")

            row.append("")  # P value placeholder
            rows.append(row)

        return {
            'header': header,
            'rows': rows,
            'caption': 'Table 1. Baseline Characteristics',
        }

    def create_results_table(
        self,
        results: List[Dict[str, Any]],
        precision: int = 3,
    ) -> Dict[str, Any]:
        """
        创建主要结果表（Table 2）

        Parameters:
        -----------
        results : List[Dict]
            分析结果列表
        precision : int
            小数位数

        Returns:
        --------
        Dict containing table structure
        """
        header = ['Variable', 'Group', 'Mean ± SD', 'Statistic', 'P value', 'Effect Size', '95% CI']

        rows = []

        for result in results:
            variable = result.get('variable', '')
            method = result.get('method', '')

            if 'means' in result:
                for group, stats in result['means'].items():
                    mean = stats.get('mean', 0)
                    sd = stats.get('sd', 0)
                    n = stats.get('n', 0)
                    cell = f"{mean:.{precision}f} ± {sd:.{precision}f} (n={n})"

                    row = [variable, group, cell]

                    if 'statistics' in result:
                        stat = result['statistics']
                        stat_value = stat.get('t', stat.get('F', stat.get('chi2', 0)))
                        row.append(f"{stat_value:.{precision}f}")

                        p = stat.get('p_raw', stat.get('p', 1))
                        row.append(f"{self._format_p(p)}")

                        es = stat.get('cohens_d', stat.get('eta_squared', stat.get('cramers_v')))
                        if es:
                            row.append(f"{es:.{precision}f}")
                        else:
                            row.append("")

                        ci = stat.get('ci', [])
                        if len(ci) == 2:
                            row.append(f"[{ci[0]:.{precision}f}, {ci[1]:.{precision}f}]")
                        else:
                            row.append("")
                    else:
                        row.extend(['', '', '', ''])

                    rows.append(row)
                    variable = ''  # 只在第一行显示变量名

        return {
            'header': header,
            'rows': rows,
            'caption': 'Table 2. Primary Results',
        }

    def create_regression_table(
        self,
        coefficients: List[Dict],
        precision: int = 3,
    ) -> Dict[str, Any]:
        """
        创建回归分析结果表

        Parameters:
        -----------
        coefficients : List[Dict]
            回归系数列表
        precision : int
            小数位数

        Returns:
        --------
        Dict containing table structure
        """
        header = ['Variable', 'β', 'SE', 'β (standardized)', 'Z/T', 'P value', '95% CI', 'VIF']

        rows = []

        for coef in coefficients:
            var = coef.get('variable', '')
            beta = coef.get('beta', 0)
            se = coef.get('se', 0)
            beta_std = coef.get('beta_std', 0)
            stat = coef.get('stat', 0)
            p = coef.get('p', 1)
            ci = coef.get('ci', [])
            vif = coef.get('vif', 1)

            row = [
                var,
                f"{beta:.{precision}f}",
                f"{se:.{precision}f}",
                f"{beta_std:.{precision}f}",
                f"{stat:.{precision}f}",
                self._format_p(p),
                f"[{ci[0]:.{precision}f}, {ci[1]:.{precision}f}]" if len(ci) == 2 else "",
                f"{vif:.{precision}f}" if vif else "",
            ]

            rows.append(row)

        return {
            'header': header,
            'rows': rows,
            'caption': 'Table 3. Regression Analysis Results',
        }

    def _is_continuous(self, values: List) -> bool:
        """判断是否为连续变量"""
        if not values:
            return True

        try:
            numeric_values = [float(v) for v in values if v is not None]
            if len(numeric_values) < len(values):
                return False

            unique_ratio = len(set(numeric_values)) / len(numeric_values)
            return unique_ratio > 0.1
        except (ValueError, TypeError):
            return False

    def _calc_mean_sd(self, values: List) -> Tuple[float, float]:
        """计算均值和标准差"""
        numeric_values = [float(v) for v in values if v is not None]
        if not numeric_values:
            return 0, 0

        mean = np.mean(numeric_values)
        sd = np.std(numeric_values, ddof=1)
        return mean, sd

    def _calc_n_percent(self, count: int, total: int) -> Tuple[int, float]:
        """计算数量和百分比"""
        pct = (count / total * 100) if total > 0 else 0
        return count, pct

    def _format_p(self, p: float) -> str:
        """格式化P值"""
        if p < 0.001:
            return "<0.001"
        elif p < 0.01:
            return f"{p:.4f}"
        else:
            return f"{p:.3f}"

    def to_markdown(self, table: Dict[str, Any]) -> str:
        """
        将表格转换为Markdown格式

        Parameters:
        -----------
        table : Dict
            表格数据

        Returns:
        --------
        Markdown格式的表格字符串
        """
        lines = []

        if table.get('caption'):
            lines.append(f"**{table['caption']}**\n")

        header = table['header']
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")

        for row in table.get('rows', []):
            lines.append("| " + " | ".join(str(c) for c in row) + " |")

        return '\n'.join(lines)


class TableStyler:
    """表格样式器，用于设置表格样式"""

    # 三线表样式
    THREE_LINE_TOP = 1.5
    THREE_LINE_BOTTOM = 1.5
    THREE_LINE_MIDDLE = 0.5

    @staticmethod
    def apply_three_line_style(cell):
        """应用三线表样式到单元格"""
        cell.border.top.width = 0.5
        cell.border.bottom.width = 0.5
        cell.paragraphs[0].alignment = 1  # center

    @staticmethod
    def set_column_width(table, column_widths: List[float]):
        """设置列宽"""
        for row in table.rows:
            for i, width in enumerate(column_widths):
                if i < len(row.cells):
                    row.cells[i].width = width
