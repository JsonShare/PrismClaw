"""
CSR格式化模块

负责CSR文档的最终格式化输出，包括：
- Markdown导出
- Word（.docx）导出
- PDF导出
- 页面布局、目录、引用格式配置
"""

from pathlib import Path
from typing import Optional


class CSRFormatter:
    """
    CSR文档格式化器

    Attributes:
    -----------
    lang : str
        语言（zh/en）
    """

    # NMPA/FDA 页面布局配置
    LAYOUT_CONFIG = {
        "zh": {
            "margin_top": "2.54cm",
            "margin_bottom": "2.54cm",
            "margin_left": "2.54cm",
            "margin_right": "2.54cm",
            "font_title": "黑体",
            "font_body": "宋体",
            "font_size_title": "小二",
            "font_size_heading": "四号",
            "font_size_body": "小四",
            "line_spacing": "1.5倍",
            "citation_style": "GB/T 7714",
        },
        "en": {
            "margin_top": "1in",
            "margin_bottom": "1in",
            "margin_left": "1in",
            "margin_right": "1in",
            "font_title": "Times New Roman",
            "font_body": "Times New Roman",
            "font_size_title": "16pt",
            "font_size_heading": "14pt",
            "font_size_body": "12pt",
            "line_spacing": "Double",
            "citation_style": "Vancouver",
        },
    }

    def __init__(self, lang: str = "zh"):
        """
        初始化格式化器

        Parameters:
        -----------
        lang : str
            语言（zh/en）
        """
        self.lang = lang
        self.config = self.LAYOUT_CONFIG.get(lang, self.LAYOUT_CONFIG["zh"])

    def export_markdown(self, content: str, output_path: Path) -> Path:
        """
        导出Markdown格式

        Parameters:
        -----------
        content : str
            CSR报告内容
        output_path : Path
            输出文件路径

        Returns:
        --------
        Path: 实际输出路径
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_path

    def export_docx(self, content: str, output_path: Path) -> Path:
        """
        导出Word（.docx）格式

        Parameters:
        -----------
        content : str
            CSR报告Markdown内容
        output_path : Path
            输出文件路径

        Returns:
        --------
        Path: 实际输出路径
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from docx import Document
            from docx.shared import Pt, Cm, Inches
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            doc = Document()

            # 设置页面布局
            section = doc.sections[0]
            if self.lang == "zh":
                section.top_margin = Cm(2.54)
                section.bottom_margin = Cm(2.54)
                section.left_margin = Cm(2.54)
                section.right_margin = Cm(2.54)
            else:
                section.top_margin = Inches(1)
                section.bottom_margin = Inches(1)
                section.left_margin = Inches(1)
                section.right_margin = Inches(1)

            # 按行解析Markdown并添加到Word文档
            for line in content.split("\n"):
                stripped = line.strip()

                if stripped.startswith("# "):
                    p = doc.add_heading(stripped[2:], level=1)
                elif stripped.startswith("## "):
                    p = doc.add_heading(stripped[3:], level=2)
                elif stripped.startswith("### "):
                    p = doc.add_heading(stripped[4:], level=3)
                elif stripped.startswith("> "):
                    p = doc.add_paragraph(stripped[2:])
                    p.style = "Intense Quote"
                elif stripped.startswith("---"):
                    doc.add_paragraph("")
                elif stripped.startswith("| "):
                    # 表格行
                    p = doc.add_paragraph(stripped)
                    p.style = "Normal"
                elif stripped == "":
                    doc.add_paragraph("")
                else:
                    doc.add_paragraph(stripped)

            doc.save(str(output_path))
            return output_path

        except ImportError:
            # python-docx 不可用时，降级为Markdown
            print("  警告：python-docx 未安装，降级为Markdown格式导出")
            fallback_path = output_path.with_suffix(".md")
            self.export_markdown(content, fallback_path)
            return fallback_path

    def export_pdf(self, content: str, output_path: Path) -> Path:
        """
        导出PDF格式

        Parameters:
        -----------
        content : str
            CSR报告Markdown内容
        output_path : Path
            输出文件路径

        Returns:
        --------
        Path: 实际输出路径
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # 尝试使用reportlab
            from reportlab.lib.pagesizes import A4, letter
            from reportlab.lib.units import cm, inch
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

            # 页面设置
            if self.lang == "zh":
                page_size = A4
                margins = (cm(2.54), cm(2.54), cm(2.54), cm(2.54))
            else:
                page_size = letter
                margins = (inch(1), inch(1), inch(1), inch(1))

            doc = SimpleDocTemplate(
                str(output_path),
                pagesize=page_size,
                leftMargin=margins[0],
                rightMargin=margins[1],
                topMargin=margins[2],
                bottomMargin=margins[3],
            )

            styles = getSampleStyleSheet()
            story = []

            for line in content.split("\n"):
                stripped = line.strip()
                if not stripped or stripped == "---":
                    story.append(Spacer(1, 6))
                elif stripped.startswith("# "):
                    story.append(Paragraph(stripped[2:], styles["Title"]))
                elif stripped.startswith("## "):
                    story.append(Paragraph(stripped[3:], styles["Heading1"]))
                elif stripped.startswith("### "):
                    story.append(Paragraph(stripped[4:], styles["Heading2"]))
                elif stripped.startswith("> "):
                    story.append(Paragraph(stripped[2:], styles["Blockquote"]))
                else:
                    story.append(Paragraph(stripped, styles["Normal"]))

            doc.build(story)
            return output_path

        except ImportError:
            print("  警告：reportlab 未安装，降级为Markdown格式导出")
            fallback_path = output_path.with_suffix(".md")
            self.export_markdown(content, fallback_path)
            return fallback_path

    def get_layout_info(self) -> dict:
        """
        获取当前语言对应的页面布局配置

        Returns:
        --------
        dict: 布局配置
        """
        return self.config.copy()
