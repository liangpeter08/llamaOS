"use client";
import { useId, useState, type ReactNode } from "react";
import { Card, Notice } from "@/components/ui";
import { useT } from "@/lib/i18n";

export type View = "active" | "archived" | "all";

/** Active / Archived / All switch. Archived items are hidden by default. */
export function ViewSwitch({ view, setView, archived }: { view: View; setView: (v: View) => void; archived?: number }) {
  const t = useT();
  return (
    <div className="tabs" style={{ marginBottom: 0 }} role="group" aria-label={t("Show")}>
      {(["active", "archived", "all"] as View[]).map((v) => (
        <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>
          {v === "archived" ? `${t("Archived")}${archived ? ` (${archived})` : ""}` : t(v === "all" ? "All" : "Active")}
        </button>
      ))}
    </div>
  );
}

/** Text input with suggestions from existing categories; "" clears. */
export function CategoryInput({ value, onChange, categories, placeholder }: { value: string; onChange: (v: string) => void; categories: string[]; placeholder?: string }) {
  const id = useId();
  const t = useT();
  return (
    <>
      <input aria-label={t("Category")} list={id} value={value} maxLength={60} placeholder={placeholder ?? t("Category")} onChange={(e) => onChange(e.target.value)} style={{ width: 160 }} />
      <datalist id={id}>{categories.map((c) => <option key={c} value={c} />)}</datalist>
    </>
  );
}

/** Actions for the selected rows of a list. */
export function BulkBar({ count, categories, busy, onCategory, onArchive, onClear }: {
  count: number; categories: string[]; busy: boolean;
  onCategory: (c: string) => void; onArchive: (archived: boolean) => void; onClear: () => void;
}) {
  const [category, setCategory] = useState("");
  const t = useT();
  if (!count) return null;
  return (
    <div className="row bulk-bar">
      <b>{t("{n} selected", { n: count })}</b>
      <CategoryInput value={category} onChange={setCategory} categories={categories} placeholder={t("Set category…")} />
      <button disabled={busy} onClick={() => onCategory(category)}>{t(category.trim() ? "Set category" : "Clear category")}</button>
      <button disabled={busy} onClick={() => onArchive(true)}>{t("Archive")}</button>
      <button disabled={busy} onClick={() => onArchive(false)}>{t("Unarchive")}</button>
      <button className="link" onClick={onClear}>{t("Clear selection")}</button>
    </div>
  );
}

/** Inline category editor for one item. */
export function CategoryEditor({ value, categories, busy, onSave }: { value: string | null; categories: string[]; busy: boolean; onSave: (c: string) => void }) {
  const [draft, setDraft] = useState(value ?? "");
  const t = useT();
  return (
    <span className="row" style={{ gap: 4 }}>
      <CategoryInput value={draft} onChange={setDraft} categories={categories} />
      <button disabled={busy || draft.trim() === (value ?? "")} onClick={() => onSave(draft)}>{t("Save category")}</button>
    </span>
  );
}

/** Permanent delete, gated on typing a confirmation string. */
export function DeleteZone({ what, confirmText, blockers, busy, children, onDelete }: {
  what: string; confirmText: string; blockers?: string[]; busy: boolean; children?: ReactNode; onDelete: () => void;
}) {
  const [typed, setTyped] = useState("");
  const blocked = !!blockers?.length;
  const t = useT();
  return (
    <Card title={t("Delete permanently")} className="mt danger-zone">
      <details>
        <summary>{t("Delete this {what}…", { what })}</summary>
        <p>{t("This can't be undone. Archive instead if you only want to hide it.")}</p>
        {children}
        {blocked ? (
          <Notice kind="warn">{t("Can't delete right now: {why}.", { why: blockers!.join("; ") })}</Notice>
        ) : (
          <div className="row">
            <input aria-label={t("Confirmation")} value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={t("Type {text} to confirm", { text: confirmText })} style={{ minWidth: 260 }} />
            <button className="danger" disabled={busy || typed !== confirmText} onClick={onDelete}>{t("Delete permanently")}</button>
          </div>
        )}
      </details>
    </Card>
  );
}
