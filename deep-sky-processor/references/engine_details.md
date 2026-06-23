# 星点处理引擎、FITS I/O 与质量指标细节

## 星点处理引擎 v2

`scripts/star_tools.py` 已升级为多尺度检测引擎：

### 多尺度检测
- 自动从图像估计 FWHM（2D 高斯拟合亮星候选，MAD 剔除异常值）
- 基于 FWHM 生成 4 个尺度的结构元素：小星(0.4x)_、中星(0.7x)_、大星(1.1x)_、星芒(1.8x)
- 各尺度独立 White Top-hat 检测，合并响应

### 连通域特征过滤
对每个检测到的亮结构计算：
- 面积、等效直径、圆度(4πA/P²)
- 峰值亮度、长宽比、包围盒填充率

自动过滤：
- **热像素**：面积 < π(0.3·FWHM)²
- **星云亮核**：面积 > π(4·FWHM)² 且圆度 < 0.25
- **噪声**：峰值 < 0.3·阈值
- **星云细丝**：长宽比 > 5 且圆度 < 0.3

### 梯度感知阈值
- 计算 Sobel 梯度幅值
- 在星云高梯度区域（亮丝、边缘）自动降低检测响应，避免误检

### 修复方法
- **默认**：OpenCV Telea 快速行进法（小/中星点）
- **大区域/星芒**：自动降级为 Navier-Stokes 流体动力学修复
- **OpenCV 不可用时**：回退到高斯模糊修复

### 置信度系统
`detect_stars_multiscale()` 返回 `confidence`（0-1）：
- 基于保留比例、圆度一致性、尺寸一致性、密度合理性、热像素比例综合评分
- `separate_stars()` 默认 `min_confidence=0.3`
- 置信度 < 0.3 时输出警告，建议改用外部 StarNet++

### CLI 使用
```bash
# 检测并输出掩膜 + 详情
python scripts/star_tools.py detect input.jpg mask.tif --details

# 分离星点（使用 Telea 修复）
python scripts/star_tools.py separate input.jpg starless.tif --method telea

# 使用旧版单尺度检测
python scripts/star_tools.py separate input.jpg starless.tif --legacy
```

---

## FITS I/O 精度保护（v2）

`scripts/fits_io.py` 已修复两个严重的精度丢失问题：

### 输入：不再使用百分位裁剪
旧版使用 `p0.1-p99.9` 百分位裁剪归一化，导致：
- p0.1 以下的暗部信号被截断为 0
- p99.9 以上的亮部信号（亮星核心、星云饱和区）被压缩到 1
- 原始线性范围和测光信息丢失

**新版行为**：
- 应用 BSCALE/BZERO 标定后，使用绝对值最大值归一化：`data = data / max(|min|, |max|)`
- 保留负值（噪声可以有负值）
- 完整动态范围进入管线，不做任何截断
- 归一化因子 `data_scale` 记录在 meta 中，供输出时恢复

### 输出：float32 替代 uint16
旧版将 `[0,1]` float 乘以 65535 转 uint16，导致：
- 32-bit 浮点精度丢失（仅 16-bit 整数精度）
- 拉伸后的非线性数据被硬编码为 16-bit，语义不匹配
- FITS header 中的 BSCALE/BZERO 不再正确描述 data

**新版行为**：
- 输出 `BITPIX=-32`（IEEE float32）
- `BSCALE=1.0, BZERO=0.0`，header 语义一致
- 添加 `HISTORY` 记录处理信息
- 可选恢复原始 `data_scale`（处理前线性数据时有用）

### 对管线的意义
- 中间 `.tif` 文件继续使用 float32，不受影响
- 最终 FITS 输出现在保留完整的浮点精度，可作为高质量母版
- JPG/PNG 输出行为不变（仍然基于 [0,1] 范围）

---

## 质量审查标准

数值审查必须运行 `quality_metrics.py`。关键字段：

| 字段 | 用法 |
|---|---|
| `median` | JPG 背景/整体亮度参考，通常 0.03-0.15 |
| `corner_uniformity_ratio` | 角落均匀度；>3 通常说明 DBE/裁切/黑边有问题 |
| `uniform_5x5_dark_patch_ratio` | 暗部过度涂抹风险；过高说明塑料感 |
| `high_frequency_energy_ratio` | 高频变化参考；不能单独判断真实细节 |
| `star_area_ratio` | 星点占比；星云通常应低于星团 |

严肃处理还应按 `references/quality_assessment.md` 补充：
- 在线性母版上做背景、暗弱目标、中亮结构和亮核的分区 SNR。
- 用未饱和孤立恒星统计 FWHM、长短轴、圆度/偏心率及全场空间趋势。
- 有 WCS 和测光星表时评估恒星颜色残差；否则只能标注为启发式色彩审查。
- 结合 WCS 像素尺度、FWHM、口径和波长评估实际分辨率与采样，不以输出像素数或锐化后高频能量代替真实分辨率。

视觉审查优先级：
1. 背景：不能有明显光害带、黑坑、拼接缝。
2. 目标：结构应来自原图，不能像生成纹理。
3. 星点：大小自然，不能有黑洞、紫边、硬边。
4. 色彩：Hα 深红不品红，OIII 青蓝不电蓝，星系自然黄核蓝臂。
5. 动态范围：核心不过曝，外围不断层。

失败时必须重跑，不要硬宣布完成：
- DBE 失败：降低 degree、换 `median`、跳过 DBE 或只裁黑边。
- 拉伸过强：降低 `stretch_factor` 或提高 `stretch_gamma`。
- 噪声塑料感：降低降噪，输出保守版。
- 颜色失真：降低 `saturation`，发射星云使用 `emission` 模式。
- 风格过重：降低 `--style-strength`，或改用 `natural`/`deep_clean`。
- 星点处理伪影：检查 `04_starless_linear.tif`，若残留星多或星云误伤严重，使用 `--external-starless` 传入 StarNet++ 无星图。也可运行 `star_tools.py detect` 查看检测置信度。
- 去星置信度过低（<0.3）：检测输出会提示原因（热像素过多/过检/全部拒绝），此时应优先使用外部 StarNet++。
