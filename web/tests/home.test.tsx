import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import HomePage from "../src/app/page";
import NavBar from "../src/components/NavBar";

let pathname = "/";

const globalStyles = readFileSync(
  resolve(process.cwd(), "src/app/globals.css"),
  "utf8",
);
const layoutSource = readFileSync(
  resolve(process.cwd(), "src/app/layout.tsx"),
  "utf8",
);
const navBarSource = readFileSync(
  resolve(process.cwd(), "src/components/NavBar.tsx"),
  "utf8",
);

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));

describe("home page", () => {
  beforeEach(() => {
    pathname = "/";
  });

  it("presents the approved hero and exact primary actions", () => {
    render(<HomePage />);

    const heading = screen.getByRole("heading", {
      level: 1,
      name: "让每一帧深空数据，得到专业判断",
    });
    expect(heading).toBeInTheDocument();
    expect(document.querySelectorAll("h1")).toHaveLength(1);

    expect(screen.getByRole("link", { name: "开始分析" })).toHaveAttribute(
      "href",
      "/analysis",
    );
    expect(screen.getByRole("link", { name: "自动出图" })).toHaveAttribute(
      "href",
      "/processing",
    );
  });

  it("keeps the compact FITS signal panel informative but noninteractive", () => {
    render(<HomePage />);

    const signalPanel = screen.getByRole("complementary", {
      name: "从一张线性 FITS 开始",
    });

    expect(within(signalPanel).queryByRole("link")).not.toBeInTheDocument();
    expect(within(signalPanel).getAllByText(/FITS/).length).toBeGreaterThan(0);
    expect(within(signalPanel).getByText("≤ 500 MB")).toBeInTheDocument();
  });

  it("reveals the horizontal feature arrow on hover and keyboard focus", () => {
    expect(globalStyles).toMatch(
      /\.feature-card--horizontal \.feature-link svg\s*{[^}]*opacity:\s*0;/,
    );
    expect(globalStyles).toMatch(
      /\.feature-card--horizontal:hover \.feature-link svg,\s*\.feature-card--horizontal:focus-within \.feature-link svg\s*{[^}]*opacity:\s*1;/,
    );
    expect(globalStyles).toMatch(
      /\.feature-card--horizontal \.feature-link svg\s*{[^}]*transition:[^}]*opacity/,
    );
    expect(globalStyles).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
  });

  it("makes format, retention, local history, and demo boundaries visible", () => {
    render(<HomePage />);

    expect(screen.getAllByText(/FITS/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/500 MB/).length).toBeGreaterThan(0);
    expect(screen.getByText(/24 小时/)).toBeInTheDocument();
    expect(screen.getByText(/仅保存在当前浏览器/)).toBeInTheDocument();
    expect(screen.getAllByText(/milestone/i).length).toBeGreaterThan(0);
  });

  it("uses landmarks and exposes every feature destination", () => {
    render(<HomePage />);

    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "核心能力" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "使用流程" })).toBeInTheDocument();

    expect(screen.getByRole("link", { name: /查看专业分析/ })).toHaveAttribute(
      "href",
      "/analysis",
    );
    expect(screen.getByRole("link", { name: /进入自动出图/ })).toHaveAttribute(
      "href",
      "/processing",
    );
    expect(screen.getByRole("link", { name: /查看本地历史/ })).toHaveAttribute(
      "href",
      "/history",
    );
  });

  it("does not show fabricated product metrics", () => {
    render(<HomePage />);

    expect(screen.queryByText(/用户量|成功率|累计处理量/)).not.toBeInTheDocument();
  });

  it("states the static shared daily task rule without a live remaining count", () => {
    render(<HomePage />);

    expect(
      screen.getByText("每日最多 5 次任务，分析与自动出图共享"),
    ).toBeVisible();
    expect(screen.queryByText(/剩余\s*\d+\s*次/)).not.toBeInTheDocument();
  });
});

