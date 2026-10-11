"use client";
import { useId, useState, type ReactNode } from "react";
import { Card, Notice } from "@/components/ui";

export type View = "active" | "archived" | "all";

/** Active / Archived / All switch. Archived items are hidden by default. */
export function ViewSwitch({ view, setView, archived }: { view: View; setView: (v: View) => void; archived?: number }) {
  return (
    <div className="tabs" style={{ marginBottom: 0 }} role="group" aria-label="Show">
      {(["active", "archived", "all"] as View[]).map((v) => (
        <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>
          {v === "archived" ? `Archived${archived ? ` (${archived})` : ""}` : v === "all" ? "All" : "Active"}
        </button>
      ))}
    </div>
  );
}

/** Text input with suggestions from existing categories; "" clears. */
export function CategoryInput({ value, onChange, categories, placeholder = "Category" }: { value: string; onChange: (v: string) => void; categories: string[]; placeholder?: string }) {
  const id = useId();
  return (
    <>
      <input aria-label="Category" list={id} value={value} maxLength={60} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} style={{ width: 160 }} />
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
  if (!count) return null;
  return (
    <div className="row bulk-bar">
      <b>{count} selected</b>
      <CategoryInput value={category} onChange={setCategory} categories={categories} placeholder="Set category…" />
      <button disabled={busy} onClick={() => onCategory(category)}>{category.trim() ? "Set category" : "Clear category"}</button>
      <button disabled={busy} onClick={() => onArchive(true)}>Archive</button>
      <button disabled={busy} onClick={() => onArchive(false)}>Unarchive</button>
      <button className="link" onClick={onClear}>Clear selection</button>
    </div>
  );
}

/** Inline category editor for one item. */
export function CategoryEditor({ value, categories, busy, onSave }: { value: string | null; categories: string[]; busy: boolean; onSave: (c: string) => void }) {
  const [draft, setDraft] = useState(value ?? "");
  return (
    <span className="row" style={{ gap: 4 }}>
      <CategoryInput value={draft} onChange={setDraft} categories={categories} />
      <button disabled={busy || draft.trim() === (value ?? "")} onClick={() => onSave(draft)}>Save category</button>
    </span>
  );
}

/** Permanent delete, gated on typing a confirmation string. */
export function DeleteZone({ what, confirmText, blockers, busy, children, onDelete }: {
  what: string; confirmText: string; blockers?: string[]; busy: boolean; children?: ReactNode; onDelete: () => void;
}) {
  const [typed, setTyped] = useState("");
  const blocked = !!blockers?.length;
  return (
    <Card title="Delete permanently" className="mt danger-zone">
      <details>
        <summary>Delete this {what}…</summary>
        <p>This can't be undone. Archive instead if you only want to hide it.</p>
        {children}
        {blocked ? (
          <Notice kind="warn">Can't delete right now: {blockers!.join("; ")}.</Notice>
        ) : (
          <div className="row">
            <input aria-label="Confirmation" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={`Type ${confirmText} to confirm`} style={{ minWidth: 260 }} />
            <button className="danger" disabled={busy || typed !== confirmText} onClick={onDelete}>Delete permanently</button>
          </div>
        )}
      </details>
    </Card>
  );
}
