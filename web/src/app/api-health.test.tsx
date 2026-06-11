import { render, screen } from "@testing-library/react";
import HomePage from "./page";

it("shows the two primary product actions", () => {
  render(<HomePage />);
  expect(screen.getByRole("link", { name: "开始分析" })).toHaveAttribute(
    "href",
    "/analysis",
  );
  expect(screen.getByRole("link", { name: "自动出图" })).toHaveAttribute(
    "href",
    "/processing",
  );
});
