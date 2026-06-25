# 三轮处理流程与参数闭环细节

## 必须优先做的三轮流程

### Round 1: 诊断和预览

识别固定遵循：

```text
Header/WCS → 原始数值诊断 → 零裁切安全预览
→ AI视觉判断 → 本地CV辅助验证
```

1. 优先读取 FITS/XISF Header 和已有 WCS。可靠的目标标识不能被视觉分类静默覆盖。
2. 直接读取原文件运行数值诊断：

```bash
python scripts/analyze.py input.fits --output analysis-report.json --format json
```

3. 为线性 FITS/XISF 生成安全视觉审查包：

```bash
python scripts/recognize.py input.fits \
  --output recognition.json \
  --stage input \
  --workflow-dir recognition_workflow
```

安全预览固定 `shadow_pctl=0.0`，不会通过低端百分位裁切微弱正信号。
工作流输出全幅、主体、RGB 通道预览、浮点 TIFF、视觉审查请求和完整证据 JSON。
预览只用于观察，不能作为后续处理输入。

AI 必须实际查看预览后回填视觉判断。若模型没有视觉能力，状态保持
`awaiting_ai_visual_review`，不得把本地 CV 结果冒充 AI 视觉结论。

4. 本地 CV 只验证 AI 判断与星场、颜色、主体区域等启发式特征是否一致。
   冲突时优先级为 Header/WCS、高置信 AI 视觉判断、本地 CV。
5. 读取 `references/target_awareness.md` 中对应天体类型的策略。
6. **推荐方式**：使用 `--strength adaptive` + `--target-type` + `--target-name`，让管线自动基于诊断数据选择参数：

```bash
python scripts/pipeline.py input.fits preview.jpg \
  --strength adaptive \
  --target-type emission_nebula \
  --target-name NGC6888 \
  --color-mode emission \
  --steps color,stretch,final_color \
  --keep-all
```

若使用 Agent-in-the-loop 模式，本轮只运行首个必要步骤或候选预览，不要直接执行所有步骤。

`adaptive` 预设会自动运行 `analyze.py` 并基于诊断报告的 recommendations 生成配置，覆盖 medium 基底的对应参数。如果已有诊断报告，可显式传入 `--analysis-report report.json` 避免重复分析。

**adaptive 的行为**：以 `medium` 为安全基底，用诊断数据逐项覆盖：DBE 方法/阶数、降噪强度、拉伸因子、星点阈值、HDR、锐化、饱和度。同时自动应用天体类型安全规则（如球状星团禁用去星）。

如果不是发射星云，或需要保守基准，用 `--strength light` 或 `medium`。

### Round 2: 针对问题增强

只加入必要步骤。使用 `adaptive` 预设时，大部分参数已自动优化，AI 只需审查并微调：

| 问题 | 加入/调整 |
|---|---|
| 明显光害/渐晕 | 加 `dbe`，优先低强度；宽场 Hα 背景要谨慎 |
| 极暗但低噪 | 增强 `stretch_factor` 或使用 emission/deep stretch |
| 噪声明显 | 加 `pre_denoise` 或 `final_denoise`，强度保守 |
| 星点压目标 | 加 `star_reduce`；密集星场优先建议外部 StarNet++ |
| 星云太平 | 加 `local_enhance` 或 `enhance`，避免 HDR/CLAHE 光晕 |
| 偏品红/电蓝 | 降低 `saturation`，发射星云保护 Hα/OIII 比例 |
| 核心过曝 | 降低拉伸、提高 HDR、输出更保守版本 |
| 图片扁平、缺少个人风格 | 加 `style`，使用 `--style auto` 或指定 profile |

**优先使用 `--strength adaptive`**，仅在需要覆盖特定参数时使用 `--override-params`：

```bash
python scripts/pipeline.py input.fits output_enhanced.jpg \
  --strength adaptive \
  --target-type emission_nebula \
  --target-name NGC6888 \
  --color-mode emission \
  --style auto \
  --style-strength 1.0 \
  --steps color,stretch,final_color,local_enhance,star_reduce,style \
  --override-params '{"stretch_factor":72,"saturation":1.55,"star_reduction":0.25}' \
  --keep-all
```

