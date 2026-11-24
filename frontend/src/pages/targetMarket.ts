import { counted, getLanguage, tr } from "../locale";
export const REGIONS = [
  ["uk", "United Kingdom"],
  ["dach", "DACH"],
  ["nordics", "Nordics"],
  ["poland", "Poland"],
  ["europe", "Rest of Europe"],
  ["international", "International"],
];

const countries = {
  en: new Intl.DisplayNames("en", { type: "region" }),
  pl: new Intl.DisplayNames("pl", { type: "region" }),
};

export function countryName(code: string | null | undefined) {
  return code && /^[A-Z]{2}$/.test(code) ? countries[getLanguage()].of(code) ?? code : code;
}

export function regionName(key: string) {
  return tr(REGIONS.find(([region]) => region === key)?.[1] ?? "");
}

export function staffCount(count: number | null, estimate?: boolean) {
  if (count == null) return tr("Headcount unknown");
  const employees = counted(count, ["employee", "employees"], ["pracownik", "pracowników", "pracowników"]);
  return estimate ? tr("Approx. {}", employees) : employees;
}
