import { navigationCopy } from "./navigation";

export const zhCN = {
  ...navigationCopy,
  home: {
    hero: {
      titlePrefix: "让每一帧深空数据，得到",
      titleEmphasis: "专业判断",
      fullTitle: "让每一帧深空数据，得到专业判断",
      description:
        "为有经验的天文摄影爱好者解析已校准、已叠加的线性 FITS 或 XISF，获得专业后期建议，或交给 Agent 自动生成艺术增强成片。",
      analysisCta: "开始分析",
      processingCta: "自动出图",
    },
    uploadSignal: {
      title: "从一张线性天文图像开始",
      description: "上传后解析 FITS HDU 或 XISF 图像，选择可处理的最大图像。",
      action: "选择文件并开始分析",
      formatLabel: "格式",
      formatValue: ".fits / .fit / .fts / .xisf",
      sizeLabel: "上限",
      sizeValue: "≤ 500 MB",
      scope: "支持 FITS / XISF",
      quota: "每日最多 5 次任务，分析与自动出图共享",
    },
    faq: {
      kicker: "新手科普",
      title: "天文学与文件格式快速入门 FAQ",
      items: [
        {
          question: "什么是原始的 FITS 或 XISF 图像文件？",
          answer: "FITS (Flexible Image Transport System) 与 XISF (Extensible Image Serialization Format) 是天文学界与专业深空摄影的标准数据格式。它们采用高位深（通常是 16位 或 32位 浮点）保存了天体光子响应的纯线性科学数据。线性数据未经相机渲染或曲线拉伸，因此看起来通常漆黑一片，但蕴含着宝贵的物理信噪比信息。"
        },
        {
          question: "为什么天文图像在处理前必须进行后期分析？",
          answer: "深空摄影捕获的信号极微弱，天光背景中充斥着光害梯度、暗角和热噪声。后期分析（如 SNR 信噪比、FWHM 半高全宽、星点椭圆率）能帮助我们客观量化图像源品质，以此指导降噪强度、反卷积锐化系数以及通道色彩平衡，防止破坏脆弱的天体气流细节。"
        },
        {
          question: "手头没有这两种天文专业图像文件，如何体验？",
          answer: "FITS/XISF 只能由天文专用相机或天文叠加软件（如 PixInsight、DSS）输出。如果您只是想尝试本工具，可以从导航栏进入“开始分析”或“自动出图”流程，系统提供了内置的深空天体示例文件，无需上传本地文件，一键即可载入并体验完整的处理与分析报告流程。"
        }
      ]
    },
    features: {
      ariaLabel: "核心能力",
      heading: "从判断到成片，保持过程清晰",
      description:
        "先看懂数据，再决定处理方式。所有 AI 输出都会明确标识能力边界。",
      analysis: {
        title: "专业分析",
        description:
          "查看 FITS/XISF 元数据、图像尺寸、位深与基础统计，并获得分步骤的专业解读、处理方案和推荐参数。",
        link: "查看专业分析",
        tags: ["FITS/XISF 解析", "处理建议", "参数报告"],
      },
      processing: {
        title: "AI 自动出图",
        description:
          "选择真实、平衡或艺术风格后，由 AI Agent 生成参考预览、艺术指导和最终增强图。",
        link: "进入自动出图",
        tags: ["无人值守", "三种风格"],
      },
      history: {
        title: "本地历史",
        description:
          "任务索引仅保存在当前浏览器。换设备、无痕模式或清除站点数据后无法恢复，也不保证永久保存。",
        link: "查看本地历史",
        tags: ["当前浏览器", "无账户"],
      },
    },
    steps: {
      ariaLabel: "使用流程",
      heading: "三步完成一次后期决策",
      items: [
        {
          title: "上传线性 FITS/XISF",
          description: "选择合法且不超过 500 MB 的 .fits、.fit、.fts 或 .xisf 文件。",
        },
        {
          title: "检查报告或选择风格",
          description: "查看分析建议，或选择真实、平衡、艺术风格启动自动出图流程。",
        },
        {
          title: "获取报告或处理产物",
          description: "阅读结构化报告，或查看参考预览、生成图与处理记录。",
        },
      ],
    },
    privacy: {
      heading: "数据保留与资源边界",
      body: "上传文件与服务端产物将在 24 小时后删除。本地历史只保存任务摘要；服务端文件过期后，历史记录仍可能存在，但结果不可继续下载。",
      resource:
        "服务运行在有限 CPU、内存和磁盘资源上，不承诺对所有合法 FITS/XISF 结构或 500 MB 文件都能完成处理。",
    },
  },
  task11: {
    common: {
      mock: "Mock",
      unavailable: "未提供",
      notApplicable: "不适用",
      hduLabel: (index: number | string) => `HDU ${index}`,
      taskPollingError: "任务状态同步失败，请稍后重试。",
      taskPollingTimeout: "任务状态同步超时，服务长时间未响应或已假死，请重试。",
      historyPersistenceError: "本地历史记录写入失败。",
      storageWarning: (message: string) =>
        `任务状态仍会继续更新，但本地历史写入失败：${message}`,
    },
    upload: {
      title: "上传 FITS/XISF 文件",
      description:
        "拖放文件到这里，或使用文件选择器。支持 .fits、.fit、.fts、.xisf，最大 500 MB。",
      inputLabel: "选择 FITS/XISF 文件",
      choose: "选择文件",
      refreshNotice: "刷新页面会中断上传，请保持当前页面打开。",
      quotaNotice: "上传与校验不计入任务额度，创建任务前不会扣除额度。",
      validating: "服务器校验中",
      ready: "校验完成",
      cancel: "取消上传",
      replace: "重新选择",
      cancelled: "上传已取消。",
      networkError: "网络连接中断，上传未完成。",
      offlineError: "网络连接断开。请检查您的网络连接并重试。",
      genericError: "上传失败，请稍后重试。",
      extensionError: "请选择 .fits、.fit、.fts 或 .xisf 文件。",
      sizeError: "文件超过 500 MB，未开始上传。",
      validationAriaLabel: "服务器校验结果",
      validationKicker: "真实天文图像校验",
      validationCount: (count: number) => `服务器已读取 ${count} 个图像单元`,
      selected: "选中",
      name: "名称",
      shape: "尺寸",
      dtype: "数据类型",
    },
    status: {
      kicker: "任务状态",
      labels: {
        queued: "排队中",
        running: "处理中",
        cancelling: "正在取消",
        cancelled: "已取消",
        completed: "已完成",
        review_required: "需要质量审查",
        failed: "失败",
        expired: "已过期",
      },
      waiting: "等待调度",
      cancel: "取消任务",
      cancelling: "正在取消",
      expiryLabel: "结果过期时间",
      remainingLabel: "剩余时间",
      expiredNow: "已过期",
      remaining: (seconds: number) => {
        if (seconds < 60) return `剩余 ${seconds} 秒`;
        const minutes = Math.floor(seconds / 60);
        const rest = seconds % 60;
        return rest > 0 ? `剩余 ${minutes} 分 ${rest} 秒` : `剩余 ${minutes} 分`;
      },
      errorCode: "错误代码",
      retryability: "重试条件",
      retryable: "可重试",
      notRetryable: "不可重试",
      quota: "额度状态",
      quotaCharged: "已扣除额度",
      quotaNotCharged: "未扣除额度",
    },
    analysis: {
      kicker: "专业分析",
      title: "读取真实 FITS，生成多模态专业分析",
      description:
        "系统解析 FITS 元数据，并由专业深空分析 Agent 调用 deep-sky-advisor 生成分析与后期建议。",
      unnamedFile: "未命名 FITS",
      creating: "正在创建任务…",
      create: "开始专业分析",
      quotaNotice: "创建任务时才会占用每日额度。",
      createError: "任务创建失败，请稍后重试。",
      cancelError: "取消失败。",
      restoring: "正在恢复任务状态…",
      realDataKicker: "真实数据",
      realDataTitle: "FITS / HDU / 基础统计",
      realDataAriaLabel: "真实 FITS、HDU、头信息与基础统计",
      hduListTitle: "全部 HDU",
      headerTitle: "FITS 头信息",
      statisticsTitle: "基础统计",
      hduIndex: "HDU",
      hduName: "名称",
      hduKind: "类型",
      shape: "形状",
      dtype: "数据类型",
      support: "处理支持",
      supported: "支持",
      unsupported: "不支持",
      minimum: "最小值",
      maximum: "最大值",
      mean: "均值",
      median: "中位数",
      standardDeviation: "标准差",
      finitePixelCount: "有限像素",
      aiKicker: "专业分析 Agent",
      aiTitle: "专业解读与后期建议",
      previewTitle: "AI 分析所用预览",
      previewAriaLabel: "由 FITS 生成并发送给 AI 分析的显示预览",
      previewLoading: "正在认证加载分析预览…",
      previewError: "分析预览加载失败。",
      previewDisclaimer:
        "该图经过显示拉伸，仅用于视觉分析，不代表线性原始数据或最终成片。",
      overviewTitle: "总体判断",
      qualityRating: "综合质量",
      confidence: "视觉判断置信度",
      qualityLabels: {
        excellent: "优秀",
        good: "良好",
        fair: "一般",
        poor: "较差",
      } as Record<string, string>,
      observationsTitle: "视觉观察",
      target: "主体与构图",
      background: "背景与梯度",
      stars: "星点",
      noise: "噪声",
      color: "色彩",
      issuesTitle: "主要问题",
      severityLabels: {
        low: "低",
        medium: "中",
        high: "高",
      } as Record<string, string>,
      workflowTitle: "建议处理流程",
      workflowGroupTitles: {
        general: "深空天体后期处理建议",
        siril: "Siril 软件的后期关键步骤",
        pixinsight: "PixInsight 软件的后期关键步骤",
        photoshop: "Photoshop 软件的后期关键步骤",
      } as Record<string, string>,
      caveatsTitle: "限制与不确定性",
      continueKicker: "继续工作流",
      sourceValid: "源文件仍在有效期内",
      processAction: "使用此文件自动出图",
      astroGuideKicker: "天体物理学与后期指南",
      astroGuideTitle: "如何理解与优化这些物理指标？",
      astroGuideItems: [
        {
          title: "信噪比 (SNR) ── 信号质量的黄金标准",
          desc: "信噪比衡量天体信号与无规则背景噪声的比例。低信噪比意味着画面背景粗糙，星云细节淹没在噪点中。\n• 拍摄提升：延长单张曝光，或者累积更多总曝光时间（如从 2 小时增加到 10 小时）进行多张叠加。\n• 后期优化：在图像处于线性状态时使用基于多尺度变换的算法（如 MMT/TGVDenoise）进行精细降噪，平滑暗部并保护星云边缘。"
        },
        {
          title: "半高全宽 (FWHM) ── 星点锐利度的科学度量",
          desc: "FWHM 反映了恒星的半高全宽尺寸。值越小，代表星点越锐利、细节越丰富，受大气视宁度、赤道仪抖动及光学焦准影响。\n• 拍摄提升：使用高精度自动对焦系统每隔 1-2 小时或温度变化 1°C 时重新对焦；优化赤道仪导星（RMS 误差维持在 0.5\" 以下）。\n• 后期优化：在非线性拉伸前执行 Richardson-Lucy 反卷积算法以修复大气带来的弥散，或在拉伸后使用缩星算法以恢复画面锐度。"
        },
        {
          title: "椭圆率 (Ellipticity) ── 跟踪与光路的精准度",
          desc: "椭圆率表示星点的圆度偏离。星点拉长（椭圆率过高）通常是由极轴不准、导星拖尾、机械形变或边缘像差引起。\n• 拍摄提升：仔细进行三点极轴校准，定期检查导星稳定性；如果仅四周星点拉长，需检查焦平面倾斜度及平场镜/改正镜的后截距。\n• 后期优化：使用形变修复工具对拖尾方向进行矢量补偿修圆，或对高椭圆率星点进行定向收缩。"
        }
      ],
    },
    processing: {
      kicker: "AI 自动出图",
      title: "选择一种风格，生成 AI 艺术增强成片",
      description:
        "系统会从 FITS 生成参考预览，由 Kimi 给出艺术指导，再调用图片生成模型输出参考图约束下的成片。",
      unnamedFile: "未命名 FITS",
      analysisSourceFile: "分析任务源文件",
      previewError: "预览加载失败。",
      sourceKicker: "已选择分析源",
      sourceDescription: "将复用仍在有效期内的分析任务源文件，不需要再次上传。",
      styleLegend: "处理风格",
      styleAriaLabel: "处理风格",
      styles: {
        realistic: {
          label: "写实",
          description: "Agent 直接调用专业 Skill，保守处理原始图片",
          previewDesc: "写实风格专注于还原真实的科学色彩与恒星亮度分布。它严格抑制星云的过度拉伸，保持宁静、暗弱的暗室背景，适合追求天体物理学真实性的摄影者。",
        },
        balanced: {
          label: "平衡",
          description: "Kimi 生成风格指导，再由专业 Skill 完成处理",
          previewDesc: "平衡风格是在写实与艺术表现之间取得的完美平衡。它适度拉伸星云以呈现更多云气细节，并运用柔和的红蓝光谱色调，表现星系和星云的丰富层次。",
        },
        artistic: {
          label: "艺术",
          description: "Kimi 生成美化建议，腾讯混元执行图生图",
          previewDesc: "艺术风格采用现代艺术美学进行强烈的艺术化拉伸。星点带有亮眼迫人的十字星芒，星云颜色对比剧烈（如经典的哈勃色调），视觉效果极其震撼。",
        },
      },
      creating: "正在创建任务…",
      create: "开始 AI 自动出图",
      styleNotice: "仅能选择一个处理风格，默认使用“平衡”。",
      createError: "任务创建失败，请稍后重试。",
      cancelError: "取消失败。",
      restoring: "正在恢复任务状态…",
      planAriaLabel: "AI Agent 处理计划",
      planLabel: "AI Agent",
      planTitle: "处理计划",
      planUpdated: "已从工具事件更新",
      planWaiting: "等待 Agent 工具事件",
      comparisonAriaLabel: "处理前后对比",
      before: "处理前",
      sourcePreviewAriaLabel: "真实 FITS 元数据源预览",
      rawFits: "原始线性 FITS",
      previewLabel: "AI 生成成片",
      previewAriaLabel: "AI 生成处理后预览",
      previewLoading: "正在加载处理预览…",
      balancedStyle: "平衡风格",
      disclaimer: "AI 自动出图是艺术增强结果，不可替代科学后期与真实性校验。",
      directionKicker: "生成说明",
      directionTitle: "AI 艺术指导",
      downloadLabel: "AI 自动出图产物",
      toolNames: {
        "processing.prepare_reference": "生成 FITS 参考预览",
        "processing.plan_art_direction": "Kimi 生成艺术指导",
        "processing.generate_artwork": "图片模型生成成片",
      } as Record<string, string>,
      expiredTitle: "处理结果已过期",
      resultUnavailable: "预览与下载结果已不可用。",
      sourceMetadata: (hdu: number | string, shape: string, range: string) =>
        [
          `HDU ${hdu}`,
          shape,
          range,
        ].filter(Boolean).join(" · "),
    },
    events: {
      liveLabel: "实时事件",
      mockLabel: "Mock 工具事件日志",
      title: "处理日志",
      count: (count: number) => `${count} 条`,
      empty: "任务事件将在这里按顺序显示。",
    },
    downloads: {
      label: "处理产物下载",
      title: "导出文件",
      preparing: "准备下载…",
      action: (name: string) => `下载 ${name}`,
      authNotice: "客户端标识仅通过请求头发送，不会写入下载 URL。",
      error: "下载失败。",
    },
    history: {
      kicker: "历史记录",
      title: "仅保存在当前浏览器的任务摘要",
      description:
        "这里不会保存 FITS、TIFF、PNG 或其他二进制数据。清除浏览器数据、更换设备或使用隐私模式都可能丢失记录。",
      durabilityNotice:
        "本地历史不是云端账户记录，也不是永久存档。结果文件仍受服务端有效期限制。",
      loading: "正在读取本地历史…",
      loadError: "无法读取本地历史。",
      retryError: "重试失败。",
      deleteError: "服务端删除失败。",
      emptyTitle: "还没有任务记录",
      emptyDescription: "创建分析或自动出图任务后，状态摘要会写入当前浏览器。",
      upload: "上传 FITS",
      analysisType: "专业分析",
      processingType: "AI 自动出图",
      createdAt: "创建时间",
      style: "风格",
      result: "结果",
      resultUnavailable: "结果不可用",
      resultAvailable: "有效期内可重新下载",
      open: "打开并继续",
      retry: "重试",
      remove: "删除记录",
    },
  },
  footer: {
    note: "Starun · 专业但不复杂",
    boundary: "FITS only · AI preview milestone",
  },
} as const;
