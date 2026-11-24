export const SITE_ORIGIN = import.meta.env.VITE_SITE_ORIGIN || "https://mutating.tech";

export function siteURL(language: "ru" | "en", path: string) {
  return `${SITE_ORIGIN}${language === "ru" ? "/ru" : ""}${path}`;
}
