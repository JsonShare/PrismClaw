"""
模板引擎模块

加载ICH E3标准模板，将章节内容填充到模板中，处理占位符替换。
"""

import re
from pathlib import Path
from typing import Dict, Optional


class TemplateEngine:
    """
    CSR模板引擎

    Attributes:
    -----------
    lang : str
        语言（zh/en）
    template_path : Path
        模板文件路径
    """

    # 占位符正则：匹配 {{KEY}} 格式
    PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")

    def __init__(self, lang: str = "zh", template_dir: Optional[Path] = None):
        """
        初始化模板引擎

        Parameters:
        -----------
        lang : str
            语言（zh/en）
        template_dir : Path
            模板目录（默认使用templates/）
        """
        self.lang = lang
        if template_dir:
            self.template_dir = Path(template_dir)
        else:
            self.template_dir = Path(__file__).parent.parent / "templates"

    def get_template_path(self) -> Path:
        """获取对应语言的模板文件路径"""
        if self.lang == "en":
            path = self.template_dir / "ich_e3_template_en.md"
            if path.exists():
                return path
        # 默认使用中文模板
        return self.template_dir / "ich_e3_template.md"

    def load_template(self) -> str:
        """
        加载ICH E3模板文件

        Returns:
        --------
        str: 模板内容
        """
        template_path = self.get_template_path()
        if template_path.exists():
            with open(template_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            # 如果模板不存在，使用内联的最小模板
            return self._get_inline_template()

    def render(
        self,
        sections: Dict[int, str],
        report_type: str = "csr",
    ) -> str:
        """
        将章节内容渲染为完整的CSR报告

        Parameters:
        -----------
        sections : Dict[int, str]
            章节序号 → 章节Markdown内容
        report_type : str
            报告类型（csr/interim/safety）

        Returns:
        --------
        str: 完整的CSR Markdown文档
        """
        lines = []

        # 文档标题
        report_type_names = {
            "csr": "临床研究报告（CSR）",
            "interim": "期中分析报告",
            "safety": "安全性分析报告",
        }
        title = report_type_names.get(report_type, "临床研究报告")
        lines.append(f"# {title}")
        lines.append("")

        # 声明
        if self.lang == "zh":
            lines.append("> **声明**：本报告遵循ICH E3《临床研究报告的结构与内容》指南（CPMP/ICH/288/95），")
            lines.append("> 符合国家药品监督管理局（NMPA）临床试验报告格式要求。")
        else:
            lines.append("> **Declaration**: This report follows the ICH E3 guideline "
                        "(Structure and Content of Clinical Study Reports, CPMP/ICH/288/95).")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 封面信息占位符
        lines.extend(self._render_cover())
        lines.append("---")
        lines.append("")

        # 目录占位符
        lines.append("## 目录")
        lines.append("")
        lines.append("{{TABLE_OF_CONTENTS}}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 各章节
        for num in sorted(sections.keys()):
            content = sections.get(num, "")
            if content:
                lines.append(content)
                lines.append("")
                lines.append("---")
                lines.append("")

        # 术语表
        if self.lang == "zh":
            lines.append("> **术语表**：{{GLOSSARY}}")
        else:
            lines.append("> **Glossary**: {{GLOSSARY}}")
        lines.append("")

        # 页脚
        if self.lang == "zh":
            lines.append("*本报告模板遵循ICH E3指南（CPMP/ICH/288/95）和NMPA临床试验报告格式要求生成。*")
        else:
            lines.append("*This report follows ICH E3 guideline (CPMP/ICH/288/95).")

        return "\n".join(lines)

    def _render_cover(self) -> list:
        """渲染封面信息"""
        lines = [
            "## 封面信息",
            "",
            "| 字段 | 内容 |",
            "|------|------|",
            "| **报告标题** | {{REPORT_TITLE}} |",
            "| **方案编号** | {{PROTOCOL_ID}} |",
            "| **研究药物** | {{INVESTIGATIONAL_PRODUCT}} |",
            "| **适应症** | {{INDICATION}} |",
            "| **研究阶段** | {{STUDY_PHASE}} |",
            "| **报告类型** | {{REPORT_TYPE}} |",
            "| **申办方** | {{SPONSOR}} |",
            "| **主要研究者** | {{PRINCIPAL_INVESTIGATOR}} |",
            "| **报告日期** | {{REPORT_DATE}} |",
            "",
        ]
        return lines

    def _get_inline_template(self) -> str:
        """获取内联的最小模板（当模板文件不存在时使用）"""
        return """# 临床研究报告（CSR）

> 本报告遵循ICH E3指南。

{{REPORT_TITLE}}

{{TABLE_OF_CONTENTS}}

{{CONTENT}}
"""

    def extract_placeholders(self, text: str) -> list:
        """
        提取文本中的所有占位符

        Parameters:
        -----------
        text : str
            待分析的文本

        Returns:
        --------
        list: 占位符名称列表
        """
        return self.PLACEHOLDER_PATTERN.findall(text)

    def count_unfilled(self, text: str) -> int:
        """
        统计未填充的占位符数量

        Parameters:
        -----------
        text : str
            待分析的文本

        Returns:
        --------
        int: 未填充的占位符数量
        """
        return len(self.extract_placeholders(text))

    def generate_toc(self, sections: Dict[int, str]) -> str:
        """
        从章节内容自动生成目录

        Parameters:
        -----------
        sections : Dict[int, str]
            章节序号 → 章节内容

        Returns:
        --------
        str: Markdown格式的目录
        """
        toc_lines = []
        for num in sorted(sections.keys()):
            content = sections.get(num, "")
            # 提取第一个二级标题
            for line in content.split("\n"):
                if line.startswith("## "):
                    title = line.replace("## ", "").strip()
                    toc_lines.append(f"{num}. [{title}](#module-{num})")
                    break

        return "\n".join(toc_lines)
