# 同次发布最终重绘

已有单文件绘图程序需要调整版式时，可在原命令的 `--plot` 后增加 `--renderer /绝对路径/plot.py`。程序须接受 `--run-dir`（读取本次 `result.json`、`input-tidy.csv`）和 `--out-dir`（只向该目录输出一张SVG、一张300dpi RGB TIFF），使用既有环境，不另做统计或修改输入、报告及计算结果。未指定时保持原流程。

入口先执行重绘，再将最终图发布为 `figure.svg`、`figure.tiff`，把绘图源码、最终图和复现入口一起放进同次 `reproduction.zip`；解压后的 `reproduce.py` 会再次执行重绘。不要发布后另改图，却继续交付旧包。原始图和计算结果另存为 `analysis-figure.*`、`analysis-result.json`。

`render-variant.json`只证明执行、文件身份与格式检查。原 `rendered-points.json`对应原始图，不证明重绘后的标签、几何或医学含义正确；最终图仍须实际读回核对，figure-preview.png由最终figure.tiff生成。重绘失败、超时或改动受保护文件时停止发布，不回退交付旧图。此入口尚待真实App自然采用及跨题收益验证。
