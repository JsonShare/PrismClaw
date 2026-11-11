#!/usr/bin/env python3
"""
pdf_toolkit.py — All-in-one PDF toolkit: merge, split, rotate, watermark, encrypt/decrypt, images2pdf, pdf2images.

Usage:
    # Merge multiple PDFs
    python3 pdf_toolkit.py merge --inputs a.pdf b.pdf c.pdf --out merged.pdf

    # Split PDF (extract pages)
    python3 pdf_toolkit.py split --input doc.pdf --pages 1-3,5,8-10 --out extract.pdf
    python3 pdf_toolkit.py split --input doc.pdf --every 5 --out-dir ./chunks/

    # Rotate pages
    python3 pdf_toolkit.py rotate --input doc.pdf --angle 90 --out rotated.pdf
    python3 pdf_toolkit.py rotate --input doc.pdf --angle 90 --pages 2,4-6 --out rotated.pdf

    # Add watermark
    python3 pdf_toolkit.py watermark --input doc.pdf --text "CONFIDENTIAL" --out marked.pdf
    python3 pdf_toolkit.py watermark --input doc.pdf --stamp logo.pdf --out marked.pdf

    # Encrypt PDF
    python3 pdf_toolkit.py encrypt --input doc.pdf --password secret --out locked.pdf

    # Decrypt PDF
    python3 pdf_toolkit.py decrypt --input locked.pdf --password secret --out unlocked.pdf

    # Images to PDF
    python3 pdf_toolkit.py images2pdf --inputs img1.png img2.jpg --out album.pdf

    # PDF to images (one per page)
    python3 pdf_toolkit.py pdf2images --input doc.pdf --out-dir ./pages/

    # PDF to single long image
    python3 pdf_toolkit.py pdf2images --input doc.pdf --mode long --out long.png

Exit codes: 0 success, 1 bad args / file not found, 2 missing dep, 3 runtime error
"""

import argparse
import importlib.util
import json
import math
import os
import sys
import traceback


def ensure_deps():
    missing = [p for p in ("pypdf", "reportlab") if importlib.util.find_spec(p) is None]
    if importlib.util.find_spec("PIL") is None:
        missing.append("Pillow")
    if importlib.util.find_spec("fitz") is None:
        missing.append("pymupdf")
    if missing:
        import subprocess
        import platform
        cmd = [sys.executable, "-m", "pip", "install", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "-q"] + missing
        if platform.system() == "Linux":
            cmd.insert(-len(missing), "--break-system-packages")
        subprocess.check_call(cmd)


ensure_deps()

from pypdf import PdfReader, PdfWriter


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_page_ranges(spec: str, total: int) -> list[int]:
    """Parse a page specification like '1-3,5,8-10' into 0-based indices.

    Pages in the spec are 1-based. Returns sorted, deduplicated list.
    """
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo = max(1, int(lo))
            hi = min(total, int(hi))
            pages.update(range(lo, hi + 1))
        else:
            p = int(part)
            if 1 <= p <= total:
                pages.add(p)
    return sorted(pages)


def _result_json(out_path: str, **extra) -> dict:
    result = {
        "status": "ok",
        "out": out_path,
        "size_kb": os.path.getsize(out_path) // 1024,
    }
    result.update(extra)
    return result


def _ensure_out_dir(out_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)


# ── Merge ─────────────────────────────────────────────────────────────────────

def cmd_merge(inputs: list[str], out: str, **_kw) -> dict:
    """Merge multiple PDF files into one."""
    writer = PdfWriter()
    page_counts = []

    for fpath in inputs:
        if not os.path.exists(fpath):
            return {"status": "error", "error": f"File not found: {fpath}"}
        reader = PdfReader(fpath)
        page_counts.append(len(reader.pages))
        for page in reader.pages:
            writer.add_page(page)

    _ensure_out_dir(out)
    with open(out, "wb") as f:
        writer.write(f)

    return _result_json(out,
                        total_pages=len(writer.pages),
                        input_count=len(inputs),
                        page_counts=page_counts)


# ── Split ─────────────────────────────────────────────────────────────────────

