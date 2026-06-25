---
name: deep-sky-processor
description: |
  AI 主导的深空天文后期助手。用于把深空天文原始叠加后的 FITS、XISF、
  TIFF、PNG/JPG 图像，在真实性约束下处理成更美观的 JPG/TIFF 成片。
  AI 负责视觉判断、天体类型策略、参数选择、风格取舍和质量审查；本地脚本
  只作为诊断、可控图像变换和量化审查工具。适用于发射星云、反射星云、
  星系、星团、行星状星云、暗星云、宽场星野的真实风格美化、拉伸、
  去光害、校色、降噪、缩星、细节增强和最终 JPG 输出。
---

# Deep Sky Processor

## 定位

这是 **AI 主导、代码辅助、真实约束** 的深空后期 skill，不是 PixInsight/Siril 的替代品。

## 触发条件

当用户提到以下任意场景时自动调用本 Skill：

**关键词**：深空后期、天文后期、处理深空照片、处理 FITS、处理 XISF、
拉伸星云、去光害、缩星、降噪星云、星云美化、深空图像处理、
NGC6888 后期、M42 后期、IC 2177 后期、星系后期处理。

**文件特征**：用户提及 `.fits` / `.xisf` / `.fit` / `.fts` 文件和深空天体名称，
或直接要求对深空天体图像做美化、拉伸、降噪或校色处理。

**不适用场景**：普通风景照、人像、日常摄影后期。这些应使用通用图像处理工具。

AI 的职责：
- 看图或根据用户描述判断天体类型、问题和审美方向。
- 运行诊断脚本，先表达视觉意图，再选择最小必要步骤和参数。
- 每执行一个高风险阶段就查看前后图、差异图和质量门禁。
- 决定接受、重试、生成候选版本、回退或请求人工审查。
- 输出真实但更好看的 `jpg`，必要时同时保留 `tif` 母版。

脚本的职责：
- 读取 FITS/XISF/TIFF/PNG/JPG。
- 提供亮度、噪声、梯度、星点、锐度、色彩等诊断。
- 执行可控的非生成式变换：拉伸、启发式校色、背景均衡、降噪、缩星、局部对比、饱和度调整。
- 输出中间图和质量指标，供 AI 审查。

## Agent-in-the-loop 模式

默认优先使用阶段式闭环，不要只制定一次计划后运行完整管线。
有关会话初始化、单阶段动作提交（`agent_workflow.py` 调用）、前后对比图审查以及动作/评审协议 JSON 规格，请参阅参考文档 [agent_protocol.md](references/agent_protocol.md) 和 [physical_metadata.md](references/physical_metadata.md)。

- **高风险阶段**：对 `dbe`、`stretch`、`star_remove`、`star_reduce`、`star_process`、`enhance`、`style` 等关键步骤必须逐步审查。
- **低风险步骤**：可合并执行，但最终需进行真实性审查。
- **星点处理依赖**：管线会自动处理星点相关的前置与后置链路依赖。
- **局部控制**：对于 Hα/OIII/亮部/暗部等局部微调，使用蒙版工具，详细参考 [mask_workflow.md](references/mask_workflow.md)。

## 真实性红线

必须遵守：
- 不生成图中不存在的星云纹理、尘埃、星点或颜色。
- 不使用 AI 超分辨率、AI 着色、AI 纹理生成、参考图重绘。
- 可以使用外部 AI 降噪和去星工具，但只作为统计建模/分类工具，结果必须审查。
- 单张 RGB/LP 数据不能伪造 SHO/HOO 窄带色彩。
- “美化”只能来自拉伸、黑位、曲线、局部对比、饱和度、降噪和星点比例的调整。

允许的风格词：
- `natural`：真实自然，低饱和，保守细节。
- `enhanced`：星云更明显，适度提高局部对比和饱和。
- `high_contrast`：更强主体结构，背景更深，风险更高。
- `soft`：柔和、低锐化、低噪声，适合反射星云。
- `emission`：保护 Hα 深红和 OIII 青蓝，不做全图灰度世界白平衡。

内置非生成式专业定调 profile：
- `auto`：根据目标类型自动选择。
- `natural`：保守自然，适合作为真实基准版本。
- `deep_clean`：深黑背景、干净现代。
- `dramatic_nebula`：发射星云主体突出，色彩有冲击但受控。
- `soft_dust`：反射星云/暗尘埃的柔和胶片感。
- `galaxy_core`：星系黄核、蓝臂、尘埃带层次。
- `widefield_punch`：宽场星野的深背景和星云可见度。

这些 profile 只做色调曲线、背景压暗、背景去饱和、局部对比、轻微色彩分离和高光 rolloff，不生成新结构。

默认输出两版更稳妥：
- `*_natural.jpg`
- `*_enhanced.jpg`

## 必须优先做的三轮处理流程与参数闭环

