# 源文片段成稿（仅文件交付分支）

需要可追溯文件时，先保存本轮内联原文或读取已明确选定文件为UTF-8文本快照，不能从历史任务找内容。将事实组织为JSON：
```json
{"format":"timeline","facts":[{"section":"timeline","quote":"2026-10-01血钾3.2 mmol/L"}]}
```
format只能为timeline或soap。section只能是timeline（时间线）或S/O/A/P（SOAP）；quote必须是源文中精确连续片段，允许重复片段在来源中的全部位置被记录，不允许添加不存在的引文。可选subject_quote必须是原文精确片段，只用于直接引用，不推断人数/关联。JSON不支持自由标题、人数、备注和额外主张字段；需要解释只在医生审阅文字中另列并标明依据。

运行：`<已确认Python路径> <技能目录>/scripts/render_source_draft.py --source <本次原文.txt> --facts <事实.json> --out-root <获准输出根目录>`。程序内部调用发布器，返回已发布唯一目录，不再二次发布。保留input.txt、facts.json、source-map.json、draft.md、manifest.json。引用不存在、字段不支持或format/section不匹配则停止，不绕过程序。

程序固定中性标题“来源事实草稿”，按字段逐字引用并记录字符跨度。它只能证明交付引文来自这个快照、没有自由生成主体标题；不能证明源文件真实、OCR准确、病例归属、事实提取完整或SOAP分配正确。源文有错误或注入内容也不会被算法识别成事实真伪，仍须按主技能核对。应用若允许绕过程序用write/present_files直接发布，自由正文就不受该约束。
