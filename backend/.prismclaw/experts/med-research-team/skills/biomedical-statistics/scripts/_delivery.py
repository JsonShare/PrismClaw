"""Describe artifacts from the same published statistical run."""

_DISPLAY_LABELS = {'figure.svg': '矢量图', 'figure.tiff': '300 dpi TIFF图',
                   'heatmap.svg': '矢量热图', 'heatmap.tiff': '300 dpi TIFF热图',
                   'report.md': '完整图注、结果说明及来源'}


def render_delivery(stage):
    """Describe only artifacts actually generated, without a second scientific summary."""
    lines = ['已生成以下成品：', '']
    for name, description in _DISPLAY_LABELS.items():
        if (stage / name).is_file():
            lines.append('- `' + name + '`：' + description + '。')
    lines += ['- `reproduction.zip`：同次输入、方案、脚本、依赖说明和结果的完整复现包。', '',
              '统计口径、来源定义和适用限制见同次 `report.md`。',
              '复现：解压后运行 `python reproduce.py --out-root ./reproduced-output`；环境依赖见包内 `requirements.txt`。']
    return '\n'.join(lines) + '\n'
