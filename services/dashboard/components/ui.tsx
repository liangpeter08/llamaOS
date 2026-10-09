"use client";
import type { ReactNode } from "react";
import { cls } from "@/lib/format";
import type { Check, Cond } from "@/lib/types";

export function Card({ title, children, className, id }: { title?: ReactNode; children: ReactNode; className?: string; id?: string }) {
  return (
    <section className={cls("card", className)} id={id}>
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}

export function Pill({ kind, children, title }: { kind?: "good" | "bad" | "warn" | "accent" | ""; children: ReactNode; title?: string }) {
  return (
    <span className={cls("pill", kind)} title={title}>
      {children}
    </span>
  );
}

export function Stat({ label, value, sub, valueClass }: { label: string; value: ReactNode; sub?: ReactNode; valueClass?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={cls("value", valueClass)}>{value}</div>
      {sub != null && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Notice({ kind, children }: { kind?: "ok" | "bad" | "warn"; children: ReactNode }) {
  return <div className={cls("notice", kind)}>{children}</div>;
}

export function Meter({ frac, danger }: { frac: number; danger?: boolean }) {
  return (
    <div className={cls("bar", danger && "danger")}>
      <span style={{ width: `${Math.max(0, Math.min(1, frac)) * 100}%` }} />
    </div>
  );
}

export function Conditions({ items }: { items?: Cond[] }) {
  if (!items?.length) return null;
  return (
    <div>
      {items.map((c, i) => (
        <div className="cond" key={i}>
          <span className={c.ok ? "ok" : "no"}>{c.ok ? "✓" : "✗"}</span>
          <span>
            <code>{c.cond}</code>
            <br />
            <span className="muted mono">{c.detail ?? String(c.value ?? "missing")}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

export function Checks({ items }: { items?: Check[] | null }) {
  if (!items?.length) return <p className="muted">—</p>;
  return (
    <table>
      <tbody>
        {items.map((g) => (
          <tr key={g.name}>
            <td className={g.ok ? "pos" : "neg"}>{g.ok ? "✓" : "✗"}</td>
            <td>{g.name}</td>
            <td className="muted">{g.detail}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function StatusPill({ status }: { status: string }) {
  const good = ["filled", "submitted", "live", "accepted", "compiled", "t1"];
  const bad = ["rejected", "auto_rejected", "rejected_guard", "void", "retired", "expired", "escalated", "unfilled"];
  const accent = ["paper", "pending", "backtested"];
  return <Pill kind={good.includes(status) ? "good" : bad.includes(status) ? "bad" : accent.includes(status) ? "accent" : ""}>{status}</Pill>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted">{children}</p>;
}

export function Loading({ error }: { error?: string | null }) {
  return error ? <Notice kind="bad">{error}</Notice> : <p className="muted">Loading…</p>;
}