深空后期处理遵循以下三轮标准流程：
1. **Round 1: 诊断和预览**：利用 `analyze.py` 进行数值诊断，利用 `recognize.py` 生成安全预览图。接着，使用 `--strength adaptive` 结合天体类型和名称让管线自动优化参数并运行首期处理。
2. **Round 2: 针对问题增强**：根据诊断报告对 DBE、降噪、拉伸、缩星、细节增强及定调风格进行微调。
3. **Round 3: 审查和定稿**：运行 `quality_metrics.py` 进行数值门禁审查。

详细的处理流程指导、CLI 命令行示例、`--override-params` 常用键及其初值推荐、以及参考图定调与 GHS 处理，请参阅参考文档 [workflow_steps.md](references/workflow_steps.md)。

## 引擎细节与数值质量指标

有关以下内容的详细设计、物理与数学原理，请参阅参考文档 [engine_details.md](references/engine_details.md)：
- **星点处理引擎 v2**：多尺度星点检测、连通域特征过滤、梯度感知阈值及 OpenCV Telea/Navier-Stokes/高斯修复。
- **FITS I/O 精度保护（v2）**：绝对值归一化、float32 输出防止精度丢失与分层色块。
- **质量审查标准**：`median`、`corner_uniformity_ratio` 等各项数值质量门禁与视觉 Critic 审查标准。

## 输出格式

默认输出：
- 最终 JPG：用户要看的成片。
- 如用户没有拒绝，保留 `--keep-all` 的工作目录，里面有中间 TIFF 和 `manifest.json`。
- 对严肃处理，额外输出 16-bit/float TIFF 母版。
- 自动化调用使用 `--result-json result.json` 获取统一状态、有效配置、质量门禁和警告。

状态含义：
- `success`：数值门禁未发现明确风险，仍需完成视觉审查。
- `partial_success`：主图已生成，但识别等可选阶段失败。
- `review_required`：产物保留，必须由视觉 Critic 检查或调整后重跑。
- `failed`：输入、配置或执行失败。

Starun 自动化调用必须把 Pipeline 的 `status`、`quality_gates` 和 `warnings`
原样写入最终 `processing-result.json` 的 `pipeline_status`、`quality_gates` 和
`warnings`。不得因为 JSON 字段或 artifact 校验通过而把 `review_required` 改写成
`success`。

长时间运行的 `analyze.py`、`recognize.py`、`pipeline.py` 和 `quality_metrics.py`
必须使用 PTY 执行。如果返回 session ID，持续使用空输入轮询，直到进程退出并获得明确
exit code；不得在进程仍运行时读取其输出文件或启动下一步。

处理完成后，按以下格式输出最终结果：

```markdown
## ✅ NGC6888 后期处理完成

| 项目 | 详情 |
|------|------|
| 输入 | FITS (SeeStar S50, 300s×45, Gain 120) |
| 目标类型 | 发射星云 (NGC6888, Crescent Nebula) |
| 风格 | enhanced / dramatic_nebula (style-strength: 1.0) |
| 预设 | adaptive (stretch_factor=76, saturation=1.52) |

**关键步骤**：analyze → adaptive pipeline (dbe + color + stretch +
final_color + star_reduce + local_enhance + style)

**输出文件**：
- 主图：`NGC6888_enhanced.jpg` (2048×1365, 982 KB)
- 自然基准：`NGC6888_natural.jpg` (2048×1365, 856 KB)

**质量审查**：
- median: 0.087 ✅
- corner_uniformity: 1.83 ✅
- star_area: 0.0024 ✅
- 视觉审查：背景均匀 ✅ | 星点自然 ✅ | Hα 深红保真 ✅ |
  核心无过曝 ✅ | 无伪影 ✅
- 状态：success
```

## 系统集成与运行环境

Starun API 通过 `scripts/run_starun_processing.py` 作为唯一自动化入口调用本
Skill。该入口负责读取 Starun sandbox 中的 `input/request.json`、
`input/inspection.json` 和原始图像，内部调用本 Skill 的诊断、预览和管线能力，
并写出符合 Starun Agents SDK 合约的 `output/processing-result.json`。

这个入口仅用于 Starun 网站的低 turn、可验证自动化调用；不改变本 Skill 作为独立
深空后期助手时的默认工作方式。独立使用时仍应优先采用上文的 Agent-in-the-loop
阶段式闭环、视觉审查和参数回退流程。

关于以下内容的详细信息，请参阅参考文档 [system_integration.md](references/system_integration.md)：
- **支持格式**：FITS、XISF、TIFF、PNG/JPG 图像的输入与输出说明。
- **何时读取 references**：其他 15 个具体业务与物理先验参考文档列表。
- **环境要求**：环境只读 Python Runtime、包依赖与路径配置规则。
- **Starun 网站处理风格**：`realistic`、`balanced`、`artistic` 处理模式细节。

## 已知问题

- **大图 OOM**：DBE（RBF 方法）在图像 >5MP（如 2877×2038）且内存 <16GB 时可能 OOM。
  缓解方案：使用 `--low-memory --tile-size 512` 并显式指定 `--steps` 跳过 DBE，或先降采样再处理。
