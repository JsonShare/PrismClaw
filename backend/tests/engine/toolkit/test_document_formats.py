"""新增文档格式（RTF/ODF/EPUB/OFD/HTML/MHTML/CSV/TSV/XLS）的读取覆盖。

解析层直接断言块模型；对外契约由 read 分支断言。构造真实的最小合法文件，
不使用 mock——格式解析的正确性只能在真文件上验证。
"""

from __future__ import annotations

import asyncio
import io
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prismclaw.engine.runtime.in_process_client import InProcessRuntimeClient  # noqa: E402
from prismclaw.engine.toolkit.builtin.file_tools import ReadTool  # noqa: E402
from prismclaw.engine.toolkit.file.workspace import WorkspaceResolver  # noqa: E402
from prismclaw.engine.toolkit.office.document_reader import (  # noqa: E402
    open_doc,
    page_count,
    parse_page,
)
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext  # noqa: E402
from prismclaw.schema.agent.data_types import TextBlock  # noqa: E402
from prismclaw.schema.config.config import WorkspaceConfig  # noqa: E402


def _read_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace_id="ws", workspace_path=tmp_path, runtime=InProcessRuntimeClient())


def _read(tmp_path: Path, path: str, **extra) -> str:
    tool = ReadTool(WorkspaceResolver(WorkspaceConfig()))
    result = asyncio.run(
        tool.execute({"file_path": path, "_call_id": "c1", **extra}, _read_ctx(tmp_path))
    )
    text = "".join(b.text for b in result.content if isinstance(b, TextBlock))
    return ("ERR: " if result.is_error else "") + text


# --------------------------------------------------------------------------
# 解析层：直接验证块模型
# --------------------------------------------------------------------------
def _parse(raw: bytes, fmt: str, tmp_path: Path):
    doc = open_doc(raw, fmt, workspace_path=tmp_path, source_rel_path=f"x.{fmt}")
    return [parse_page(doc, p) for p in range(1, page_count(doc, fmt) + 1)]


def test_rtf_yields_text_blocks(tmp_path):
    raw = r"{\rtf1\ansi\deff0 First paragraph.\par Second line \u20013?\u25991?}"
    pages = _parse(raw.encode("latin-1"), "rtf", tmp_path)
    text = " ".join(b.text for b in pages[0])
    assert "First paragraph" in text
    assert "Second line" in text


def _docx_with_rotated_image(tmp_path: Path, degrees: int) -> Path:
    """构造含一张旋转图片的 docx（模拟 Word 里「旋转 90°/270°」的扫描件）。

    真实故障形态：``word/media/`` 存未旋转位图，旋转只写在
    ``pic:spPr/a:xfrm/@rot``（1/60000 度）。python-docx 无旋转 API，
    因此在保存后改写 document.xml。
    """
    import docx
    from PIL import Image

    img_path = tmp_path / "page.png"
    # 竖向底图：旋转 90°/270° 后应变成横向，0° 保持竖向。
    Image.new("RGB", (40, 120), (255, 255, 255)).save(img_path)
    doc = docx.Document()
    doc.add_paragraph("首页正文")
    doc.add_picture(str(img_path))
    raw_path = tmp_path / "rotated.docx"
    doc.save(raw_path)

    rot = degrees * 60000
    buf = io.BytesIO()
    with zipfile.ZipFile(raw_path) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "word/document.xml":
                xml = data.decode("utf-8")
                # xfrm 可能已存在（带 off/ext）或不存在，两种都覆盖。
                if "<a:xfrm" in xml:
                    xml = xml.replace("<a:xfrm", f'<a:xfrm rot="{rot}"', 1)
                else:
                    xml = xml.replace(
                        "<a:prstGeom",
                        f'<a:xfrm rot="{rot}"/><a:prstGeom',
                        1,
                    )
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    raw_path.write_bytes(buf.getvalue())
    return raw_path