def cmd_split(input: str, out: str = "", pages: str = "",
              every: int = 0, out_dir: str = "", **_kw) -> dict:
    """Split/extract pages from a PDF.

    Two modes:
      --pages '1-3,5'  -> extract those pages into a single output PDF
      --every N         -> split into chunks of N pages, written to --out-dir
    """
    if not os.path.exists(input):
        return {"status": "error", "error": f"File not found: {input}"}

    reader = PdfReader(input)
    total = len(reader.pages)

    # Mode 1: extract specific pages
    if pages:
        indices = _parse_page_ranges(pages, total)
        if not indices:
            return {"status": "error", "error": f"No valid pages in '{pages}' (PDF has {total} pages)"}
        writer = PdfWriter()
        for idx in indices:
            writer.add_page(reader.pages[idx - 1])
        _ensure_out_dir(out)
        with open(out, "wb") as f:
            writer.write(f)
        return _result_json(out, extracted_pages=indices, total_source_pages=total)

    # Mode 2: split into chunks
    if every > 0:
        target_dir = out_dir or os.path.dirname(os.path.abspath(input))
        os.makedirs(target_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(input))[0]
        chunk_count = math.ceil(total / every)
        outputs = []
        for i in range(chunk_count):
            start = i * every
            end = min(start + every, total)
            writer = PdfWriter()
            for j in range(start, end):
                writer.add_page(reader.pages[j])
            chunk_path = os.path.join(target_dir, f"{base}_part{i + 1}.pdf")
            with open(chunk_path, "wb") as f:
                writer.write(f)
            outputs.append({"path": chunk_path, "pages": end - start})
        return {
            "status": "ok",
            "chunks": len(outputs),
            "total_source_pages": total,
            "outputs": outputs,
        }

    return {"status": "error", "error": "Specify either --pages or --every"}


# ── Rotate ────────────────────────────────────────────────────────────────────

def cmd_rotate(input: str, out: str, angle: int = 90, pages: str = "", **_kw) -> dict:
    """Rotate pages in a PDF by 90/180/270 degrees clockwise."""
    if angle not in (90, 180, 270):
        return {"status": "error", "error": f"Angle must be 90, 180, or 270 (got {angle})"}
    if not os.path.exists(input):
        return {"status": "error", "error": f"File not found: {input}"}

    reader = PdfReader(input)
    writer = PdfWriter()
    total = len(reader.pages)

    if pages:
        target_indices = set(_parse_page_ranges(pages, total))
    else:
        target_indices = set(range(1, total + 1))

    rotated = []
    for i, page in enumerate(reader.pages):
        if (i + 1) in target_indices:
            page.rotate(angle)
            rotated.append(i + 1)
        writer.add_page(page)

    _ensure_out_dir(out)
    with open(out, "wb") as f:
        writer.write(f)

    return _result_json(out,
                        total_pages=total,
                        rotated_pages=rotated,
                        angle=angle)


# ── Watermark ─────────────────────────────────────────────────────────────────

