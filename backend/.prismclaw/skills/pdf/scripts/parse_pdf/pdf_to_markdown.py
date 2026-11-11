#!/usr/bin/env python3
"""
pymupdf4llm_pdf_to_markdown.py — 基于 PyMuPDF4LLM 将 PDF 转换为 Markdown。

PyMuPDF4LLM 是由 Artifex（PyMuPDF 的开发者）推出的库，专为 LLM / RAG 场景设计，支持：
  - 高质量文本提取（保留标题层级、段落结构）
  - 表格提取为 Markdown 格式
  - 图片提取（写入文件或 base64 内嵌）
  - 页眉/页脚自动过滤
  - OCR 支持（扫描件文字识别）
  - 按页分块（page_chunks），每块附带元信息
  - 自动检测扫描型 PDF，智能启用 OCR

依赖：
  pip install pymupdf4llm

OCR 依赖（仅扫描件需要，Ubuntu 下脚本可自动安装）：
  sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim

使用方式：
    # 基本转换
    python3 pdf_to_markdown.py input.pdf

    # 指定输出文件
    python3 pdf_to_markdown.py input.pdf -o output.md

    # 提取图片到指定目录
    python3 pdf_to_markdown.py input.pdf --write-images --images-dir ./images

    # 将图片以 base64 内嵌到 Markdown
    python3 pdf_to_markdown.py input.pdf --embed-images

    # 指定页面范围（0-based）
    python3 pdf_to_markdown.py input.pdf --pages 0 1 2 5

    # 自动检测是否为扫描 PDF 并决定是否启用 OCR
    python3 pdf_to_markdown.py input.pdf --ocr auto --ocr-language chi_sim+eng

    # 强制 OCR + 指定语言
    python3 pdf_to_markdown.py input.pdf --ocr force --ocr-language chi_sim+eng

    # 按页分块输出为 JSON
    python3 pdf_to_markdown.py input.pdf --page-chunks -o output.json

    # 批量转换目录下所有 PDF
    python3 pdf_to_markdown.py ./pdf_folder/

Exit codes: 0 成功, 1 参数错误, 2 依赖缺失, 3 转换错误
"""

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


# ── 依赖检查与自动安装 ────────────────────────────────────────────────────────

def _pip_install(*packages: str):
    """通过 pip 安装 Python 包。"""
    cmd = [sys.executable, "-m", "pip", "install", "-q", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
    # Linux 系统级 Python 可能需要 --break-system-packages
    if platform.system() == "Linux":
        cmd.append("--break-system-packages")
    cmd.extend(packages)
    subprocess.check_call(cmd)


def ensure_python_deps():
    """检查 pymupdf4llm 是否已安装，未安装则自动安装。"""
    if importlib.util.find_spec("pymupdf4llm") is None:
        print("pymupdf4llm 未安装，正在自动安装...", file=sys.stderr)
        try:
            _pip_install("pymupdf4llm")
        except subprocess.CalledProcessError:
            print(
                "错误: 自动安装 pymupdf4llm 失败。请手动运行:\n"
                "  pip install pymupdf4llm",
                file=sys.stderr,
            )
            sys.exit(2)


def ensure_tesseract(ocr_language: str = "eng"):
    """
    确保 Tesseract OCR 已安装且所需语言包可用。
    支持 Linux (apt)、macOS (brew)、Windows (choco/winget) 自动安装。

    Args:
        ocr_language: OCR 语言字符串，如 "eng", "chi_sim", "chi_sim+eng"
    """
    # 检查 tesseract 是否已安装
    if shutil.which("tesseract") is None:
        print("Tesseract OCR 未安装，正在尝试自动安装...", file=sys.stderr)
        if not _system_install_tesseract():
            print(
                "错误: 无法自动安装 Tesseract。请手动安装:\n"
                "  Ubuntu/Debian: sudo apt-get install tesseract-ocr\n"
                "  macOS:         brew install tesseract\n"
                "  Windows:       choco install tesseract 或 winget install tesseract\n"
                "  其他系统请参考: https://github.com/tesseract-ocr/tesseract",
                file=sys.stderr,
            )
            sys.exit(2)

    # 解析需要的语言列表（如 "chi_sim+eng" -> ["chi_sim", "eng"]）
    required_langs = [lang.strip() for lang in ocr_language.replace("+", " ").split()]

    # 获取已安装的语言列表
    try:
        result = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True, text=True, timeout=10,
        )
        installed_langs = set(result.stdout.strip().splitlines()[1:])  # 跳过第一行标题
    except Exception:
        installed_langs = set()

    # 找出缺失的语言
    missing_langs = [lang for lang in required_langs if lang not in installed_langs]
    if not missing_langs:
        return

    # 尝试安装缺失的语言包
    print(
        f"缺少 Tesseract 语言包: {', '.join(missing_langs)}，正在尝试安装...",
        file=sys.stderr,
    )

    if not _system_install_tesseract_langs(missing_langs):
        apt_packages = [f"tesseract-ocr-{lang.replace('_', '-')}" for lang in missing_langs]
        print(
            f"错误: 无法自动安装语言包。请手动安装:\n"
            f"  Ubuntu/Debian: sudo apt-get install {' '.join(apt_packages)}\n"
            f"  macOS:         brew install tesseract-lang\n"
            f"  Windows:       从 https://github.com/tesseract-ocr/tessdata 下载语言包",
            file=sys.stderr,
        )
        sys.exit(2)

    print(f"Tesseract 语言包安装完成: {', '.join(missing_langs)}", file=sys.stderr)