@pytest.mark.parametrize(
    "degrees,expect_landscape",
    [(0, False), (90, True), (270, True)],
)
def test_docx_image_rotation_is_applied(tmp_path, degrees, expect_landscape):
    """图片按 docx 声明的旋转角落盘，而不是交给视觉模型猜方向。

    未应用旋转时，文字倒转 90°，模型只能编造数值（实测同一页出现
    8.8/11.16/13.87 三个不同 WBC）并把 max_tokens 耗尽。
    """
    from PIL import Image

    raw_path = tmp_path / "rotated.docx"
    raw_path.write_bytes(_docx_with_rotated_image(tmp_path, degrees).read_bytes())

    ws = tmp_path / "ws"
    ws.mkdir()
    rel = "sessions/s1/inbox/x.docx"
    (ws / "sessions/s1/inbox").mkdir(parents=True)
    (ws / rel).write_bytes(raw_path.read_bytes())

    doc = open_doc((ws / rel).read_bytes(), "docx", workspace_path=ws, source_rel_path=rel)
    blocks = parse_page(doc, 1, workspace_path=ws, source_rel_path=rel)
    images = [b for b in blocks if b.kind == "image"]
    assert len(images) == 1
    saved = Image.open(ws / images[0].source_path)
    assert (saved.width > saved.height) is expect_landscape


def _pptx_with_rotated_image(tmp_path: Path, degrees: int) -> bytes:
    """构造含一张声明旋转的 pptx。

    ``ppt/media/`` 存未旋转位图；旋转写在 ``p:pic > p:spPr > a:xfrm/@rot``
    （1/60000 度），与 docx 同一约定。python-pptx 的 ``shape.rotation`` 正好
    写这个属性，因此直接用官方 API 生成。
    """
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches

    img_path = tmp_path / "page.png"
    # 竖向底图：旋转 90°/270° 后应变成横向。
    Image.new("RGB", (40, 120), (255, 255, 255)).save(img_path)
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "标题"
    pic = slide.shapes.add_picture(str(img_path), Inches(1), Inches(1))
    pic.rotation = degrees
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize(
    "degrees,expect_landscape",
    [(0, False), (90, True), (270, True)],
)
def test_pptx_embedded_image_rotation_is_applied(tmp_path, degrees, expect_landscape):
    """pptx 内嵌图按 ``a:xfrm/@rot`` 还原后再落盘。

    与 docx 同一真实故障形态：媒体目录存未旋转位图，直接交给视觉模型会
    看到倒转 90° 的页面。
    """
    from PIL import Image

    from prismclaw.engine.toolkit.office.document_reader import structured_office_images

    raw = _pptx_with_rotated_image(tmp_path, degrees)
    blocks = structured_office_images(
        raw, "pptx", workspace_path=tmp_path, source_rel_path="deck.pptx"
    )
    assert len(blocks) == 1
    assert blocks[0].kind == "image"
    assert blocks[0].mime_type == "image/png"
    saved = Image.open(tmp_path / blocks[0].source_path)
    assert (saved.width > saved.height) is expect_landscape


def _xlsx_with_rotated_image(tmp_path: Path, degrees: int) -> bytes:
    """构造含一张声明旋转的 xlsx。

    openpyxl 没有旋转 API，保存后改写 ``xl/drawings/drawingN.xml``：在
    ``xdr:pic > xdr:spPr`` 下插入带命名空间的 ``a:xfrm``（未声明命名空间
    会让 openpyxl 自身解析失败，说明该命名空间是结构的一部分）。
    """
    import openpyxl
    from openpyxl.drawing.image import Image as XLImage
    from PIL import Image

    img_path = tmp_path / "page.png"
    # 竖向底图：旋转 90°/270° 后应变成横向。
    Image.new("RGB", (40, 120), (255, 255, 255)).save(img_path)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "标题"
    ws.add_image(XLImage(str(img_path)), "B2")
    src = io.BytesIO()
    wb.save(src)

    drawing = "xl/drawings/drawing1.xml"
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(src.getvalue())) as zin,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.namelist():
            data = zin.read(item)
            if item == drawing:
                xml = data.decode("utf-8")
                xfrm = f'<a:xfrm xmlns:a="{_A_NS}" rot="{degrees * 60000}"/>' if degrees else ""
                xml = xml.replace("<spPr>", f"<spPr>{xfrm}", 1)
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    return out.getvalue()


