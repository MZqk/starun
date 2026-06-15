"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  navigationCopy,
  navigationItems,
} from "../lib/i18n/navigation";
import { MenuIcon, StarMark, UploadIcon } from "./Icons";

function isActivePath(pathname: string, href: string) {
  return pathname === href || (href !== "/" && pathname.startsWith(`${href}/`));
}

export default function NavBar() {
  const pathname = usePathname();
  const [menuOpenPath, setMenuOpenPath] = useState<string | null>(null);
  const menuToggleRef = useRef<HTMLButtonElement>(null);
  const menuOpen = menuOpenPath === pathname;

  useEffect(() => {
    if (!menuOpen) {
      return;
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMenuOpenPath(null);
        menuToggleRef.current?.focus();
      }
    }

    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  return (
    <header className="site-header">
      <div className="nav-shell">
        <Link className="brand-link" href="/" aria-label={navigationCopy.brand.name}>
          <span className="brand-mark">
            <StarMark />
          </span>
          <span>{navigationCopy.brand.name}</span>
        </Link>

        <nav className="desktop-nav" aria-label={navigationCopy.nav.ariaLabel}>
          {navigationItems.map((item) => {
            const active = isActivePath(pathname, item.href);
            return (
              <Link
                aria-current={active ? "page" : undefined}
                className={active ? "nav-link is-active" : "nav-link"}
                href={item.href}
                key={item.href}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <Link className="nav-upload" href="/analysis">
          <UploadIcon />
          <span>{navigationCopy.nav.upload}</span>
        </Link>

        <button
          aria-controls="mobile-navigation"
          aria-expanded={menuOpen}
          aria-label={
            menuOpen ? navigationCopy.nav.closeMenu : navigationCopy.nav.openMenu
          }
          className="menu-toggle"
          onClick={() => setMenuOpenPath((openPath) => (
            openPath === pathname ? null : pathname
          ))}
          ref={menuToggleRef}
          type="button"
        >
          <MenuIcon open={menuOpen} />
        </button>
      </div>

      {menuOpen ? (
        <nav
          aria-label={navigationCopy.nav.ariaLabel}
          className="mobile-nav"
          data-testid="mobile-navigation"
          id="mobile-navigation"
        >
          <div className="mobile-nav-inner">
            {navigationItems.map((item) => {
              const active = isActivePath(pathname, item.href);
              return (
                <Link
                  aria-current={active ? "page" : undefined}
                  className={active ? "mobile-nav-link is-active" : "mobile-nav-link"}
                  href={item.href}
                  key={item.href}
                  onClick={() => setMenuOpenPath(null)}
                >
                  {item.label}
                </Link>
              );
            })}
            <Link
              className="mobile-upload"
              href="/analysis"
              onClick={() => setMenuOpenPath(null)}
            >
              <UploadIcon />
              {navigationCopy.nav.upload}
            </Link>
          </div>
        </nav>
      ) : null}
    </header>
  );
}
