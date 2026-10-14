"use client";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { usePoll } from "@/lib/hooks";
import { short } from "@/lib/format";
import { Loading } from "@/components/ui";
import { useT } from "@/lib/i18n";

function Diff() {
  const { hash } = useParams<{ hash: string }>();
  const against = useSearchParams().get("against") ?? "";
  const t = useT();
  const { data, error } = usePoll<{ diff: string }>(`/api/strategies/${hash}/diff?against=${encodeURIComponent(against)}`);
  return (
    <>
      <h1>{t("Diff")}</h1>
      <p className="muted"><span className="mono">{short(against)}</span> → <Link className="mono" href={`/strategies/${hash}`}>{short(hash)}</Link></p>
      {data ? <pre>{data.diff}</pre> : <Loading error={error} />}
    </>
  );
}

export default function DiffPage() {
  return <Suspense><Diff /></Suspense>;
}