AI 自动风格选择规则（`--style auto` 时）：
- 发射星云或 `color-mode emission` → `dramatic_nebula`
- 反射星云/暗星云 → `soft_dust`
- 星系 → `galaxy_core`
- 宽场 → `widefield_punch`
- 球状/疏散星团 → `star_cluster`（解析密集恒星，低饱和）
- 未知目标 → `deep_clean`

如果用户明确要求某种风格，使用 `--style <profile>` 覆盖 `auto`。
`--style-strength` 通常保持 `0.8-1.1`，超过 `1.2` 要特别检查颜色和光晕。

### 天体类型安全规则（已自动落实）

管线代码已根据 `target_type` 和 `target_name` 自动执行以下规则，AI 不需要手动禁用步骤：

| 目标类型 | 自动规则 |
|---|---|
| 球状星团 / 疏散星团 | 自动禁用 `star_remove`、`star_reduce`、`star_process`；降噪限制为保守级别 |
| M45 昴星团 | 同上（星点即主体，禁止去星/缩星） |
| M42 猎户座大星云 | 拉伸 ×0.75、HDR +0.15、target_bg ≥0.10，防止核心过曝 |
| 发射星云 | 拉伸 ×0.85、锐化 ×0.7、HDR 若过低则 +0.1 |
| 反射星云 | 拉伸 +20%、HDR -0.1、降噪 +15% |
| 星系 | HDR +0.15、锐化增强 |
| 行星状星云 | HDR +0.15、锐化增强 |
| 暗星云 | 拉伸 +15% |
| 发射星云 + 无梯度 | 自动跳过 DBE（诊断报告驱动） |

### Round 3: 审查和定稿

运行：

```bash
python scripts/quality_metrics.py output_enhanced.jpg
```

若有中间图，重点查看：
- `01_dbe.tif`：是否过减、黑坑、残留梯度。
- `05_stretched_starless.tif` 或拉伸后图：暗部是否浮现，核心是否死白。
- `08_color.tif`：颜色是否真实，背景是否中性。
- 最终 JPG：星点、噪声、结构、伪影。

质量门禁按阶段解释：
- 线性阶段允许存在轻微负背景，重点检查负值像素比例；不要用最终图的
  `median >= 0.015` 规则误判线性数据。
- 最终非线性图通过 `p1`、非正像素比例和背景中位数共同判断是否贴黑。
- 发射星云最终背景偏低只作为审查警告；是否保留真实 Hα/暗尘仍需看图。

密集星场处理：
- `dense/very_dense` 默认优先建议 StarNet++ 外部无星层，并降低星点重混合强度。
- 内置去星质量不达标时保持安全回退，不降低质量阈值强行接受。
- 发射星云去星回退后，自动改用 `masked_ghs` 保护星核和背景。
- 带星图局部增强与最终缩星复用线性星点蒙版，避免强化星核或误缩亮壳层。
- 发射星云颜色阶段保护青色 OIII 候选区域，避免 SCNR 与品红修正误伤。

极暗数据 `masked_ghs`：
- 先按稳健高光分位数归一化，再运行 GHS；默认 `shadow_pctl=0.0`，
  不从低端减黑位。
- `stretch_gamma` 会参与 masked GHS 的暗部预拉伸，推荐 `0.4-0.5`。
- 通用 `stretch_factor` 会映射到安全的 GHS `b=4-12`；若需直接控制，
  使用 `ghs_b`，不要把 `stretch_factor=100` 直接解释成 `b=100`。
- 可通过 `--override-params '{"dbe_method":"skip"}'` 显式跳过 DBE。

若视觉不可用，输出“需人工视觉审查”的清单，不要声称视觉通过。

## 参数闭环

### 参考图定调

用户提供参考成片时，只匹配全局亮度曲线、综合色调、饱和度和有限局部对比，
不得复制参考图结构、纹理、星点或颜色通道。先完成安全基础处理，再运行：

