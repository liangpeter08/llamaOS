"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { post } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { num, pct, short } from "@/lib/format";
import { Card, Empty, Loading, Notice, Pill, Stat, StatusPill } from "@/components/ui";
import { isOwner, useSession } from "@/components/session";
import { useT } from "@/lib/i18n";

export default function Ideas() {
  const { data, error, reload } = usePoll<any>("/api/ideas");
  const { me } = useSession();
  const router = useRouter();
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const t = useT();
  if (!data) return <Loading error={error} />;
  const { study, min_events } = data;

  async function act(path: string, body: object = {}) {
    setBusy(true);
    try {
      const r = await post(path, body);
      setMsg({ kind: "ok", text: r.message });
      if (r.hash) router.push(`/strategies/${r.hash}`);
    } catch (e) {
      setMsg({ kind: "bad", text: (e as Error).message });
    } finally {
      setBusy(false);
      reload();
    }
  }

  return (
    <>
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <div className="row between">
        <div>
          <h1>{t("Idea Lab")}</h1>
          <p className="muted">{t("Code measures event reactions; Kimi (or the offline rules generator) proposes hypotheses; you curate. Every hypothesis is counted, so discount lucky finds.")}</p>
        </div>
        <Card>
          <Stat label={t("Hypotheses")} value={t("tested {n}, {s} survived", { n: data.counter.tested, s: data.counter.survived })} sub={t("survived = enough events and passed its one holdout score")} />
        </Card>
      </div>
      {isOwner(me) && (
        <Card className="mt">
          <form className="row" onSubmit={(e) => { e.preventDefault(); act("/api/ideas/run", { focus }); }}>
            <input placeholder={t("Look for patterns in… (optional)")} value={focus} onChange={(e) => setFocus(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
            <button className="primary" disabled={busy}>{t(busy ? "Running…" : "Run discovery")}</button>
            <span className="muted">{t("Holdout: {n} most recent sessions, scored once per idea.", { n: study.holdout_days.length })}</span>
          </form>
        </Card>
      )}

      <Card className="mt" title={<>{t("Event study")} <span className="muted">({t("{n} discovery sessions; \"signed\" = return in the direction of the news", { n: study.discovery_days.length })})</span></>}>
        <div className="table-wrap"><table>
          <thead><tr><th>{t("Catalyst")}</th><th>{t("Direction")}</th><th className="num">n</th><th className="num">5m</th><th className="num">15m</th><th className="num">60m</th><th className="num">t (60m)</th><th className="num">{t("hit (60m)")}</th><th className="num">{t("to close")}</th><th className="num">{t("holdout n")}</th></tr></thead>
          <tbody>
            {study.groups.map((g: any) => (
              <tr key={`${g.catalyst}-${g.direction}`} className={g.n < min_events ? "dim" : ""}>
                <td>{t(g.catalyst)}</td><td>{t(g.direction)}</td>
                <td className="num">{g.n}{g.n < min_events && <> <span className="tag">{t("anecdotal")}</span></>}</td>
                {["h5", "h15", "h60"].map((h) => <td key={h} className="num">{pct(g[h].signed_mean, 2, true)}</td>)}
                <td className="num">{num(g.h60.signed_t, 1)}</td><td className="num">{pct(g.h60.signed_hit, 0)}</td>
                <td className="num">{pct(g.hclose.signed_mean, 2, true)}</td><td className="num">{g.n_holdout}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </Card>

      <h2 style={{ marginTop: 18 }}>{t("Ideas")}</h2>
      {data.ideas.length === 0 && <Card><Empty>{t("No ideas yet. Run discovery.")}</Empty></Card>}
      {data.ideas.map((i: any) => (
        <IdeaCard key={i.id} i={i} minEvents={min_events} theses={data.theses} owner={isOwner(me)} busy={busy} act={act} />
      ))}
    </>
  );
}

function IdeaCard({ i, minEvents, theses, owner, busy, act }: any) {
  const [note, setNote] = useState("");
  const [thesis, setThesis] = useState(theses[0]?.id ?? "");
  const ho = i.holdout || {};
  const t = useT();
  return (
    <Card className="mt">
      <div className="row between">
        <h2 style={{ margin: 0 }}>#{i.id} {i.title}</h2>
        <div className="row">
          <StatusPill status={i.status} />
          {i.anecdotal ? <Pill kind="warn">{t("anecdotal")} (n={i.sample_size} &lt; {minEvents})</Pill> : <Pill>n={i.sample_size}</Pill>}
          <Pill kind={i.passed ? "good" : i.passed === false ? "bad" : ""}>{t("holdout:")} {ho.n ? `n=${ho.n}, ${pct(ho.signed_mean, 2, true)}` : t("no events")}</Pill>
          <span className="tag">{i.model}</span>
        </div>
      </div>
      <p>{i.hypothesis}</p>
      <dl className="kv">
        <dt>{t("Mechanism")}</dt><dd>{i.mechanism}</dd>
        <dt>{t("Falsified if")}</dt><dd>{i.falsify}</dd>
        <dt>{t("Events")}</dt><dd className="mono muted">{(i.event_ids || []).slice(0, 20).join(", ")}{(i.event_ids || []).length > 20 && " …"}</dd>
        <dt>{t("Theses")}</dt><dd>{JSON.stringify(i.thesis_ids || {})}</dd>
        {i.note && (<><dt>{t("Your note")}</dt><dd>{i.note}</dd></>)}
        {i.strategy_hash && (<><dt>{t("Strategy")}</dt><dd><Link href={`/strategies/${i.strategy_hash}`}>{t("draft")} {short(i.strategy_hash)}</Link></dd></>)}
      </dl>
      {owner && ["proposed", "accepted"].includes(i.status) && (
        <div className="row" style={{ marginTop: 8 }}>
          {i.status === "proposed" && (
            <>
              <input placeholder={t("note")} value={note} onChange={(e) => setNote(e.target.value)} />
              <button className="good" disabled={busy} onClick={() => act(`/api/ideas/${i.id}/accept`, { note })}>{t("Accept")}</button>
              <button disabled={busy} onClick={() => act(`/api/ideas/${i.id}/reject`, { note })}>{t("Reject")}</button>
            </>
          )}
          {i.status === "accepted" && (
            <button className="primary" disabled={busy || !!i.anecdotal} onClick={() => act(`/api/ideas/${i.id}/compile`)}>{t("Compile to draft strategy")}</button>
          )}
          {theses.length > 0 && (
            <>
              <select value={thesis} onChange={(e) => setThesis(e.target.value)}>
                {theses.map((th: any) => <option key={th.id} value={th.id}>#{th.id} {th.text.slice(0, 40)}</option>)}
              </select>
              <button disabled={busy} onClick={() => act(`/api/ideas/${i.id}/attach`, { thesis_id: Number(thesis) })}>{t("Attach thesis")}</button>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
