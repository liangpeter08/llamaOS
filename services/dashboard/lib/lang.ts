// Shared by the server root layout and the client i18n provider (no "use client" here on purpose).
export type Lang = "en" | "zh";
export const LANGS: Lang[] = ["en", "zh"];
export const LANG_COOKIE = "lang";