def _create_text_watermark(text: str, page_width: float, page_height: float,
                           font_size: int = 48, opacity: float = 0.15,
                           angle: float = 45.0, mode: str = "center") -> bytes:
    """Generate a single-page PDF with text watermark.

    Modes:
      center — single text drawn at page center
      full   — tiled grid covering the entire page
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import io
    import re

    # Detect CJK characters and register Chinese font if needed
    has_cjk = bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', text))
    if has_cjk:
        font_name = "SourceHanSerifCN-Bold"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            font_path = os.path.join(os.path.dirname(__file__),
                                     "..", "create_pdf", "fonts", "SourceHanSerifCN",
                                     "SourceHanSerifCN-Bold.ttf")
            pdfmetrics.registerFont(TTFont(font_name, font_path))
    else:
        font_name = "Helvetica-Bold"

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    c.saveState()
    c.setFillColor(HexColor("#888888"))
    c.setFillAlpha(opacity)
    c.setFont(font_name, font_size)

    if mode == "full":
        # Tile watermark text across the entire page in a rotated grid
        text_width = c.stringWidth(text, font_name, font_size)
        spacing_x = text_width + font_size * 2
        spacing_y = font_size * 3
        # Compute how far we need to extend the grid to cover corners after rotation
        diag = math.hypot(page_width, page_height)
        cx, cy = page_width / 2, page_height / 2
        c.translate(cx, cy)
        c.rotate(angle)
        half = diag / 2
        y = -half
        while y < half:
            x = -half
            while x < half:
                c.drawCentredString(x, y, text)
                x += spacing_x
            y += spacing_y
    else:
        # Single centered watermark
        cx, cy = page_width / 2, page_height / 2
        c.translate(cx, cy)
        c.rotate(angle)
        c.drawCentredString(0, 0, text)

    c.restoreState()
    c.save()
    buf.seek(0)
    return buf.read()


def cmd_watermark(input: str, out: str, text: str = "", stamp: str = "",
                  font_size: int = 48, opacity: float = 0.15,
                  angle: float = 45.0, mode: str = "center",
                  pages: str = "", **_kw) -> dict:
    """Add a text or PDF-stamp watermark to pages."""
    if not text and not stamp:
        return {"status": "error", "error": "Specify --text or --stamp"}
    if not os.path.exists(input):
        return {"status": "error", "error": f"File not found: {input}"}
    if stamp and not os.path.exists(stamp):
        return {"status": "error", "error": f"Stamp file not found: {stamp}"}

    reader = PdfReader(input)
    writer = PdfWriter()
    total = len(reader.pages)

    if pages:
        target_indices = set(_parse_page_ranges(pages, total))
    else:
        target_indices = set(range(1, total + 1))

    # Prepare stamp reader
    stamp_reader = None
    if stamp:
        stamp_reader = PdfReader(stamp)

    marked = []
    for i, page in enumerate(reader.pages):
        if (i + 1) in target_indices:
            if stamp_reader:
                page.merge_page(stamp_reader.pages[0])
            else:
                mb = page.mediabox
                pw = float(mb.width)
                ph = float(mb.height)
                wm_bytes = _create_text_watermark(text, pw, ph, font_size, opacity, angle, mode)
                import io
                wm_reader = PdfReader(io.BytesIO(wm_bytes))
                page.merge_page(wm_reader.pages[0])
            marked.append(i + 1)
        writer.add_page(page)

    _ensure_out_dir(out)
    with open(out, "wb") as f:
        writer.write(f)

    return _result_json(out,
                        total_pages=total,
                        watermarked_pages=marked,
                        watermark_type="stamp" if stamp else "text",
                        watermark_mode=mode if not stamp else "stamp")


# ── Encrypt ───────────────────────────────────────────────────────────────────

def cmd_encrypt(input: str, out: str, password: str,
                owner_password: str = "", **_kw) -> dict:
    """Encrypt a PDF with a user password (and optional owner password)."""
    if not os.path.exists(input):
        return {"status": "error", "error": f"File not found: {input}"}

    reader = PdfReader(input)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    # Copy metadata
    if reader.metadata:
        writer.add_metadata(reader.metadata)

    owner = owner_password or password
    writer.encrypt(user_password=password, owner_password=owner)

    _ensure_out_dir(out)
    with open(out, "wb") as f:
        writer.write(f)

    return _result_json(out,
                        total_pages=len(reader.pages),
                        encrypted=True,
                        has_owner_password=bool(owner_password))


# ── Decrypt ───────────────────────────────────────────────────────────────────

def cmd_decrypt(input: str, out: str, password: str, **_kw) -> dict:
    """Decrypt a password-protected PDF."""
    if not os.path.exists(input):
        return {"status": "error", "error": f"File not found: {input}"}

    reader = PdfReader(input)

    if not reader.is_encrypted:
        return {"status": "error", "error": "PDF is not encrypted"}

    if not reader.decrypt(password):
        return {"status": "error", "error": "Incorrect password"}

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    if reader.metadata:
        writer.add_metadata(reader.metadata)

    _ensure_out_dir(out)
    with open(out, "wb") as f:
        writer.write(f)

    return _result_json(out,
                        total_pages=len(reader.pages),
                        decrypted=True)


# ── Images to PDF ─────────────────────────────────────────────────────────────

def cmd_images2pdf(inputs: list[str], out: str, **_kw) -> dict:
    """Combine multiple images into a single PDF. Each image becomes one page."""
    from PIL import Image as PILImage

    images = []
    for fpath in inputs:
        if not os.path.exists(fpath):
            return {"status": "error", "error": f"File not found: {fpath}"}
        img = PILImage.open(fpath)
        if img.mode == "RGBA":
            bg = PILImage.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        images.append(img)

    if not images:
        return {"status": "error", "error": "No images provided"}

    _ensure_out_dir(out)
    first, rest = images[0], images[1:]
    first.save(out, "PDF", resolution=72.0, save_all=True, append_images=rest)

    return _result_json(out, total_pages=len(images), image_count=len(images))


# ── PDF to Images ─────────────────────────────────────────────────────────────

def cmd_pdf2images(input: str, out: str = "", out_dir: str = "",
                   mode: str = "pages", dpi: int = 300,
                   format: str = "png", pages: str = "", **_kw) -> dict:
    """Render PDF pages to images.

    Modes:
      pages — one image per page, saved to --out-dir
      long  — all pages stitched vertically into a single long image, saved to --out
    """
    import fitz  # pymupdf
    from PIL import Image as PILImage

    if not os.path.exists(input):
        return {"status": "error", "error": f"File not found: {input}"}

    doc = fitz.open(input)
    total = len(doc)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    if pages:
        indices = [i - 1 for i in _parse_page_ranges(pages, total)]
    else:
        indices = list(range(total))

    if mode == "long":
        # Render all selected pages, stitch vertically
        if not out:
            return {"status": "error", "error": "--out is required for long mode"}
        pil_images = []
        for idx in indices:
            pix = doc[idx].get_pixmap(matrix=mat)
            img = PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)
            pil_images.append(img)

        total_height = sum(img.height for img in pil_images)
        max_width = max(img.width for img in pil_images)
        long_img = PILImage.new("RGB", (max_width, total_height), (255, 255, 255))

        y_offset = 0
        for img in pil_images:
            long_img.paste(img, (0, y_offset))
            y_offset += img.height

        _ensure_out_dir(out)
        long_img.save(out, quality=95)
        doc.close()

        return _result_json(out,
                            total_source_pages=total,
                            rendered_pages=len(indices),
                            width=max_width,
                            height=total_height,
                            mode="long")

    # mode == "pages"
    target_dir = out_dir or os.path.dirname(os.path.abspath(input))
    os.makedirs(target_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(input))[0]
    fmt = format.lower()

    outputs = []
    for idx in indices:
        pix = doc[idx].get_pixmap(matrix=mat)
        img_path = os.path.join(target_dir, f"{base}_page{idx + 1}.{fmt}")
        pix.save(img_path)
        outputs.append({"path": img_path, "page": idx + 1,
                        "width": pix.width, "height": pix.height})

    doc.close()
    return {
        "status": "ok",
        "total_source_pages": total,
        "rendered_pages": len(outputs),
        "mode": "pages",
        "format": fmt,
        "dpi": dpi,
        "outputs": outputs,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="All-in-one PDF toolkit: merge, split, rotate, watermark, encrypt/decrypt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # merge
    p_merge = sub.add_parser("merge", help="Merge multiple PDFs into one")
    p_merge.add_argument("--inputs", nargs="+", required=True, help="Input PDF files")
    p_merge.add_argument("--out", required=True, help="Output PDF path")

    # split
    p_split = sub.add_parser("split", help="Split/extract pages from a PDF")
    p_split.add_argument("--input", required=True, help="Input PDF file")
    p_split.add_argument("--pages", default="", help="Page spec, e.g. '1-3,5,8-10'")
    p_split.add_argument("--every", type=int, default=0, help="Split every N pages")
    p_split.add_argument("--out", default="", help="Output PDF (for --pages mode)")
    p_split.add_argument("--out-dir", default="", help="Output directory (for --every mode)")

    # rotate
    p_rotate = sub.add_parser("rotate", help="Rotate pages in a PDF")
    p_rotate.add_argument("--input", required=True, help="Input PDF file")
    p_rotate.add_argument("--out", required=True, help="Output PDF path")
    p_rotate.add_argument("--angle", type=int, required=True, choices=[90, 180, 270],
                          help="Rotation angle (clockwise)")
    p_rotate.add_argument("--pages", default="", help="Page spec (default: all pages)")

    # watermark
    p_wm = sub.add_parser("watermark", help="Add watermark to PDF pages")
    p_wm.add_argument("--input", required=True, help="Input PDF file")
    p_wm.add_argument("--out", required=True, help="Output PDF path")
    wm_group = p_wm.add_mutually_exclusive_group(required=True)
    wm_group.add_argument("--text", help="Watermark text")
    wm_group.add_argument("--stamp", help="Watermark PDF file (overlay first page)")
    p_wm.add_argument("--font-size", type=int, default=48, help="Font size (default: 48)")
    p_wm.add_argument("--opacity", type=float, default=0.15, help="Opacity 0.0-1.0 (default: 0.15)")
    p_wm.add_argument("--angle", type=float, default=45.0, help="Rotation angle (default: 45)")
    p_wm.add_argument("--mode", choices=["center", "full"], default="center",
                       help="Watermark layout: center (single) or full (tiled grid)")
    p_wm.add_argument("--pages", default="", help="Page spec (default: all pages)")

    # encrypt
    p_enc = sub.add_parser("encrypt", help="Encrypt a PDF with password")
    p_enc.add_argument("--input", required=True, help="Input PDF file")
    p_enc.add_argument("--out", required=True, help="Output PDF path")
    p_enc.add_argument("--password", required=True, help="User password")
    p_enc.add_argument("--owner-password", default="", help="Owner password (default: same as user)")

    # decrypt
    p_dec = sub.add_parser("decrypt", help="Decrypt a password-protected PDF")
    p_dec.add_argument("--input", required=True, help="Input PDF file")
    p_dec.add_argument("--out", required=True, help="Output PDF path")
    p_dec.add_argument("--password", required=True, help="Password")

    # images2pdf
    p_i2p = sub.add_parser("images2pdf", help="Combine images into a single PDF")
    p_i2p.add_argument("--inputs", nargs="+", required=True,
                        help="Input image files (png, jpg, webp, etc.)")
    p_i2p.add_argument("--out", required=True, help="Output PDF path")

    # pdf2images
    p_p2i = sub.add_parser("pdf2images", help="Render PDF pages to images")
    p_p2i.add_argument("--input", required=True, help="Input PDF file")
    p_p2i.add_argument("--mode", choices=["pages", "long"], default="pages",
                        help="pages: one image per page; long: single stitched image")
    p_p2i.add_argument("--out", default="", help="Output image path (for long mode)")
    p_p2i.add_argument("--out-dir", default="", help="Output directory (for pages mode)")
    p_p2i.add_argument("--dpi", type=int, default=300, help="Resolution (default: 300)")
    p_p2i.add_argument("--format", choices=["png", "jpg"], default="png",
                        help="Image format (default: png)")
    p_p2i.add_argument("--pages", default="", help="Page spec, e.g. '1-3,5' (default: all)")

    args = parser.parse_args()

    dispatch = {
        "merge":      cmd_merge,
        "split":      cmd_split,
        "rotate":     cmd_rotate,
        "watermark":  cmd_watermark,
        "encrypt":    cmd_encrypt,
        "decrypt":    cmd_decrypt,
        "images2pdf": cmd_images2pdf,
        "pdf2images": cmd_pdf2images,
    }

    try:
        result = dispatch[args.command](**vars(args))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e),
                          "trace": traceback.format_exc()}), file=sys.stderr)
        sys.exit(3)

    if result["status"] == "error":
        print(json.dumps(result), file=sys.stderr)
        sys.exit(3)

    # print(json.dumps(result, indent=2, ensure_ascii=False))

    # Human-readable summary
    cmd = args.command
    print(f"\n── {cmd.title()} complete ──────────────────────────────────",
          file=sys.stderr)
    if "out" in result:
        print(f"  Output : {result['out']}", file=sys.stderr)
    if "total_pages" in result:
        print(f"  Pages  : {result['total_pages']}", file=sys.stderr)
    if "size_kb" in result:
        print(f"  Size   : {result['size_kb']} KB", file=sys.stderr)

    if cmd == "merge":
        print(f"  Inputs : {result.get('input_count', '?')} files", file=sys.stderr)
    elif cmd == "split" and "chunks" in result:
        print(f"  Chunks : {result['chunks']}", file=sys.stderr)
        for c in result["outputs"]:
            print(f"    • {c['path']} ({c['pages']} pages)", file=sys.stderr)
    elif cmd == "split":
        print(f"  Extracted : pages {result.get('extracted_pages', [])}", file=sys.stderr)
    elif cmd == "rotate":
        print(f"  Rotated : pages {result.get('rotated_pages', [])} by {result.get('angle')}°",
              file=sys.stderr)
    elif cmd == "watermark":
        print(f"  Type   : {result.get('watermark_type', '?')}", file=sys.stderr)
        print(f"  Marked : pages {result.get('watermarked_pages', [])}", file=sys.stderr)
    elif cmd == "encrypt":
        print(f"  Encrypted : yes", file=sys.stderr)
    elif cmd == "decrypt":
        print(f"  Decrypted : yes", file=sys.stderr)
    elif cmd == "images2pdf":
        print(f"  Images : {result.get('image_count', '?')} files", file=sys.stderr)
    elif cmd == "pdf2images":
        m = result.get("mode", "pages")
        print(f"  Mode   : {m}", file=sys.stderr)
        print(f"  Rendered : {result.get('rendered_pages', '?')} pages", file=sys.stderr)
        if m == "long":
            print(f"  Size   : {result.get('width')}x{result.get('height')} px", file=sys.stderr)
        elif "outputs" in result:
            for o in result["outputs"]:
                print(f"    • {o['path']} ({o['width']}x{o['height']})", file=sys.stderr)

    print(f"────────────────────────────────────────────────────────\n", file=sys.stderr)


if __name__ == "__main__":
    main()
