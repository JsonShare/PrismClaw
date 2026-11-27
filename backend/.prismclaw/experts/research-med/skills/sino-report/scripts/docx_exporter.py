"""
Word文档导出器 - 使用python-docx生成Word文档
"""

from docx import Document
from docx.shared import Inches, Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from typing import Dict, Any, List
import re


class DocxExporter:
    """Word文档导出器"""

    def __init__(self):
        self.document = None

    def export(self, content: Dict[str, Any], output_path: str):
        """
        导出为Word文档

        Parameters:
        -----------
        content : Dict
            报告内容，包含markdown字段
        output_path : str
            输出文件路径
        """
        self.document = Document()

        # 设置文档默认样式
        self._setup_styles()

        # 解析Markdown并添加到文档
        markdown = content.get('markdown', '')

        self._add_markdown_content(markdown)

        # 保存文档
        self.document.save(output_path)

    def _setup_styles(self):
        """设置文档样式"""
        # 设置默认字体
        style = self.document.styles['Normal']
        font = style.font
        font.name = 'Times New Roman'
        font.size = Pt(11)

        # 设置中文兼容
        style.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')

    def _add_markdown_content(self, markdown: str):
        """将Markdown内容添加到Word文档"""
        lines = markdown.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i]

            # 跳过空行
            if not line.strip():
                i += 1
                continue

            # 标题
            if line.startswith('# '):
                self._add_heading(line[2:], 1)
            elif line.startswith('## '):
                self._add_heading(line[3:], 2)
            elif line.startswith('### '):
                self._add_heading(line[4:], 3)
            elif line.startswith('#### '):
                self._add_heading(line[5:], 4)

            # 粗体文本
            elif '**' in line:
                self._add_bold_paragraph(line)
            # 普通段落
            else:
                self._add_paragraph(line)

            i += 1

    def _add_heading(self, text: str, level: int):
        """添加标题"""
        heading = self.document.add_heading(text, level=level)

        # 设置标题样式
        for run in heading.runs:
            run.font.name = 'Arial'
            run.font.bold = True
            if level == 1:
                run.font.size = Pt(16)
            elif level == 2:
                run.font.size = Pt(14)
            elif level == 3:
                run.font.size = Pt(12)
            else:
                run.font.size = Pt(11)

    def _add_paragraph(self, text: str):
        """添加普通段落"""
        # 清理Markdown格式
        text = self._clean_markdown(text)

        if text.strip():
            para = self.document.add_paragraph(text)
            para.paragraph_format.space_after = Pt(6)
            para.paragraph_format.line_spacing = 1.5

    def _add_bold_paragraph(self, text: str):
        """添加包含粗体的段落"""
        text = self._clean_markdown(text)

        para = self.document.add_paragraph()

        # 分割粗体和普通文本
        parts = re.split(r'\*\*([^*]+)\*\*', text)

        for i, part in enumerate(parts):
            if i % 2 == 1:  # 粗体部分
                run = para.add_run(part)
                run.bold = True
            else:  # 普通部分
                run = para.add_run(part)

            run.font.name = 'Times New Roman'
            run.font.size = Pt(11)

        para.paragraph_format.space_after = Pt(6)

    def _clean_markdown(self, text: str) -> str:
        """清理Markdown格式符号"""
        # 移除Markdown链接
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # 移除粗体标记
        text = text.replace('**', '')
        # 移除斜体标记
        text = text.replace('*', '')
        # 移除代码标记
        text = text.replace('`', '')
        return text

    def _add_table_from_markdown(self, markdown_table: str):
        """从Markdown表格创建Word表格"""
        lines = markdown_table.strip().split('\n')

        if len(lines) < 2:
            return

        # 解析表头
        header_line = lines[0].strip('| ')
        headers = [h.strip() for h in header_line.split('|')]

        # 创建表格
        table = self.document.add_table(rows=1, cols=len(headers))
        table.style = 'Table Grid'

        # 添加表头
        header_row = table.rows[0]
        for i, header in enumerate(headers):
            cell = header_row.cells[i]
            cell.text = header
            cell.paragraphs[0].runs[0].bold = True

        # 添加数据行
        for line in lines[2:]:  # 跳过分隔线和表头
            if not line.strip() or line.startswith('|'):
                continue

            cells = [c.strip() for c in line.split('|')[1:-1]]
            row = table.add_row()

            for i, cell_text in enumerate(cells):
                if i < len(row.cells):
                    row.cells[i].text = cell_text

    def add_page_break(self):
        """添加分页符"""
        self.document.add_page_break()

    def add_table(
        self,
        headers: List[str],
        rows: List[List[str]],
        caption: str = None,
    ):
        """
        添加表格

        Parameters:
        -----------
        headers : List[str]
            表头
        rows : List[List[str]]
            数据行
        caption : str
            表格标题
        """
        # 添加标题
        if caption:
            para = self.document.add_paragraph()
            run = para.add_run(caption)
            run.bold = True
            run.font.size = Pt(10)

        # 创建表格
        table = self.document.add_table(rows=1, cols=len(headers))
        table.style = 'Table Grid'

        # 添加表头
        header_row = table.rows[0]
        for i, header in enumerate(headers):
            cell = header_row.cells[i]
            cell.text = header

            # 设置表头样式
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True
                    run.font.size = Pt(10)
                    run.font.name = 'Arial'

        # 添加数据行
        for row_data in rows:
            row = table.add_row()
            for i, cell_text in enumerate(row_data):
                if i < len(row.cells):
                    cell = row.cells[i]
                    cell.text = str(cell_text)

                    for para in cell.paragraphs:
                        for run in para.runs:
                            run.font.size = Pt(10)
                            run.font.name = 'Times New Roman'

    def set_page_margins(self, top: float = 2.54, bottom: float = 2.54,
                        left: float = 3.18, right: float = 3.18):
        """
        设置页边距

        Parameters:
        -----------
        top, bottom, left, right : float
            页边距（厘米）
        """
        sections = self.document.sections
        for section in sections:
            section.top_margin = Cm(top)
            section.bottom_margin = Cm(bottom)
            section.left_margin = Cm(left)
            section.right_margin = Cm(right)