```bash
python scripts/reference_grade.py processed.jpg reference.png final.jpg \
  --strength 0.85 \
  --max-color-gain 1.25 \
  --max-saturation 1.45 \
  --local-contrast 0.10 \
  --match-orientation \
  --report final.reference-grade.json
```

参考图与输入不是同一视场或曝光深度时，只把参考图作为审美约束；不得为了
“看起来一样”制造输入数据中不存在的暗尘、Hα/OIII 结构或高频细节。

极暗线性数据不要直接量化为 16-bit 后交给 StarNet2，否则弱星云与背景会
产生分层和色块。先安全拉伸，再运行 StarNet2。对已拉伸 TIFF/PNG，应保留
StarNet 独立输出的 starless 和 stars 图层，再运行：

```bash
python scripts/enhance_starless.py \
  starless.tif stars.tif output_dir \
  --target-type emission_nebula \
  --target-name NGC6888
```

流程输出 LOW、MEDIUM、HIGH 三档 starless 和最终带星候选，并同步调整星点
主导程度。两个图层必须尺寸、方向和裁切一致；stars 必须是黑背景正向星点层，
不使用“带星图减 starless”的回退。球状星团、疏散星团、一般星团及 M45 会
被拒绝。HIGH 档不能绕过黑位、噪声、光晕、结构连续性和星点门禁。

### 推荐工作流：诊断 → adaptive → 审查 → 微调

```bash
# 1. 诊断（或让 adaptive 自动运行）
python scripts/analyze.py input.fits --output report.json --format json

# 2. 使用 adaptive 预设，传入诊断报告和目标信息
python scripts/pipeline.py input.fits output.jpg \
  --strength adaptive \
  --analysis-report report.json \
  --target-type emission_nebula \
  --target-name NGC6888 \
  --color-mode emission \
  --style auto \
  --keep-all
```

### 关键 CLI 参数

| 参数 | 作用 |
|---|---|
| `--strength adaptive` | **推荐**。以 medium 为基底，自动读取 analyze.py 诊断报告生成配置。未提供 `--analysis-report` 时会自动运行 analyze.py。 |
| `--analysis-report` | 显式传入 analyze.py 的 JSON 报告路径，避免重复分析。 |
| `--target-type` | 天体类型，触发自动安全规则（禁用危险步骤、参数修正）。 |
| `--target-name` | 天体名称（如 M42、M45），用于激活特定规则（M42 核心保护、M45 禁用去星）。FITS 文件会自动从 OBJECT header 读取。 |
| `--override-params` | JSON 对象，覆盖 adaptive 或预设的任何参数。仅在诊断结果不满意时使用。 |

### `--override-params` 常用键

```json
{
  "dbe_method": "polynomial",
  "dbe_degree": 2,
  "pre_denoise_lum": 0.012,
  "pre_denoise_chroma": 0.035,
  "stretch_factor": 54,
  "target_bg": 0.08,
  "star_threshold": 0.85,
  "star_reduction": 0.3,
  "star_stretch_factor": 12,
  "star_scnr_strength": 0.15,
  "star_combine_strength": 0.9,
  "hdr_strength": 0.35,
  "sharpen_amount": 0.7,
  "saturation": 1.4,
  "style_strength": 1.0,
  "final_denoise_lum": 0.008,
  "final_denoise_chroma": 0.024,
  "shadow_pctl": 0.5,
  "highlight_pctl": 99.9,
  "stretch_gamma": 0.4
}
```

经验初值（`adaptive` 已自动处理大部分）：
- 极暗线性 FITS/XISF：`stretch_factor` 80-140，`stretch_gamma` 0.33-0.45。
- 发射星云 RGB/LP：`color-mode emission`，`saturation` 1.3-1.8，避免全图白平衡。
- 星系：`saturation` 1.1-1.35，`hdr_strength` 0.35-0.6，锐化可略强。
- 反射星云：拉伸可强，饱和和锐化要柔和。
- 球状星团/M45：管线已自动禁用 `star_remove`/`star_reduce`，无需手动操作。