_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


@pytest.mark.parametrize(
    "degrees,expect_landscape",
    [(0, False), (90, True), (270, True)],
)
def test_xlsx_embedded_image_rotation_is_applied(tmp_path, degrees, expect_landscape):
    """xlsx 内嵌图经 sheet → drawing → media 关系取到，并按声明的旋转角还原。"""
    from PIL import Image

    from prismclaw.engine.toolkit.office.document_reader import structured_office_images

    raw = _xlsx_with_rotated_image(tmp_path, degrees)
    blocks = structured_office_images(
        raw, "xlsx", workspace_path=tmp_path, source_rel_path="book.xlsx"
    )
    assert len(blocks) == 1
    assert blocks[0].kind == "image"
    saved = Image.open(tmp_path / blocks[0].source_path)
    assert (saved.width > saved.height) is expect_landscape


def test_structured_office_images_without_pictures_is_empty(tmp_path):
    """无内嵌图的 xlsx/pptx 返回空列表，不报错（图片是可选内容）。"""
    import openpyxl
    from pptx import Presentation

    from prismclaw.engine.toolkit.office.document_reader import structured_office_images

    wb = openpyxl.Workbook()
    wb.active["A1"] = "只有文本"
    buf = io.BytesIO()
    wb.save(buf)
    assert structured_office_images(buf.getvalue(), "xlsx", workspace_path=tmp_path) == []

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "只有文本"
    buf = io.BytesIO()
    prs.save(buf)
    assert structured_office_images(buf.getvalue(), "pptx", workspace_path=tmp_path) == []


def _make_odf(body: str, mime: str) -> bytes:
    from odf import draw, table, text
    from odf.opendocument import (
        OpenDocumentPresentation,
        OpenDocumentSpreadsheet,
        OpenDocumentText,
    )

    if mime.endswith("text"):
        doc = OpenDocumentText()
        doc.text.addElement(text.H(outlinelevel=1, text="标题一"))
        doc.text.addElement(text.P(text=body))
        lst = text.List()
        for item in ("条目A", "条目B"):
            li = text.ListItem()
            li.addElement(text.P(text=item))
            lst.addElement(li)
        doc.text.addElement(lst)
        tbl = table.Table(name="T1")
        tr = table.TableRow()
        for value in ("C1", "C2"):
            cell = table.TableCell()
            cell.addElement(text.P(text=value))
            tr.addElement(cell)
        tbl.addElement(tr)
        doc.text.addElement(tbl)
    elif mime.endswith("spreadsheet"):
        doc = OpenDocumentSpreadsheet()
        tbl = table.Table(name="S1")
        tr = table.TableRow()
        for value in ("A1", "B1"):
            cell = table.TableCell()
            cell.addElement(text.P(text=value))
            tr.addElement(cell)
        tbl.addElement(tr)
        doc.spreadsheet.addElement(tbl)
    else:
        doc = OpenDocumentPresentation()
        for i in range(1, 3):
            pg = draw.Page(masterpagename="Default", name=f"S{i}")
            frame = draw.Frame()
            box = draw.TextBox()
            box.addElement(text.P(text=f"第{i}页"))
            frame.addElement(box)
            pg.addElement(frame)
            doc.presentation.addElement(pg)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_odt_heading_paragraph_list_and_table(tmp_path):
    raw = _make_odf("正文段落", "application/vnd.oasis.opendocument.text")
    pages = _parse(raw, "odt", tmp_path)
    assert len(pages) == 1
    texts = [b.text for b in pages[0]]
    assert any("标题一" in t for t in texts)
    assert any("正文段落" in t for t in texts)
    # 列表项是正文，不能被只遍历直接子节点的实现吞掉
    assert "条目A" in texts and "条目B" in texts
    tables = [b for b in pages[0] if b.kind == "table"]
    assert len(tables) == 1 and tables[0].rows[0] == ("C1", "C2")


