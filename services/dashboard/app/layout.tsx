import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "llamaOS", template: "%s · llamaOS" },
  icons: { icon: "/logo.svg" },
  description: "News-driven day trading: monitoring, approvals, strategies and Idea Lab",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
