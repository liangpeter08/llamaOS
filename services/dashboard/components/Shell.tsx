"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { post } from "@/lib/api";
import { cls, et } from "@/lib/format";
import { LangToggle, useT } from "@/lib/i18n";
import { Logo } from "./Logo";
import { Pill } from "./ui";
import { useSession } from "./session";

const NAV: [string, string][] = [
  ["/", "Overview"],
  ["/approvals", "Trade approvals"],
  ["/portfolio", "Portfolio"],
  ["/positions", "Positions"],
  ["/funds", "Funds"],
  ["/strategies", "Strategies"],
  ["/backtests", "Backtesting"],
  ["/ideas", "Idea Lab"],
  ["/theses", "Theses"],
  ["/news", "News & signals"],
  ["/watchlist", "Monitoring watchlist"],
];
const NAV2: [string, string][] = [
  ["/system", "System"],
  ["/audit", "Audit log"],
  ["/risk", "Risk"],
];

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { me, status } = useSession();
  const t = useT();
  const on = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  const sim = status?.sim || {};

  async function logout() {
    await post("/api/logout").catch(() => {});
    location.href = "/login";
  }

  const link = ([href, label]: [string, string]) => (
    <Link key={href} href={href} className={cls(on(href) && "on")}>
      {t(label)}
      {href === "/approvals" && !!status?.pending_count && <Pill kind="bad">{status.pending_count}</Pill>}
    </Link>
  );

  return (
    <div className="layout">
      <nav className="side">
        <Link href="/" className="brand" aria-label="llamaOS home">
          <Logo size={34} />
          <span>
            llamaOS<small>{t(status?.sim.source === "alpaca_paper" ? "Alpaca paper" : "simulator")} · {status?.mode ? t(status.mode) : "…"}</small>
          </span>
        </Link>
        {NAV.map(link)}
        <div className="sep" />
        {NAV2.map(link)}
        <div className="sep" />
        <div className="who">
          {me.username} · {t(me.role)}
        </div>
        <div style={{ padding: "0 8px" }}>
          <button onClick={logout}>{t("Log out")}</button>
        </div>
      </nav>
      <main>
        <div className="topbar">
          <Pill kind="accent">{t("SIMULATOR")} · {sim.session ?? "—"}</Pill>
          <Pill>
            {t(sim.phase ?? "not started")} · {status?.sim_now ? et(status.sim_now) : "—"} {t("ET")}
          </Pill>
          {sim.paused && <Pill kind="warn">{t("paused")}</Pill>}
          {sim.waiting_for_approval && <Pill kind="warn">{t("clock held for approval")}</Pill>}
          {status?.kill?.active && (
            <Pill kind="bad">
              <span className="dot" /> {t("KILL SWITCH ACTIVE")}
            </Pill>
          )}
          <span className="grow" />
          {me.role === "viewer" && <Pill>{t("read-only")}</Pill>}
          <LangToggle />
        </div>
        {children}
      </main>
    </div>
  );
}