def test_ods_yields_table_block(tmp_path):
    raw = _make_odf("", "application/vnd.oasis.opendocument.spreadsheet")
    pages = _parse(raw, "ods", tmp_path)
    tables = [b for b in pages[0] if b.kind == "table"]
    assert len(tables) == 1
    assert tables[0].rows[0] == ("A1", "B1")


def test_odp_paginates_by_slide(tmp_path):
    raw = _make_odf("", "application/vnd.oasis.opendocument.presentation")
    pages = _parse(raw, "odp", tmp_path)
    assert len(pages) == 2
    assert any("第1页" in b.text for b in pages[0])
    assert any("第2页" in b.text for b in pages[1])


def test_epub_skips_nav_document(tmp_path):
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("id")
    book.set_title("书名")
    book.set_language("zh")
    chapters = []
    for i in (1, 2):
        ch = epub.EpubHtml(title=f"Ch{i}", file_name=f"c{i}.xhtml", lang="zh")
        ch.content = f"<html><body><h1>第{i}章</h1><p>正文{i}</p></body></html>"
        book.add_item(ch)
        chapters.append(ch)
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    buf = io.BytesIO()
    epub.write_epub(buf, book)

    pages = _parse(buf.getvalue(), "epub", tmp_path)
    # 2 个章节页；nav/ncx 被排除，不得出现「Ch1/Ch2」目录噪音
    assert len(pages) == 2
    all_text = " ".join(b.text for pg in pages for b in pg)
    assert "第1章" in all_text and "第2章" in all_text
    assert "Ch1" not in all_text


def test_ofd_extracts_text_and_page_images(tmp_path):
    """按 GB/T 33190 结构：OFD.xml → DocRoot → Document.xml → Pages。"""
    ofd_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ofd:OFD xmlns:ofd="http://www.ofdspec.org/2016" DocType="OFD" Version="1.0">'
        "<ofd:DocBody><ofd:DocInfo><ofd:DocID>1</ofd:DocID></ofd:DocInfo>"
        "<ofd:DocRoot>Doc_0/Document.xml</ofd:DocRoot></ofd:DocBody></ofd:OFD>"
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ofd:Document xmlns:ofd="http://www.ofdspec.org/2016">'
        '<ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/Page_0/Content.xml"/>'
        '<ofd:Page ID="2" BaseLoc="Pages/Page_1/Content.xml"/></ofd:Pages>'
        "<ofd:Resources><ofd:MultiMedias>"
        '<ofd:MultiMedia ID="10"><ofd:MediaFile>Res/img_0.png</ofd:MediaFile>'
        "</ofd:MultiMedia></ofd:MultiMedias></ofd:Resources></ofd:Document>"
    )
    page0_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016"><ofd:Content>'
        '<ofd:Layer ID="2"><ofd:TextObject ID="3">'
        "<ofd:TextCode>发票正文</ofd:TextCode>"
        "</ofd:TextObject></ofd:Layer></ofd:Content></ofd:Page>"
    )
    page1_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016"><ofd:Content>'
        '<ofd:Layer ID="2"><ofd:ImageObject ID="11" ResourceID="10" '
        'Boundary="10 10 80 80"/></ofd:Layer></ofd:Content></ofd:Page>'
    )
    # 1x1 PNG：足以验证「资源被找到并落盘」，不依赖图像内容
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db4"
        "0000000049454e44ae426082"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", ofd_xml)
        zf.writestr("Doc_0/Document.xml", document_xml)
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", page0_xml)
        zf.writestr("Doc_0/Pages/Page_1/Content.xml", page1_xml)
        zf.writestr("Doc_0/Res/img_0.png", png)

    pages = _parse(buf.getvalue(), "ofd", tmp_path)
    assert len(pages) == 2
    assert any("发票正文" in b.text for b in pages[0])
    images = [b for b in pages[1] if b.kind == "image"]
    assert len(images) == 1
    assert images[0].source_path and images[0].source_path.endswith(".png")
    assert (tmp_path / images[0].source_path).read_bytes() == png


