"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { post } from "@/lib/api";
import { cls, et } from "@/lib/format";
import { Logo } from "./Logo";
import { Pill } from "./ui";
import { useSession } from "./session";

const NAV: [string, string][] = [
  ["/", "Overview"],
  ["/approvals", "Trade approvals"],
  ["/portfolio", "Portfolio"],
  ["/funds", "Funds"],
  ["/strategies", "Strategies"],
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
  const on = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  const sim = status?.sim || {};

  async function logout() {
    await post("/api/logout").catch(() => {});
    location.href = "/login";
  }

  const link = ([href, label]: [string, string]) => (
    <Link key={href} href={href} className={cls(on(href) && "on")}>
      {label}
      {href === "/approvals" && !!status?.pending_count && <Pill kind="bad">{status.pending_count}</Pill>}
    </Link>
  );

  return (
    <div className="layout">
      <nav className="side">
        <Link href="/" className="brand" aria-label="llamaOS home">
          <Logo size={34} />
          <span>
            llamaOS<small>{status?.sim.source === "alpaca_paper" ? "Alpaca paper" : "simulator"} · {status?.mode ?? "…"}</small>
          </span>
        </Link>
        {NAV.map(link)}
        <div className="sep" />
        {NAV2.map(link)}
        <div className="sep" />
        <div className="who">
          {me.username} · {me.role}
        </div>
        <div style={{ padding: "0 8px" }}>
          <button onClick={logout}>Log out</button>
        </div>
      </nav>
      <main>
        <div className="topbar">
          <Pill kind="accent">SIMULATOR · {sim.session ?? "—"}</Pill>
          <Pill>
            {sim.phase ?? "not started"} · {status?.sim_now ? et(status.sim_now) : "—"} ET
          </Pill>
          {sim.paused && <Pill kind="warn">paused</Pill>}
          {sim.waiting_for_approval && <Pill kind="warn">clock held for approval</Pill>}
          {status?.kill?.active && (
            <Pill kind="bad">
              <span className="dot" /> KILL SWITCH ACTIVE
            </Pill>
          )}
          <span className="grow" />
          {me.role === "viewer" && <Pill>read-only</Pill>}
        </div>
        {children}
      </main>
    </div>
  );
}
