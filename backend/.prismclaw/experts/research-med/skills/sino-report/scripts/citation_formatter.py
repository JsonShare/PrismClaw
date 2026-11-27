"""
引用格式化器 - 支持Vancouver、APA、GB/T 7714等引用格式
"""

from typing import List, Dict, Any, Optional
from datetime import datetime


class CitationFormatter:
    """引用格式化器，支持多种期刊引用风格"""

    STYLES = ['Vancouver', 'APA', 'GB/T 7714']

    def __init__(self, style: str = 'Vancouver'):
        """
        初始化引用格式化器

        Parameters:
        -----------
        style : str
            引用风格 ('Vancouver', 'APA', 'GB/T 7714')
        """
        self.style = style

    def format_reference(self, ref: Dict[str, Any]) -> str:
        """
        格式化单条引用

        Parameters:
        -----------
        ref : Dict
            引用信息字典，包含:
            - authors: 作者列表
            - title: 标题
            - journal: 期刊名
            - year: 年份
            - volume: 卷
            - issue: 期
            - pages: 页码
            - doi: DOI

        Returns:
        --------
        格式化后的引用字符串
        """
        if self.style == 'Vancouver':
            return self._format_vancouver(ref)
        elif self.style == 'APA':
            return self._format_apa(ref)
        elif self.style == 'GB/T 7714':
            return self._format_gbt7714(ref)
        else:
            return self._format_vancouver(ref)

    def format_reference_list(self, refs: List[Dict[str, Any]]) -> List[str]:
        """
        格式化引用列表

        Parameters:
        -----------
        refs : List[Dict]
            引用列表

        Returns:
        --------
        格式化后的引用字符串列表
        """
        return [self.format_reference(ref) for ref in refs]

    def _format_vancouver(self, ref: Dict[str, Any]) -> str:
        """Vancouver格式"""
        authors = ref.get('authors', [])
        if isinstance(authors, list):
            if len(authors) <= 6:
                author_str = ', '.join(authors)
            else:
                author_str = ', '.join(authors[:3]) + ', et al'
        else:
            author_str = authors

        parts = [
            author_str,
            ref.get('title', ''),
            ref.get('journal', ''),
            f"{ref.get('year', '')};{ref.get('volume', '')}",
        ]

        if ref.get('issue'):
            parts[-1] += f"({ref['issue']})"

        if ref.get('pages'):
            parts[-1] += f":{ref['pages']}"

        if ref.get('doi'):
            parts.append(f"doi: {ref['doi']}")

        return '. '.join(parts) + '.'

    def _format_apa(self, ref: Dict[str, Any]) -> str:
        """APA格式"""
        authors = ref.get('authors', [])
        if isinstance(authors, list):
            if len(authors) == 1:
                author_str = authors[0]
            elif len(authors) == 2:
                author_str = f"{authors[0]} & {authors[1]}"
            elif len(authors) <= 20:
                author_str = ', '.join(authors[:-1]) + f', & {authors[-1]}'
            else:
                author_str = ', '.join(authors[:19]) + ', ... ' + authors[-1]
        else:
            author_str = authors

        year = ref.get('year', '')
        title = ref.get('title', '')
        journal = ref.get('journal', '')
        volume = ref.get('volume', '')
        issue = ref.get('issue', '')
        pages = ref.get('pages', '')
        doi = ref.get('doi', '')

        parts = [
            f"{author_str} ({year}).",
            f"{title}.",
            f"*{journal}*",
        ]

        if volume:
            vol_str = f"{volume}"
            if issue:
                vol_str += f"({issue})"
            parts.append(vol_str)

        if pages:
            parts.append(f"{pages}")

        if doi:
            parts.append(f"https://doi.org/{doi}")

        return ' '.join(parts)

    def _format_gbt7714(self, ref: Dict[str, Any]) -> str:
        """GB/T 7714格式（顺序编码制）"""
        authors = ref.get('authors', [])
        if isinstance(authors, list):
            if len(authors) <= 3:
                author_str = ', '.join(authors)
            else:
                author_str = f"{authors[0]}, 等"
        else:
            author_str = authors

        year = ref.get('year', '')
        title = ref.get('title', '')
        journal = ref.get('journal', '')
        volume = ref.get('volume', '')
        issue = ref.get('issue', '')
        pages = ref.get('pages', '')
        doi = ref.get('doi', '')

        parts = [
            author_str,
            f"[{title}]",
            f"*{journal}*",
            f"{year}",
        ]

        if volume:
            vol_str = f"Vol.{volume}"
            if issue:
                vol_str += f", No.{issue}"
            parts.append(vol_str)

        if pages:
            parts.append(f"PP.{pages}")

        if doi:
            parts.append(f"doi: {doi}")

        return '. '.join(parts) + '.'

    def format_citation_in_text(self, ref: Dict[str, Any], style: str = 'parenthetical') -> str:
        """
        格式化文内引用

        Parameters:
        -----------
        ref : Dict
            引用信息
        style : str
            引用风格 ('parenthetical', 'narrative')

        Returns:
        --------
        格式化后的文内引用
        """
        if self.style == 'Vancouver':
            authors = ref.get('authors', [])
            if isinstance(authors, list):
                if len(authors) == 1:
                    author_str = authors[0].split()[-1]
                elif len(authors) == 2:
                    author_str = f"{authors[0].split()[-1]} & {authors[1].split()[-1]}"
                else:
                    author_str = f"{authors[0].split()[-1]} et al"
            else:
                author_str = authors.split()[-1]

            year = ref.get('year', '')

            if style == 'parenthetical':
                return f"({author_str}, {year})"
            else:
                return f"{author_str} ({year})"

        elif self.style == 'APA':
            authors = ref.get('authors', [])
            if isinstance(authors, list):
                if len(authors) == 1:
                    author_str = authors[0].split()[-1]
                elif len(authors) == 2:
                    author_str = f"{authors[0].split()[-1]} & {authors[1].split()[-1]}"
                else:
                    author_str = f"{authors[0].split()[-1]} et al"
            else:
                author_str = authors.split()[-1]

            year = ref.get('year', '')

            if style == 'parenthetical':
                return f"({author_str}, {year})"
            else:
                return f"{author_str} ({year})"

        return ""


class ReferenceManager:
    """参考文献管理器"""

    def __init__(self, style: str = 'Vancouver'):
        self.citation_formatter = CitationFormatter(style)
        self.references = []

    def add_reference(self, ref: Dict[str, Any]):
        """添加参考文献"""
        self.references.append(ref)

    def add_reference_from_text(self, text: str):
        """从文本解析并添加参考文献"""
        pass

    def generate_reference_list(self) -> str:
        """生成完整的参考文献列表"""
        formatted = self.citation_formatter.format_reference_list(self.references)

        lines = []
        for i, ref in enumerate(formatted, 1):
            if self.citation_formatter.style == 'Vancouver':
                lines.append(f"{i}. {ref}")
            else:
                lines.append(f"{ref}")

        return '\n'.join(lines)

    def cite(self, ref_id: int, style: str = 'parenthetical') -> str:
        """
        生成文内引用

        Parameters:
        -----------
        ref_id : int
            参考文献ID（从1开始）
        style : str
            引用风格

        Returns:
        --------
        文内引用字符串
        """
        if 0 < ref_id <= len(self.references):
            return self.citation_formatter.format_citation_in_text(
                self.references[ref_id - 1], style
            )
        return ""
