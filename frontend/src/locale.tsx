import { useSyncExternalStore } from "react";
import polish from "./locales/pl.json";

export type Language = "en" | "pl";

const storageKey = "ri_language";
const messages: Record<string, string> = polish;
const subscribers = new Set<() => void>();

function initialLanguage(): Language {
  try {
    const stored = localStorage.getItem(storageKey);
    if (stored === "en" || stored === "pl") return stored;
  } catch { /* storage can be disabled */ }
  return navigator.language.toLowerCase().startsWith("pl") ? "pl" : "en";
}

let language = initialLanguage();

function applyDocumentLanguage() {
  document.documentElement.lang = language;
  document.title = tr("Revenue Intelligence");
}

export function getLanguage() {
  return language;
}

function subscribe(listener: () => void) {
  subscribers.add(listener);
  return () => { subscribers.delete(listener); };
}

export function useLanguage() {
  return useSyncExternalStore(subscribe, getLanguage);
}

export function tr(message: string, ...values: unknown[]): string {
  const translated = language === "pl" && Object.prototype.hasOwnProperty.call(messages, message) ? messages[message] : message;
  if (!values.length) return translated;
  let index = 0;
  return translated.replace(/\{(\d*)\}/g, (_, position: string) => {
    const value = values[position ? Number(position) : index++];
    return value == null ? "" : String(value);
  });
}

export function number(value: number) {
  return value.toLocaleString(language === "pl" ? "pl-PL" : "en-GB");
}

const pluralRules = { en: new Intl.PluralRules("en"), pl: new Intl.PluralRules("pl") };

export function counted(value: number, english: [string, string], polish: [string, string, string]) {
  const form = pluralRules[language].select(value);
  const noun = language === "pl" ? polish[form === "one" ? 0 : form === "few" ? 1 : 2] : english[form === "one" ? 0 : 1];
  return `${number(value)} ${noun}`;
}

export function LanguageSwitcher() {
  const current = useLanguage();
  function choose(next: Language) {
    if (current === next) return;
    language = next;
    try { localStorage.setItem(storageKey, next); } catch { /* still works for this visit */ }
    applyDocumentLanguage();
    subscribers.forEach((listener) => listener());
  }

  return (
    <div className="language-switch" role="group" aria-label={tr("Language")}>
      <button type="button" aria-pressed={current === "pl"} lang="pl" onClick={() => choose("pl")}>Polski</button>
      <button type="button" aria-pressed={current === "en"} lang="en" onClick={() => choose("en")}>English</button>
    </div>
  );
}

applyDocumentLanguage();