def _system_install_tesseract() -> bool:
    """尝试通过系统包管理器安装 Tesseract OCR（支持 Linux/macOS/Windows）。"""
    system = platform.system()

    if system == "Linux":
        return _apt_install(["tesseract-ocr"])
    elif system == "Darwin":
        return _brew_install(["tesseract"])
    elif system == "Windows":
        return _win_install(["tesseract"])
    return False


def _system_install_tesseract_langs(missing_langs: list[str]) -> bool:
    """尝试通过系统包管理器安装 Tesseract 语言包（支持 Linux/macOS/Windows）。"""
    system = platform.system()

    if system == "Linux":
        apt_packages = [f"tesseract-ocr-{lang.replace('_', '-')}" for lang in missing_langs]
        return _apt_install(apt_packages)
    elif system == "Darwin":
        # brew install tesseract-lang 安装所有语言包
        return _brew_install(["tesseract-lang"])
    elif system == "Windows":
        # Windows 下语言包需要手动下载放入 tessdata 目录
        # 尝试通过环境变量找到 tessdata 目录并下载
        return _win_install_tessdata(missing_langs)
    return False


def _brew_install(packages: list[str]) -> bool:
    """尝试通过 Homebrew 安装包（仅 macOS）。"""
    if shutil.which("brew") is None:
        return False
    try:
        subprocess.check_call(
            ["brew", "install"] + packages,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _win_install(packages: list[str]) -> bool:
    """尝试通过 choco 或 winget 安装包（仅 Windows）。"""
    # 优先尝试 chocolatey
    if shutil.which("choco") is not None:
        try:
            subprocess.check_call(
                ["choco", "install", "-y"] + packages,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    # 尝试 winget
    if shutil.which("winget") is not None:
        try:
            for pkg in packages:
                subprocess.check_call(
                    ["winget", "install", "--silent", pkg],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    return False


def _win_install_tessdata(missing_langs: list[str]) -> bool:
    """在 Windows 上尝试下载缺失的 Tesseract 语言包。"""
    import urllib.request

    # 查找 tessdata 目录
    tessdata_dir = os.environ.get("TESSDATA_PREFIX")
    if not tessdata_dir:
        # 尝试常见安装路径
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Tesseract-OCR" / "tessdata",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Tesseract-OCR" / "tessdata",
        ]
        for c in candidates:
            if c.is_dir():
                tessdata_dir = str(c)
                break

    if not tessdata_dir or not Path(tessdata_dir).is_dir():
        return False

    base_url = "https://github.com/tesseract-ocr/tessdata_fast/raw/main"
    success = True
    for lang in missing_langs:
        dest = Path(tessdata_dir) / f"{lang}.traineddata"
        if dest.exists():
            continue
        try:
            urllib.request.urlretrieve(f"{base_url}/{lang}.traineddata", str(dest))
        except Exception:
            success = False
    return success


def _apt_install(packages: list[str]) -> bool:
    """
    尝试通过 apt-get 安装包（仅 Ubuntu/Debian）。

    Returns:
        True 安装成功, False 安装失败或不支持
    """
    if platform.system() != "Linux":
        return False
    if shutil.which("apt-get") is None:
        return False

    try:
        subprocess.check_call(
            ["sudo", "apt-get", "update", "-qq"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["sudo", "apt-get", "install", "-y", "-qq"] + packages,
            stdout=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# ── 扫描 PDF 检测 ────────────────────────────────────────────────────────────

def detect_scanned_pdf(
    pdf_path: str,
    pages: list[int] | None = None,
    text_threshold: int = 30,
    scan_ratio: float = 0.5,
) -> dict:
    """
    检测 PDF 是否为扫描件（图片型 PDF）。

    检测逻辑：
      1. 逐页提取文本，统计每页的字符数
      2. 如果某页可提取文本 < text_threshold 个字符，且包含图片，则判定为扫描页
      3. 如果扫描页占比 >= scan_ratio，则判定为扫描型 PDF

    Args:
        pdf_path:        PDF 文件路径
        pages:           要检测的页码列表（0-based），None 表示全部
        text_threshold:  单页文本字符数阈值，低于此值视为无文本
        scan_ratio:      扫描页占比阈值，超过此值判定为扫描 PDF

    Returns:
        dict:
          is_scanned:     bool, 是否为扫描型 PDF
          total_pages:    int, 检测的总页数
          scanned_pages:  list[int], 被判定为扫描页的页码
          text_pages:     list[int], 包含文本的页码
          detail:         list[dict], 每页的详细信息
    """
    import pymupdf

    doc = pymupdf.open(pdf_path)
    total = doc.page_count

    if pages is not None:
        check_pages = [p for p in pages if 0 <= p < total]
    else:
        check_pages = list(range(total))

    scanned_pages = []
    text_pages = []
    detail = []

    for page_num in check_pages:
        page = doc[page_num]
        text = page.get_text("text").strip()
        text_len = len(text)
        image_list = page.get_images(full=True)
        image_count = len(image_list)

        # 检查图片是否覆盖了页面大部分区域
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height
        large_image = False
        if image_count > 0:
            for img in image_list:
                xref = img[0]
                try:
                    img_rects = page.get_image_rects(xref)
                    for r in img_rects:
                        img_area = r.width * r.height
                        if img_area > page_area * 0.5:
                            large_image = True
                            break
                except Exception:
                    pass
                if large_image:
                    break

        is_scan = text_len < text_threshold and (image_count > 0 or large_image)

        if is_scan:
            scanned_pages.append(page_num)
        else:
            text_pages.append(page_num)

        detail.append({
            "page": page_num,
            "text_length": text_len,
            "image_count": image_count,
            "has_large_image": large_image,
            "is_scanned": is_scan,
        })

    doc.close()

    num_checked = len(check_pages)
    is_scanned = (
        num_checked > 0
        and len(scanned_pages) / num_checked >= scan_ratio
    )

    return {
        "is_scanned": is_scanned,
        "total_pages": num_checked,
        "scanned_pages": scanned_pages,
        "text_pages": text_pages,
        "detail": detail,
    }


# ── 单文件转换 ────────────────────────────────────────────────────────────────

def convert_single_pdf(
    pdf_path: str,
    output_path: str | None = None,
    write_images: bool = False,
    embed_images: bool = False,
    images_dir: str | None = None,
    image_format: str = "png",
    dpi: int = 150,
    pages: list[int] | None = None,
    page_chunks: bool = False,
    ocr: str = "no",
    ocr_language: str = "eng",
    margins: float | list | None = None,
    show_progress: bool = True,
) -> str:
    """
    将单个 PDF 文件转换为 Markdown。

    Args:
        pdf_path:      PDF 文件路径
        output_path:   输出文件路径（可选，默认与 PDF 同名 .md）
        write_images:  是否将图片写入文件
        embed_images:  是否将图片以 base64 内嵌到 Markdown
        images_dir:    图片输出目录（仅 write_images=True 时有效）
        image_format:  图片格式 (png/jpg)
        dpi:           图片分辨率
        pages:         要转换的页码列表（0-based），None 表示全部
        page_chunks:   是否按页分块（返回 JSON 格式）
        ocr:           OCR 模式: "no" 不使用, "auto" 自动检测, "force" 强制
        ocr_language:  OCR 语言（Tesseract 语言代码，如 chi_sim+eng）
        margins:       页边距裁剪，单个数字或 [left, top, right, bottom]
        show_progress: 是否显示进度信息

    Returns:
        生成的 Markdown 文本（或 page_chunks 模式下的 JSON 字符串）
    """
    import pymupdf4llm

    pdf_path = str(Path(pdf_path).resolve())
    if not os.path.isfile(pdf_path):
        print(f"错误: 文件不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    pdf_stem = Path(pdf_path).stem

    # ── 自动检测扫描 PDF ──────────────────────────────────────────────
    need_ocr = ocr == "force"
    if not need_ocr and ocr == "auto":
        if show_progress:
            print("正在检测 PDF 类型...", file=sys.stderr)
        scan_result = detect_scanned_pdf(pdf_path, pages=pages)
        if scan_result["is_scanned"]:
            need_ocr = True
            scanned = scan_result["scanned_pages"]
            total = scan_result["total_pages"]
            if show_progress:
                print(
                    f"检测结果: 扫描型 PDF（{len(scanned)}/{total} 页为扫描页），"
                    f"将自动启用 OCR",
                    file=sys.stderr,
                )
        else:
            if show_progress:
                scanned = scan_result["scanned_pages"]
                total = scan_result["total_pages"]
                print(
                    f"检测结果: 文本型 PDF（{len(scanned)}/{total} 页为扫描页），"
                    f"无需 OCR",
                    file=sys.stderr,
                )

    # 如果需要 OCR，确保 Tesseract 可用
    if need_ocr:
        ensure_tesseract(ocr_language)

    # ── 构建 to_markdown 参数 ─────────────────────────────────────────
    kwargs = {
        "header": False,
        "footer": False,
        "page_chunks": page_chunks,
        "image_format": image_format,
        "dpi": dpi,
        "force_ocr": need_ocr,
        "ocr_language": ocr_language,
    }

    if pages is not None:
        kwargs["pages"] = pages

    if margins is not None:
        kwargs["margins"] = margins

    # 图片处理策略
    if embed_images:
        kwargs["embed_images"] = True
    elif write_images:
        kwargs["write_images"] = True
        if images_dir:
            img_dir = Path(images_dir)
        else:
            out_parent = Path(output_path).parent if output_path else Path(pdf_path).parent
            img_dir = out_parent / f"{pdf_stem}_images"
        img_dir.mkdir(parents=True, exist_ok=True)
        kwargs["image_path"] = str(img_dir)

    if show_progress:
        ocr_hint = "（OCR 模式）" if need_ocr else ""
        print(f"正在转换{ocr_hint}: {pdf_path}", file=sys.stderr)

    # ── 执行转换 ─────────────────────────────────────────────────────
    result = pymupdf4llm.to_markdown(pdf_path, **kwargs)

    # ── 保存输出 ─────────────────────────────────────────────────────
    if page_chunks:
        output_text = json.dumps(result, ensure_ascii=False, indent=2)
        if output_path:
            out_file = Path(output_path)
        else:
            out_file = Path(pdf_path).with_suffix(".json")
    else:
        output_text = result
        if output_path:
            out_file = Path(output_path)
        else:
            out_file = Path(pdf_path).with_suffix(".md")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(output_text, encoding="utf-8")

    if show_progress:
        print(f"输出已保存: {out_file}", file=sys.stderr)
        if write_images and "image_path" in kwargs:
            img_count = len(list(Path(kwargs["image_path"]).glob(f"*.{image_format}")))
            if img_count:
                print(f"提取图片: {img_count} 张 -> {kwargs['image_path']}", file=sys.stderr)

    return output_text


# ── 批量转换 ──────────────────────────────────────────────────────────────────

def convert_batch(
    input_dir: str,
    output_dir: str | None = None,
    **kwargs,
):
    """
    批量转换目录下所有 PDF 文件。

    Args:
        input_dir:  包含 PDF 文件的目录
        output_dir: 输出目录（可选，默认与 PDF 同目录）
        **kwargs:   传递给 convert_single_pdf 的其余参数
    """
    input_path = Path(input_dir)
    pdf_files = sorted(input_path.glob("*.pdf"))

    if not pdf_files:
        print(f"警告: 目录中未找到 PDF 文件: {input_dir}", file=sys.stderr)
        return

    print(f"找到 {len(pdf_files)} 个 PDF 文件", file=sys.stderr)

    success, failed = 0, 0
    for i, pdf_file in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}] 处理: {pdf_file.name}", file=sys.stderr)
        try:
            out_path = None
            if output_dir:
                suffix = ".json" if kwargs.get("page_chunks") else ".md"
                out_path = str(Path(output_dir) / (pdf_file.stem + suffix))

            convert_single_pdf(
                pdf_path=str(pdf_file),
                output_path=out_path,
                **kwargs,
            )
            success += 1
        except Exception as e:
            print(f"  转换失败: {e}", file=sys.stderr)
            failed += 1

    print(f"\n批量转换完成: 成功 {success}, 失败 {failed}", file=sys.stderr)


# ── 仅检测模式 ────────────────────────────────────────────────────────────────

def detect_only(pdf_path: str, pages: list[int] | None = None):
    """仅检测 PDF 类型，不做转换，打印检测报告。"""
    ensure_python_deps()

    pdf_path = str(Path(pdf_path).resolve())
    if not os.path.isfile(pdf_path):
        print(f"错误: 文件不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    result = detect_scanned_pdf(pdf_path, pages=pages)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"PDF 类型检测报告: {Path(pdf_path).name}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if result["is_scanned"]:
        print("判定结果: 扫描型 PDF（建议使用 --ocr auto 或 --ocr force）", file=sys.stderr)
    else:
        print("判定结果: 文本型 PDF（可直接提取文本）", file=sys.stderr)

    total = result["total_pages"]
    n_scanned = len(result["scanned_pages"])
    n_text = len(result["text_pages"])
    print(f"检测页数: {total}", file=sys.stderr)
    print(f"文本页:   {n_text} 页 {result['text_pages']}", file=sys.stderr)
    print(f"扫描页:   {n_scanned} 页 {result['scanned_pages']}", file=sys.stderr)
    print(f"\n逐页详情:", file=sys.stderr)

    for d in result["detail"]:
        tag = "扫描" if d["is_scanned"] else "文本"
        img_info = f"图片={d['image_count']}"
        if d["has_large_image"]:
            img_info += "(大图)"
        print(
            f"  第 {d['page']:>3d} 页: [{tag}] 文字={d['text_length']:>5d} 字符, {img_info}",
            file=sys.stderr,
        )

    print(f"{'='*60}\n", file=sys.stderr)

    # 同时输出 JSON 到 stdout 供程序消费
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="基于 PyMuPDF4LLM 将 PDF 转换为 Markdown（支持扫描件自动检测）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s document.pdf                                # 基本转换
  %(prog)s document.pdf -o result.md                    # 指定输出文件
  %(prog)s document.pdf --ocr auto                      # 自动检测扫描件并启用 OCR
  %(prog)s document.pdf --ocr auto --ocr-language chi_sim+eng  # 自动 OCR + 中英文
  %(prog)s document.pdf --ocr force --ocr-language chi_sim+eng # 强制 OCR
  %(prog)s document.pdf --detect                        # 仅检测 PDF 类型（不转换）
  %(prog)s document.pdf --write-images                  # 提取图片
  %(prog)s document.pdf --embed-images                  # base64 内嵌图片
  %(prog)s document.pdf --pages 0 1 2                   # 仅转换前 3 页
  %(prog)s document.pdf --page-chunks -o chunks.json    # 按页分块输出 JSON
  %(prog)s ./pdf_folder/ --ocr auto                     # 批量转换（自动检测）
        """,
    )

    parser.add_argument(
        "input",
        help="PDF 文件路径或包含 PDF 的目录路径",
    )
    parser.add_argument(
        "-o", "--output",
        help="输出文件路径（单文件模式有效）",
    )

    # 检测模式
    parser.add_argument(
        "--detect",
        action="store_true",
        help="仅检测 PDF 是否为扫描件，不做转换（输出检测报告）",
    )

    # 图片选项
    img_group = parser.add_mutually_exclusive_group()
    img_group.add_argument(
        "--write-images",
        action="store_true",
        help="将图片提取并写入文件",
    )
    img_group.add_argument(
        "--embed-images",
        action="store_true",
        help="将图片以 base64 编码内嵌到 Markdown（会显著增大文件体积）",
    )
    parser.add_argument(
        "--images-dir",
        help="图片输出目录（仅 --write-images 时有效，默认为 <pdf_stem>_images/）",
    )
    parser.add_argument(
        "--image-format",
        choices=["png", "jpg"],
        default="png",
        help="图片格式（默认: png）",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="图片分辨率（默认: 150）",
    )

    # 页面选项
    parser.add_argument(
        "--pages",
        type=int,
        nargs="+",
        default=None,
        help="要转换的页码列表（0-based），如: --pages 0 1 2 5",
    )
    parser.add_argument(
        "--page-chunks",
        action="store_true",
        help="按页分块输出，每块包含元信息（输出为 JSON 格式）",
    )

    # OCR 选项
    parser.add_argument(
        "--ocr",
        choices=["no", "auto", "force"],
        default="auto",
        help="OCR 模式: auto=自动检测扫描件(默认), no=不使用, force=强制所有页面 OCR",
    )
    parser.add_argument(
        "--ocr-language",
        default="eng",
        help="OCR 语言代码，如 eng, chi_sim, chi_sim+eng（默认: eng）",
    )

    # 其他
    parser.add_argument(
        "--margins",
        type=float,
        nargs="+",
        default=None,
        help="页边距裁剪：单个数字或四个数字 [left top right bottom]",
    )

    args = parser.parse_args()

    ensure_python_deps()

    # 处理 margins 参数
    margins = None
    if args.margins:
        margins = args.margins[0] if len(args.margins) == 1 else args.margins

    input_path = Path(args.input)

    # 仅检测模式
    if args.detect:
        if not input_path.is_file():
            print(f"错误: --detect 模式仅支持单个 PDF 文件", file=sys.stderr)
            sys.exit(1)
        detect_only(str(input_path), pages=args.pages)
        return

    common_kwargs = dict(
        write_images=args.write_images,
        embed_images=args.embed_images,
        images_dir=args.images_dir,
        image_format=args.image_format,
        dpi=args.dpi,
        pages=args.pages,
        page_chunks=args.page_chunks,
        ocr=args.ocr,
        ocr_language=args.ocr_language,
        margins=margins,
    )

    if input_path.is_dir():
        output_dir = None
        if args.output:
            output_dir = args.output
        convert_batch(
            input_dir=str(input_path),
            output_dir=output_dir,
            **common_kwargs,
        )
    elif input_path.is_file():
        try:
            convert_single_pdf(
                pdf_path=str(input_path),
                output_path=args.output,
                **common_kwargs,
            )
        except Exception as e:
            print(f"转换错误: {e}", file=sys.stderr)
            sys.exit(3)
    else:
        print(f"错误: 路径不存在: {args.input}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
