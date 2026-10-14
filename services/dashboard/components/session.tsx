"use client";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { get, setCsrf } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Me, Status } from "@/lib/types";

interface SessionCtx {
  me: Me;
  status: Status | null;
  refresh: () => void;
}

const Ctx = createContext<SessionCtx | null>(null);

export function useSession(): SessionCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useSession outside SessionProvider");
  return c;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const t = useT();

  useEffect(() => {
    get<Me>("/api/me")
      .then((m) => {
        setCsrf(m.csrf);
        setMe(m);
      })
      .catch(() => {
        location.href = "/login";
      });
  }, []);

  const refresh = useCallback(() => {
    get<Status>("/api/status").then(setStatus).catch(() => {});
  }, []);

  useEffect(() => {
    if (!me) return;
    refresh();
    const id = setInterval(() => !document.hidden && refresh(), 2000);
    return () => clearInterval(id);
  }, [me, refresh]);

  if (!me) return <p className="muted" style={{ padding: 24 }}>{t("Loading…")}</p>;
  return <Ctx.Provider value={{ me, status, refresh }}>{children}</Ctx.Provider>;
}

export const isOwner = (me: Me) => me.role === "owner";
