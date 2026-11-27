"""
CSR报告主入口模块

负责CLI参数解析、流程编排和结果输出。

使用方式：
    python scripts/main.py --session_id abc123 --report_type csr --study_phase phase3 --lang zh --output_format md
"""

import argparse
import json
import os
import sys
from pathlib import Path
import os
_SESSION_ROOT = Path(os.environ.get("PRISCLAW_SESSION_ROOT", str(Path.cwd())))

# 添加scripts目录到Python路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from csr_structure import ICH_E3_STRUCTURE, CSRModule
from data_collector import DataCollector
from template_engine import TemplateEngine
from section_builders import SectionBuilder
from csr_formatter import CSRFormatter


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="CSR报告撰写工具 - 按照ICH E3规范生成临床研究报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python main.py --session_id abc123 --report_type csr --study_phase phase3 --lang zh
  python main.py --session_id abc123 --report_type interim --study_phase phase2 --lang en --output_format docx
  python main.py --session_id abc123 --report_type safety --study_phase phase3 --lang zh --output_format pdf
        """
    )
    parser.add_argument(
        "--session_id", type=str, required=True,
        help="会话ID（用于数据桩路径 <session-root>/session-{id}/）"
    )
    parser.add_argument(
        "--report_type", type=str, default="csr",
        choices=["csr", "interim", "safety"],
        help="报告类型：csr（最终报告）/ interim（期中报告）/ safety（安全性报告），默认csr"
    )
    parser.add_argument(
        "--study_phase", type=str, required=True,
        choices=["phase1", "phase2", "phase3", "phase4"],
        help="研究分期：phase1/phase2/phase3/phase4"
    )
    parser.add_argument(
        "--lang", type=str, default="zh",
        choices=["zh", "en"],
        help="语言：zh（中文NMPA）/ en（英文FDA），默认zh"
    )
    parser.add_argument(
        "--output_format", type=str, default="md",
        choices=["md", "docx", "pdf"],
        help="输出格式：md/docx/pdf，默认md"
    )
    parser.add_argument(
        "--protocol_id", type=str, default="",
        help="方案编号（手动补充）"
    )
    parser.add_argument(
        "--study_design", type=str, default="auto",
        help="研究设计类型（auto=自动检测 / rct / cohort / case_control / cross_sectional）"
    )
    parser.add_argument(
        "--context_path", type=str, default="",
        help="自定义数据桩路径（不指定则使用默认 <session-root>/session-{id}/）"
    )

    return parser.parse_args()


def get_context_path(session_id: str, custom_path: str = "") -> Path:
    """
    获取数据桩路径

    Parameters:
    -----------
    session_id : str
        会话ID
    custom_path : str
        自定义路径（优先级更高）

    Returns:
    --------
    Path: 数据桩根目录
    """
    if custom_path:
        return Path(custom_path)
    return _SESSION_ROOT / f"session-{session_id}"


def main():
    """主函数：编排CSR报告生成流程"""
    args = parse_args()

    print(f"[CSR Report Writer] 开始生成临床研究报告")
    print(f"  会话ID: {args.session_id}")
    print(f"  报告类型: {args.report_type}")
    print(f"  研究分期: {args.study_phase}")
    print(f"  语言: {args.lang}")
    print(f"  输出格式: {args.output_format}")

    # 第1步：确定数据桩路径
    context_path = get_context_path(args.session_id, args.context_path)
    print(f"\n[步骤1/5] 数据桩路径: {context_path}")

    if not context_path.exists():
        print(f"  警告：数据桩目录不存在，将创建空目录")
        context_path.mkdir(parents=True, exist_ok=True)

    # 第2步：收集所有上游分析结果
    print(f"\n[步骤2/5] 收集上游分析结果...")
    collector = DataCollector(context_path)
    available_data = collector.collect_all()
    print(f"  可用数据文件: {len(available_data)} 个")
    for key, path in available_data.items():
        status = "已找到" if path.exists() else "未找到（将使用占位符）"
        print(f"    - {key}: {status}")

    # 第3步：构建各章节内容
    print(f"\n[步骤3/5] 构建章节内容...")
    builder = SectionBuilder(
        context_path=context_path,
        lang=args.lang,
        study_phase=args.study_phase,
        report_type=args.report_type,
        study_design=args.study_design,
        protocol_id=args.protocol_id,
        available_data=available_data
    )
    sections = builder.build_all()
    print(f"  已构建 {len(sections)} 个章节")

    # 第4步：使用模板引擎填充
    print(f"\n[步骤4/5] 填充模板...")
    template_engine = TemplateEngine(lang=args.lang)
    report_md = template_engine.render(sections, report_type=args.report_type)
    print(f"  报告长度: {len(report_md)} 字符")

    # 第5步：格式化输出
    print(f"\n[步骤5/5] 格式化输出...")
    formatter = CSRFormatter(lang=args.lang)
    output_path = context_path / f"20_csr_report.{args.output_format}"

    if args.output_format == "md":
        formatter.export_markdown(report_md, output_path)
    elif args.output_format == "docx":
        formatter.export_docx(report_md, output_path)
    elif args.output_format == "pdf":
        formatter.export_pdf(report_md, output_path)

    print(f"\n[完成] CSR报告已生成: {output_path}")
    print(f"\n下一步建议：")
    print(f"  1. 检查报告中的占位符（标记为 {{...}} 的内容需要手动补充）")
    print(f"  2. 补充研究背景、研究者信息等需要手写的章节")
    print(f"  3. 确认安全性分析数据是否完整")
    print(f"  4. 如需导出Word格式：/report-export --session_id {args.session_id} --format docx --journal_style ICH-E3")

    return str(output_path)


if __name__ == "__main__":
    main()
