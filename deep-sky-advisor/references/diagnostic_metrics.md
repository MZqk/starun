# 定量诊断指标解读

在阅读 `scripts/analyze_file.py` 生成的 `*_analysis.json` 时使用本参考文档。

## 目录

- 证据模型
- 统计量
- 裁剪
- 噪点
- 背景与渐变
- 星点
- 色彩
- 分类

## 证据模型

每个诊断节均包含 `evidence` 字段：

- `measured`: 直接从提供的像素数据计算得出；
- `measured_on_robust_normalization`: 将亮度 P0.1–P99.9 映射到 0–1 后计算得出；
- `metadata_and_filename_heuristic`: 从头信息和文件名推断；
- `unavailable`: 有效样本不足；
- `not_applicable`: 该指标不适用于此通道模型。

不得将启发式推断或不可用的字段提升为测量事实。

## 统计量

`statistics` 保留原始数值范围，报告鲁棒百分位数、MAD sigma、NaN/Inf 占比以及精确/近似极值。

- 高 `exact_max_ratio` 可能表明裁剪、整数边界、蒙版或合成边框。
- 高 `exact_min_ratio` 可能表明裁剪的黑像素、叠加边缘、蒙版或有效的零值背景。
- 这些字段不识别物理原因。

## 裁剪

`clipping` 在鲁棒 P0.1–P99.9 归一化后测量。

- `highlight_ratio_ge_0_999` 标识审查映射中位于亮端的像素。
- `shadow_ratio_le_0_001` 标识暗端像素。
- 在未检查原始 ADU 边界、位深、校准历史和星点核心之前，不得将这些像素称为传感器饱和。
- 在未检查原始直方图和无效边框之前，不得将暗部像素称为黑位裁剪。

使用高亮预览进行视觉确认。

## 噪点

`noise.background_noise_sigma_normalized` 是从归一化亮度最暗的 35% 区域采样的 3×3 中值滤波残差的 MAD sigma。

其用途：

- 比较同一配准图像的不同版本；
- 识别异常噪点的背景；
- 判断线性降噪是否值得检查。

其并非：

- 物理信噪比；
- 以电子为单位的读出噪点；
- 暗弱低频结构是噪点的有效证据。

重采样、Drizzle、压缩、先前的降噪、欠采样以及真实的暗弱信号都可能改变估计值。请同时检查 `block_sigma_median`、`block_sigma_p90` 和采样计数。

## 背景与渐变

`background.plane` 对低信号采样像素拟合平面：

- `x_change_across_frame`；
- `y_change_across_frame`；
- `magnitude_across_frame`；
- `angle_degrees`；
- `r_squared`；
- `residual_rms`。

`region_medians_normalized` 和 `corner_mean_over_center` 提供独立的空间锚点。

解读：

- 高 magnitude 加高 R² 支持存在一致的低频趋势；
- 低 R² 意味着平面不能很好地解释背景；
- 低角心比可能与渐晕一致，但也可能与目标位置或真实天空结构有关；
- 不同 RGB 平面向量可能指示色差渐变或真实线发射信号。

切勿仅凭这些数值就推荐背景扣除。需检查背景预览图、目标类型、构图、平场、拼接、H-alpha/IFN/尘埃风险，以及背景模型试验效果。

## 星点

`stars` 检测局部最大值，并对未饱和星点状斑块计算扣除背景后的二阶矩。

可用字段：

- `usable_star_count`；
- `fwhm_major_median_px` 和 `fwhm_minor_median_px`；
- `axis_ratio_median`；
- `eccentricity_median` 和 `eccentricity_p90`；
- `position_angle_median_deg`。

局限性：

- 这不是非线性 PSF 拟合；
- 重叠、星云结块、衍射芒、欠采样和已处理的星点都可能使结果产生偏差；
- 仅凭一个中位角无法在不检查空间一致性的前提下诊断跟踪误差；
- 以像素为单位的 FWHM 不等于以角秒为单位的视宁度，除非有可靠的像素比例；
- 已完成后期处理的图像不能可靠地用于判断采集阶段的 FWHM。

若少于五个候选星点通过验证，该节报告 `unavailable`。不得凭空编造星点形态结论。

## 色彩

`color` 报告背景通道中值、通道 P99、相关性以及塌缩通道。

- 背景不平衡可能源于光害、校准、滤镜响应或真实发射信号。
- 发射天区的高红色信号不自动等同于红色偏色。
- 窄带和双窄带数据不遵从宽带白平衡假设。
- 通道相关性描述形态相似性，而非色彩精度。

基于星表的色彩精度需要 WCS、合适的恒星光度测量、仪器响应和未饱和恒星测量；本分析器不执行此类验证。

## 分类

`classification` 使用 FITS/XISF 元数据和文件名。

- `frame_role`: 可能的亮场/暗场/平场/偏置，或未知；
- `processing_stage`: 可能的已叠加/母版状态，或未知；
- `transfer_state`: 天文容器通常为线性，但不保证；
- `channel_model`: 从维度、滤镜和 Bayer 元数据推断。

除非有采集历史确认，所有分类字段均视为暂定。