def test_html_strips_script(tmp_path):
    raw = b"<html><body><h1>T</h1><p>P</p><script>var x=1;</script></body></html>"
    pages = _parse(raw, "html", tmp_path)
    text = " ".join(b.text for b in pages[0])
    assert "T" in text and "P" in text
    assert "var x" not in text


def test_mhtml_reads_html_part(tmp_path):
    mhtml = (
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/related; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        "<html><body><h1>邮件标题</h1><p>正文</p><script>x=1</script></body></html>\r\n"
        "--B--\r\n"
    )
    pages = _parse(mhtml.encode("utf-8"), "mhtml", tmp_path)
    text = " ".join(b.text for b in pages[0])
    assert "邮件标题" in text and "正文" in text
    assert "x=1" not in text


@pytest.mark.parametrize("sep", [",", "\t"])
def test_csv_tsv_yield_table(tmp_path, sep):
    raw = f"姓名{sep}年龄\n张三{sep}30\n".encode()
    pages = _parse(raw, "csv", tmp_path)
    tables = [b for b in pages[0] if b.kind == "table"]
    assert len(tables) == 1
    assert tables[0].rows[0] == ("姓名", "年龄")
    assert tables[0].rows[1] == ("张三", "30")


def test_xls_yields_table(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    book = xlwt.Workbook()
    sheet = book.add_sheet("数据")
    sheet.write(0, 0, "姓名")
    sheet.write(1, 0, "张三")
    buf = io.BytesIO()
    book.save(buf)

    pages = _parse(buf.getvalue(), "xls", tmp_path)
    tables = [b for b in pages[0] if b.kind == "table"]
    assert len(tables) == 1
    assert tables[0].rows[0][0] == "姓名"


# --------------------------------------------------------------------------
# 对外契约：read 分支
# --------------------------------------------------------------------------
def test_read_html_end_to_end(tmp_path):
    (tmp_path / "a.html").write_text(
        "<html><body><h1>页面标题</h1><p>页面正文</p></body></html>", encoding="utf-8"
    )
    out = _read(tmp_path, "a.html")
    assert out.startswith("[read: a.html")
    assert "页面标题" in out and "页面正文" in out


def test_read_csv_end_to_end_renders_table(tmp_path):
    (tmp_path / "a.csv").write_text("姓名,年龄\n张三,30\n", encoding="utf-8")
    out = _read(tmp_path, "a.csv")
    assert "[table]" in out
    assert "姓名" in out


def test_read_doc_ppt_gives_conversion_hint(tmp_path):
    for ext in (".doc", ".ppt"):
        (tmp_path / f"old{ext}").write_bytes(b"\xd0\xcf\x11\xe0")
        out = _read(tmp_path, f"old{ext}")
        assert out.startswith("ERR:")
        assert "旧式二进制 Office 格式" in out


def test_read_corrupt_odt_gives_readable_error(tmp_path):
    (tmp_path / "broken.odt").write_bytes(b"not a zip at all")
    out = _read(tmp_path, "broken.odt")
    assert out.startswith("ERR:")
    assert "无法解析" in out or "损坏" in out
    assert "KeyError" not in out


def test_read_pptx_lists_embedded_images(tmp_path):
    """read(.pptx) 既保留 tf 锚点文本，也列出内嵌图片路径供二次 read。"""
    (tmp_path / "deck.pptx").write_bytes(_pptx_with_rotated_image(tmp_path, 270))
    out = _read(tmp_path, "deck.pptx")
    assert not out.startswith("ERR:")
    assert "slide#1/tf#1" in out
    assert "内嵌图片 1 张" in out
    assert "使用 read 识别图片内容" in out


def test_read_xlsx_lists_embedded_images(tmp_path):
    """read(.xlsx) 既保留单元格锚点文本，也列出内嵌图片路径。"""
    (tmp_path / "book.xlsx").write_bytes(_xlsx_with_rotated_image(tmp_path, 90))
    out = _read(tmp_path, "book.xlsx")
    assert not out.startswith("ERR:")
    assert "s#1!r#1c#1" in out
    assert "内嵌图片 1 张" in out