describe("navigation", () => {
  beforeEach(() => {
    pathname = "/";
  });

  it("marks only the desktop home link as the current page", () => {
    render(<NavBar />);

    const navigation = screen.getByRole("navigation", { name: "主导航" });
    const links = within(navigation).getAllByRole("link");

    expect(within(navigation).getByRole("link", { name: "首页" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    for (const link of links.filter((item) => item.textContent !== "首页")) {
      expect(link).not.toHaveAttribute("aria-current");
    }
  });

  it("contains all product links and the FITS upload action", () => {
    render(<NavBar />);

    const navigation = screen.getByRole("navigation", { name: "主导航" });
    expect(within(navigation).getByRole("link", { name: "首页" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(
      within(navigation).getByRole("link", { name: "专业分析" }),
    ).toHaveAttribute("href", "/analysis");
    expect(
      within(navigation).getByRole("link", { name: "AI 自动出图" }),
    ).toHaveAttribute("href", "/processing");
    expect(
      within(navigation).getByRole("link", { name: "历史记录" }),
    ).toHaveAttribute("href", "/history");
    expect(screen.getByRole("link", { name: "上传 FITS" })).toHaveAttribute(
      "href",
      "/analysis",
    );
  });

  it("toggles the mobile menu with an accessible disclosure button", async () => {
    const user = userEvent.setup();
    render(<NavBar />);

    const toggle = screen.getByRole("button", { name: "打开导航菜单" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("mobile-navigation")).not.toBeInTheDocument();

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAccessibleName("关闭导航菜单");
    expect(screen.getByTestId("mobile-navigation")).toBeInTheDocument();

    await user.click(toggle);
    expect(screen.queryByTestId("mobile-navigation")).not.toBeInTheDocument();
  });

  it("marks only the mobile home link as the current page", async () => {
    const user = userEvent.setup();
    render(<NavBar />);

    await user.click(screen.getByRole("button", { name: "打开导航菜单" }));

    const navigation = screen.getByTestId("mobile-navigation");
    const links = within(navigation).getAllByRole("link");

    expect(within(navigation).getByRole("link", { name: "首页" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    for (const link of links.filter((item) => item.textContent !== "首页")) {
      expect(link).not.toHaveAttribute("aria-current");
    }
  });

  it("uses segment boundaries for non-root active routes", () => {
    pathname = "/analysis/report";
    const { rerender } = render(<NavBar />);

    expect(
      within(screen.getByRole("navigation", { name: "主导航" })).getByRole(
        "link",
        { name: "专业分析" },
      ),
    ).toHaveAttribute("aria-current", "page");

    pathname = "/analysis-old";
    rerender(<NavBar />);

    expect(
      within(screen.getByRole("navigation", { name: "主导航" })).getByRole(
        "link",
        { name: "专业分析" },
      ),
    ).not.toHaveAttribute("aria-current");
  });

  it("closes the mobile menu on Escape and restores focus to the toggle", async () => {
    const user = userEvent.setup();
    render(<NavBar />);

    const toggle = screen.getByRole("button", { name: "打开导航菜单" });
    await user.click(toggle);
    await user.keyboard("{Escape}");

    expect(screen.queryByTestId("mobile-navigation")).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
  });

  it("closes the mobile menu when a route link is activated", async () => {
    const user = userEvent.setup();
    render(<NavBar />);

    await user.click(screen.getByRole("button", { name: "打开导航菜单" }));
    const routeLink = within(screen.getByTestId("mobile-navigation")).getByRole(
      "link",
      { name: "专业分析" },
    );
    routeLink.addEventListener("click", (event) => event.preventDefault(), {
      once: true,
    });
    await user.click(routeLink);

    expect(screen.queryByTestId("mobile-navigation")).not.toBeInTheDocument();
  });
});

describe("web quality boundaries", () => {
  it("loads local Geist package fonts and keeps navigation copy focused", () => {
    expect(layoutSource).toContain('from "geist/font/sans"');
    expect(layoutSource).toContain('from "geist/font/mono"');
    expect(layoutSource).toMatch(/<body[^>]*className=/);
    expect(globalStyles).toMatch(
      /body\s*{[^}]*--type-ui:\s*var\(--font-geist-sans\)[^}]*font-family:\s*var\(--type-ui\)/,
    );
    expect(globalStyles).toMatch(
      /body\s*{[^}]*--type-mono:\s*var\(--font-geist-mono\)/,
    );
    expect(navBarSource).toContain("../lib/i18n/navigation");
    expect(navBarSource).not.toContain("../lib/i18n/zh-CN");
  });

  it("uses the PRD md breakpoint and does not hide root overflow", () => {
    expect(globalStyles).toContain("@media (max-width: 767px)");
    expect(globalStyles).not.toContain("@media (max-width: 900px)");
    expect(globalStyles).not.toMatch(
      /body\s*{[^}]*overflow-x:\s*(?:hidden|clip)/,
    );
  });

  it("keeps essential small copy off the muted token", () => {
    expect(globalStyles).toMatch(
      /\.upload-specs dt,\s*\.upload-scope\s*{[^}]*color:\s*var\(--color-space-text-secondary\)/,
    );
    expect(globalStyles).toMatch(
      /\.steps-list p\s*{[^}]*color:\s*var\(--color-space-text-secondary\)/,
    );
    expect(globalStyles).toMatch(
      /\.footer-inner\s*{[^}]*color:\s*var\(--color-space-text-secondary\)/,
    );
    expect(globalStyles).toMatch(
      /\.nav-link\.is-active\s*{[^}]*color:\s*var\(--color-space-text\)/,
    );
  });
});
