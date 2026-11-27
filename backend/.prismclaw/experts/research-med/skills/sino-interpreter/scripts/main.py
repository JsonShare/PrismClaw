"""result-interpreter 主入口

用法:
    python main.py --session_id <session_id> [--interpret_only true] [--result_type <type>]

参数:
    --session_id: 会话ID，用于读取数据桩
    --interpret_only: 是否仅解释（不生成论文风格结论）
    --result_type: 结果类型 (differential|regression|survival)

输出:
    <session-root>/session-{session_id}/20_interpretation.md
"""

import argparse
import json
import os
import sys
from pathlib import Path
import os
_SESSION_ROOT = Path(os.environ.get("PRISCLAW_SESSION_ROOT", str(Path.cwd())))

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from interpreter_factory import InterpreterFactory
from template_engine import TemplateEngine
from conclusion_writer import ConclusionWriter


def parse_args():
    parser = argparse.ArgumentParser(description="result-interpreter: Plain Language结果解释器")
    parser.add_argument("--session_id", type=str, required=True, help="会话ID")
    parser.add_argument("--interpret_only", type=str, default="false", help="仅生成Plain Language解释")
    parser.add_argument("--result_type", type=str, default="auto", choices=["auto", "differential", "regression", "survival"], help="结果类型")
    parser.add_argument("--output_path", type=str, default=None, help="自定义输出路径")
    return parser.parse_args()


def find_result_file(session_id: str) -> tuple:
    """查找分析结果文件"""
    context_path = _SESSION_ROOT / f"session-{session_id}"

    # 按优先级查找结果文件
    result_files = [
        ("differential", context_path / "10_differential_results.json"),
        ("regression", context_path / "11_regression_results.json"),
        ("survival", context_path / "12_survival_results.json"),
    ]

    for result_type, file_path in result_files:
        if file_path.exists():
            return result_type, file_path

    return None, None


def load_results(result_path: Path) -> dict:
    """加载分析结果"""
    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_interpretation(output_path: Path, content: str):
    """保存解释结果"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[result-interpreter] 解释已保存至: {output_path}")


def main():
    args = parse_args()
    session_id = args.session_id

    print(f"[result-interpreter] 开始解释分析结果...")
    print(f"[result-interpreter] session_id: {session_id}")

    # 查找结果文件
    result_type, result_path = find_result_file(session_id)
    if result_type is None:
        print("[result-interpreter] 错误: 未找到分析结果文件")
        print("[result-interpreter] 请先执行differential-analysis、regression-analysis或survival-analysis")
        sys.exit(1)

    print(f"[result-interpreter] 找到{result_type}类型的结果文件: {result_path}")

    # 加载结果
    results = load_results(result_path)
    print(f"[result-interpreter] 已加载结果数据")

    # 确定输出路径
    if args.output_path:
        output_path = Path(args.output_path)
    else:
        output_path = _SESSION_ROOT / f"session-{session_id}/20_interpretation.md"

    # 创建解释器工厂
    factory = InterpreterFactory()
    interpreter = factory.create_interpreter(result_type)

    # 生成Plain Language解释
    interpret_only = args.interpret_only.lower() == "true"

    if interpret_only:
        # 仅生成Plain Language解释
        plain_text = interpreter.interpret(results)
        content = plain_text
        print("[result-interpreter] 已生成Plain Language解释")
    else:
        # 生成完整解释（含论文风格结论）
        plain_text = interpreter.interpret(results)

        conclusion_writer = ConclusionWriter()
        conclusion = conclusion_writer.write_conclusion(results, result_type)

        template_engine = TemplateEngine()
        content = template_engine.render(
            result_type=result_type,
            plain_text=plain_text,
            conclusion=conclusion,
            raw_results=results
        )
        print("[result-interpreter] 已生成完整解释（含论文风格结论）")

    # 保存结果
    save_interpretation(output_path, content)

    print(f"[result-interpreter] 解释完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())