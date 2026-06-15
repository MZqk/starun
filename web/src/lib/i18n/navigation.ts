export const navigationCopy = {
  brand: {
    name: "Starun",
    tagline: "深空后期智能处理平台",
  },
  nav: {
    ariaLabel: "主导航",
    home: "首页",
    analysis: "专业分析",
    processing: "AI 自动出图",
    history: "历史记录",
    upload: "上传 FITS",
    openMenu: "打开导航菜单",
    closeMenu: "关闭导航菜单",
  },
} as const;

export const navigationItems = [
  { href: "/", label: navigationCopy.nav.home },
  { href: "/analysis", label: navigationCopy.nav.analysis },
  { href: "/processing", label: navigationCopy.nav.processing },
  { href: "/history", label: navigationCopy.nav.history },
] as const;
