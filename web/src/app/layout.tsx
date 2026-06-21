import type { Metadata } from "next";
import type { ReactNode } from "react";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import NavBar from "../components/NavBar";
import { zhCN } from "../lib/i18n/zh-CN";
import "./globals.css";

export const metadata: Metadata = {
  title: `${zhCN.brand.name} | ${zhCN.brand.tagline}`,
  description: zhCN.home.hero.description,
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className={`${GeistSans.variable} ${GeistMono.variable}`}>
        <div className="starfield" aria-hidden="true">
          <div className="star star--1" />
          <div className="star star--2" />
          <div className="star star--3" />
          <div className="star star--4" />
          <div className="star star--5" />
        </div>
        <NavBar />
        {children}
      </body>
    </html>
  );
}
