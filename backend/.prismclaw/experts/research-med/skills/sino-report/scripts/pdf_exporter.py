"""
PDF文档导出器 - 使用reportlab生成PDF文档
注意：需要安装 reportlab 包（pip install reportlab）
"""

try:
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm, inch
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Image, KeepTogether
    )
    from reportlab.lib import colors
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

from pathlib import Path
from typing import Dict, Any, List
import re


class PdfExporter:
    """PDF文档导出器"""

    def __init__(self):
        self.styles = None
        self.story = []

    def export(self, content: Dict[str, Any], output_path: str):
        """
        导出为PDF文档

        Parameters:
        -----------
        content : Dict
            报告内容
        output_path : str
            输出文件路径
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError(
                "PDF导出功能需要安装 reportlab 包。"
                "请运行: pip install reportlab"
            )
        # 创建PDF文档（reportlab 不接受 Path 对象，转 str）
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            topMargin=2.54 * cm,
            bottomMargin=2.54 * cm,
            leftMargin=3.18 * cm,
            rightMargin=3.18 * cm,
        )

        # 设置样式
        self._setup_styles()

        # 解析Markdown并添加到故事
        markdown = content.get('markdown', '')
        self._add_markdown_content(markdown)

        # 构建PDF
        doc.build(self.story)

    def _setup_styles(self):
        """设置PDF样式"""
        self.styles = getSampleStyleSheet()

        # 添加自定义样式
        self.styles.add(ParagraphStyle(
            name='Title_CN',
            parent=self.styles['Title'],
            fontName='Helvetica-Bold',
            fontSize=18,
            leading=22,
            alignment=TA_CENTER,
            spaceAfter=20,
        ))

        self.styles.add(ParagraphStyle(
            name='Heading1_CN',
            parent=self.styles['Heading1'],
            fontName='Helvetica-Bold',
            fontSize=14,
            leading=18,
            spaceBefore=16,
            spaceAfter=10,
        ))

        self.styles.add(ParagraphStyle(
            name='Heading2_CN',
            parent=self.styles['Heading2'],
            fontName='Helvetica-Bold',
            fontSize=12,
            leading=16,
            spaceBefore=12,
            spaceAfter=8,
        ))

        self.styles.add(ParagraphStyle(
            name='BodyText_CN',
            parent=self.styles['BodyText'],
            fontName='Helvetica',
            fontSize=11,
            leading=16,
            alignment=TA_JUSTIFY,
            spaceAfter=8,
        ))

    def _add_markdown_content(self, markdown: str):
        """将Markdown内容添加到PDF"""
        lines = markdown.split('\n')

        for line in lines:
            line = line.strip()

            if not line:
                self.story.append(Spacer(1, 6))
                continue

            # 标题
            if line.startswith('# '):
                title = line[2:]
                self.story.append(Paragraph(title, self.styles['Title_CN']))

            elif line.startswith('## '):
                heading = line[3:]
                self.story.append(Paragraph(heading, self.styles['Heading1_CN']))

            elif line.startswith('### '):
                heading = line[4:]
                self.story.append(Paragraph(heading, self.styles['Heading2_CN']))

            # 表格
            elif line.startswith('|'):
                table_data = []
                while line.startswith('|'):
                    cells = [c.strip() for c in line.strip('|').split('|')]
                    if not all(c == '' or c == '---' for c in cells):
                        table_data.append(cells)
                    if lines.index(line) + 1 < len(lines):
                        line = lines[lines.index(line) + 1]
                        if not line.strip().startswith('|'):
                            break
                    else:
                        break

                if table_data:
                    self._add_table(table_data)

            # 段落
            else:
                text = self._clean_markdown(line)
                self.story.append(Paragraph(text, self.styles['BodyText_CN']))

    def _add_table(self, data: List[List[str]]):
        """添加表格"""
        if not data or len(data) < 2:
            return

        header = data[0]
        rows = data[1:]

        table = Table([header] + rows)

        style = TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.Color(0.95, 0.95, 0.95)]),
        ])

        table.setStyle(style)
        self.story.append(table)
        self.story.append(Spacer(1, 12))

    def _clean_markdown(self, text: str) -> str:
        """清理Markdown格式"""
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = text.replace('`', '')
        return text


class MarkdownToPDFConverter:
    """Markdown转PDF转换器"""

    def __init__(self):
        self.exporter = PdfExporter()

    def convert(self, markdown_file: str, output_file: str):
        """将Markdown文件转换为PDF"""
        with open(markdown_file, 'r', encoding='utf-8') as f:
            markdown = f.read()

        content = {'markdown': markdown}
        self.exporter.export(content, output_file)