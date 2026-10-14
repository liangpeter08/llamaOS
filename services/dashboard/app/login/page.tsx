"use client";
import { useEffect, useState } from "react";
import { get, post } from "@/lib/api";
import { Notice } from "@/components/ui";
import { Logo } from "@/components/Logo";
import { LangToggle, useT } from "@/lib/i18n";

export default function Login() {
  const [hasUsers, setHasUsers] = useState(true);
  const [form, setForm] = useState({ username: "", password: "", code: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const t = useT();

  useEffect(() => {
    get<{ has_users: boolean }>("/api/setup").then((r) => setHasUsers(r.has_users)).catch(() => {});
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await post("/api/login", form);
      location.href = "/";
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="login card">
      <div className="row" style={{ gap: 12, marginBottom: 6 }}>
        <Logo size={48} />
        <h1 style={{ margin: 0, fontSize: 24 }}>llamaOS</h1>
        <span className="grow" style={{ flex: 1 }} />
        <LangToggle />
      </div>
      <p className="muted">{t("Owner and viewer accounts only. 2FA is required.")}</p>
      {!hasUsers && (
        <Notice kind="warn">
          {t("No accounts yet. On the host, run:")}
          <br />
          <code>python -m services.api.create_owner --username admin</code>
        </Notice>
      )}
      {error && <Notice kind="bad">{error}</Notice>}
      <form onSubmit={submit} autoComplete="off">
        <label htmlFor="u">{t("Username")}</label>
        <input id="u" value={form.username} onChange={set("username")} required autoFocus />
        <label htmlFor="p">{t("Password")}</label>
        <input id="p" type="password" value={form.password} onChange={set("password")} required />
        <label htmlFor="c">{t("2FA code")}</label>
        <input id="c" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} placeholder="123456" value={form.code} onChange={set("code")} autoComplete="one-time-code" />
        <p>
          <button className="primary" disabled={busy}>
            {busy ? t("Logging in…") : t("Log in")}
          </button>
        </p>
      </form>
    </div>
  );
}
