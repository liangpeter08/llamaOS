"use client";
import { useEffect, useState } from "react";
import { get, post } from "@/lib/api";
import { Notice } from "./ui";
import { isOwner, useSession } from "./session";
import { useT } from "@/lib/i18n";

interface Msg {
  id: number;
  role: "user" | "assistant";
  content: string;
  model: string;
}

function newThread(prefix: string) {
  const rand = Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("");
  return `${prefix}:${rand}`;
}

/** prefix: "strategy:<hash>" or "trade:<id>". Each conversation is a fresh Kimi thread. */
export function Chat({ prefix, intro }: { prefix: string; intro: string }) {
  const { me } = useSession();
  const [thread, setThread] = useState(() => newThread(prefix));
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const t = useT();

  useEffect(() => {
    get<{ messages: Msg[] }>(`/api/chats/${thread}`).then((r) => setMsgs(r.messages)).catch(() => {});
  }, [thread]);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ messages: Msg[] }>("/api/chat", { thread, message: text });
      setMsgs(r.messages);
      setText("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="chat">
        {msgs.length === 0 && <p className="muted">{intro}</p>}
        {msgs.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <div className="who">{m.role === "user" ? t("you") : m.model}</div>
            {m.content}
          </div>
        ))}
      </div>
      {error && <Notice kind="bad">{error}</Notice>}
      {isOwner(me) && (
        <form onSubmit={ask}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={t("Does this make sense? What breaks it?")} />
          <div className="row" style={{ marginTop: 6 }}>
            <button className="primary" disabled={busy}>
              {t(busy ? "Thinking…" : "Ask")}
            </button>
            <button type="button" onClick={() => { setThread(newThread(prefix)); setMsgs([]); }}>
              {t("New conversation")}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
