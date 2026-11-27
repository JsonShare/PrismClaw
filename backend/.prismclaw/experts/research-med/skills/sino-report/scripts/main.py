"""
Report Export - 报告导出Skill主入口
将分析结果导出为符合期刊格式的研究报告
"""

import argparse
import json
import os
import sys
from pathlib import Path
import os
_SESSION_ROOT = Path(os.environ.get("PRISCLAW_SESSION_ROOT", str(Path.cwd())))

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from report_builder import ReportBuilder
from docx_exporter import DocxExporter
from pdf_exporter import PdfExporter
from citation_formatter import CitationFormatter


def load_session_data(session_id: str) -> dict:
    """从共享数据桩加载会话数据"""
    context_path = _SESSION_ROOT / f"session-{session_id}"

    data = {
        "session_id": session_id,
        "context_path": context_path,
    }

    # 加载分析结果
    results_files = [
        "10_differential_results.json",
        "11_regression_results.json",
        "12_survival_results.json",
        "20_interpretation.md",
        "04_quality_report.md",
    ]

    for filename in results_files:
        filepath = context_path / filename
        if filepath.exists():
            if filename.endswith('.json'):
                with open(filepath, 'r', encoding='utf-8') as f:
                    key = filename.replace(".json", "").replace(".md", "")
                    data[key] = json.load(f)
            else:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data[filename.replace(".md", "")] = f.read()

    # 加载数据字典
    dict_file = context_path / "02_data_dictionary.md"
    if dict_file.exists():
        with open(dict_file, 'r', encoding='utf-8') as f:
            data["data_dictionary"] = f.read()

    # 加载清洗日志
    log_file = context_path / "03_preparation_log.md"
    if log_file.exists():
        with open(log_file, 'r', encoding='utf-8') as f:
            data["preparation_log"] = f.read()

    # 检查图表目录
    charts_dir = context_path / "30_charts"
    if charts_dir.exists():
        data["charts_dir"] = str(charts_dir)
        data["charts"] = list(charts_dir.glob("*"))
        data["charts"] = [f for f in data["charts"] if f.is_file()]

    return data


def parse_arguments(args_str: str) -> dict:
    """解析$ARGUMENTS参数"""
    if not args_str:
        return {}

    import shlex
    try:
        parts = shlex.split(args_str)
    except:
        parts = args_str.split()

    parser = argparse.ArgumentParser(description='Report Export')

    parser.add_argument('--session_id', type=str, required=True)
    parser.add_argument('--report_type', type=str, default='full',
                       choices=['full', 'methods', 'results', 'summary'])
    parser.add_argument('--target_journal', type=str, default='Lancet')
    parser.add_argument('--include_methods', type=bool, default=True)
    parser.add_argument('--include_results', type=bool, default=True)
    parser.add_argument('--include_interpretation', type=bool, default=True)
    parser.add_argument('--include_charts', type=bool, default=True)
    parser.add_argument('--export_format', type=str, default='docx,pdf',
                       help='逗号分隔: docx,pdf,md')
    parser.add_argument('--citation_style', type=str, default='Vancouver',
                       choices=['Vancouver', 'APA', 'GB/T 7714'])
    parser.add_argument('--output_filename', type=str, default=None)
    parser.add_argument('--title', type=str, default=None)
    parser.add_argument('--authors', type=str, default=None)
    parser.add_argument('--affiliation', type=str, default=None)
    parser.add_argument('--corresponding_author', type=str, default=None)
    parser.add_argument('--abstract', type=str, default=None)

    parsed = parser.parse_args(parts)
    return vars(parsed)


def main():
    """主入口函数"""
    import sys

    args_str = sys.argv[1] if len(sys.argv) > 1 else ""
    if not args_str:
        print("错误: 必须提供 --session_id 参数")
        sys.exit(1)

    args = parse_arguments(args_str)
    session_id = args['session_id']

    print(f"[Report Export] 开始生成报告 - session: {session_id}")

    # 加载会话数据
    session_data = load_session_data(session_id)

    # 确定输出路径
    context_path = session_data['context_path']

    if args.get('output_filename'):
        base_name = args['output_filename']
    else:
        base_name = "40_report"

    output_dir = context_path

    # 创建报告构建器
    builder = ReportBuilder(
        target_journal=args['target_journal'],
        citation_style=args['citation_style'],
    )

    # 构建报告内容
    report_data = {
        "session_id": session_id,
        "title": args.get('title', f"数据分析报告 - {session_id}"),
        "authors": args.get('authors'),
        "affiliation": args.get('affiliation'),
        "corresponding_author": args.get('corresponding_author'),
        "abstract": args.get('abstract'),
        "report_type": args['report_type'],
        "include_methods": args['include_methods'],
        "include_results": args['include_results'],
        "include_interpretation": args['include_interpretation'],
        "include_charts": args['include_charts'],
        "session_data": session_data,
        "target_journal": args['target_journal'],
    }

    print(f"[Report Export] 报告类型: {args['report_type']}, 目标期刊: {args['target_journal']}")

    # 构建报告
    content = builder.build_report(report_data)

    # 导出文件
    exported_files = {}
    export_formats = args.get('export_format', 'docx,pdf').split(',')

    for fmt in export_formats:
        fmt = fmt.strip().lower()

        if fmt == 'md':
            # Markdown输出
            md_path = output_dir / f"{base_name}.md"
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(content['markdown'])
            exported_files['md'] = str(md_path)
            print(f"  导出成功: {md_path.name}")

        elif fmt == 'docx':
            # Word输出
            docx_exporter = DocxExporter()
            docx_path = output_dir / f"{base_name}.docx"
            docx_exporter.export(content, docx_path)
            exported_files['docx'] = str(docx_path)
            print(f"  导出成功: {docx_path.name}")

        elif fmt == 'pdf':
            # PDF输出
            pdf_exporter = PdfExporter()
            pdf_path = output_dir / f"{base_name}.pdf"
            pdf_exporter.export(content, pdf_path)
            exported_files['pdf'] = str(pdf_path)
            print(f"  导出成功: {pdf_path.name}")

    print(f"[Report Export] 报告生成完成，共 {len(exported_files)} 个文件")

    return {
        "status": "success",
        "session_id": session_id,
        "target_journal": args['target_journal'],
        "report_type": args['report_type'],
        "exported_files": exported_files,
    }


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2, ensure_ascii=False))