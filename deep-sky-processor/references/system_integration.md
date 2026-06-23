# 系统集成、格式与处理风格细节

## 支持格式

| 格式 | 输入 | 输出 | 说明 |
|---|---|---|---|
| FITS `.fit/.fits/.fts` | 是 | 是 | 线性叠加图；脚本会归一化显示处理 |
| XISF `.xisf` | 是 | 否 | PixInsight 格式；需安装 `xisf` |
| TIFF `.tif/.tiff` | 是 | 是 | 推荐作为母版 |
| PNG/JPG | 是 | 是 | 已非线性/压缩，处理要保守 |

## 何时读取 references

- 阶段动作、候选比较和 Critic 返回协议：`references/agent_protocol.md`
- FITS/XISF 拍摄元数据、物理先验和天区解析：`references/physical_metadata.md`
- 多尺度亮度/色相/星点蒙版与局部算子：`references/mask_workflow.md`
- 目标类型策略：`references/target_awareness.md`
- 坏点修复、局部归一化、梯度诊断与宽场 DBE：`references/linear_stage_processing.md`
- 窄带合成：`references/narrowband_synthesis.md`
- LRGB 合成：`references/lrgb_synthesis.md`
- 多曝光 HDR 合成：`references/hdr_composition.md`
- 彗星、行星、月面、宽场星野和超新星残骸：`references/special_targets.md`
- 分区 SNR、星点圆度、星表色差与分辨率评估：`references/quality_assessment.md`
- Siril、PixInsight、StarNet++、外部降噪等集成：`references/external_tools.md`
- AI 工具边界：`references/ai_hybrid_workflow.md`
- 常见误判：`references/ai_common_pitfalls.md`
- 真实案例：`references/case_ngc6888_rgb.md`

## 环境

运行时不得创建虚拟环境或执行 `pip install`。Starun 会通过工作区中的
`python`/`python3` 命令提供已经安装好 `requirements.txt` 依赖的只读 Python Runtime。
不得设置或覆盖 `STARUN_SKILL_PYTHON`、`PATH`、`VIRTUAL_ENV`、`PYTHONHOME` 或
`PYTHONPATH`。若缺少依赖，应直接报告环境配置错误，由 API 镜像或专用 Skill Runtime
的构建流程修复。

## Starun 网站处理风格

读取 `input/request.json` 中的 `style`：

- `realistic`：直接分析并处理图片，采用保守、非生成式流程；不得生成风格提示词，
  不得调用生图模型。
- `balanced`：先结合诊断结果生成 `output/style-prompt.json`，再以该提示词指导
  Skill 的非生成式处理；不得调用生图模型。
- `artistic`：不由本 Skill 执行。后端 Agent 使用 Kimi 多模态生成美化建议，再调用
  腾讯混元图生图。
