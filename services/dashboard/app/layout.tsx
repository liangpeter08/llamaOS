import type { Metadata } from "next";
import type { ReactNode } from "react";
import { cookies, headers } from "next/headers";
import { I18nProvider } from "@/lib/i18n";
import { LANG_COOKIE, type Lang } from "@/lib/lang";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "llamaOS", template: "%s · llamaOS" },
  icons: { icon: "/logo.svg" },
  description: "News-driven day trading: monitoring, approvals, strategies and Idea Lab",
};

// Saved choice wins; otherwise follow the browser's language.
async function initialLang(): Promise<Lang> {
  const saved = (await cookies()).get(LANG_COOKIE)?.value;
  if (saved === "en" || saved === "zh") return saved;
  return /^zh/i.test((await headers()).get("accept-language") ?? "") ? "zh" : "en";
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const lang = await initialLang();
  return (
    <html lang={lang === "zh" ? "zh-CN" : "en"}>
      <body>
        <I18nProvider initial={lang}>{children}</I18nProvider>
      </body>
    </html>
  );
}
