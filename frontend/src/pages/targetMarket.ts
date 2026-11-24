export const REGIONS = [
  ["uk", "United Kingdom"],
  ["dach", "DACH"],
  ["nordics", "Nordics"],
  ["poland", "Poland"],
  ["europe", "Rest of Europe"],
  ["international", "International"],
];

const countries = new Intl.DisplayNames(["en"], { type: "region" });

export function countryName(code: string | null | undefined) {
  return code && /^[A-Z]{2}$/.test(code) ? countries.of(code) ?? code : code;
}

export function regionName(key: string) {
  return REGIONS.find(([region]) => region === key)?.[1] ?? "";
}

export function staffCount(count: number | null, estimate?: boolean) {
  return count == null ? "Headcount unknown" : `${estimate ? "Approx. " : ""}${count.toLocaleString()} employees`;
}
