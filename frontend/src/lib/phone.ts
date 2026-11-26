/**
 * Country dial codes and phone helpers shared by the web login, register and admin
 * forms. Same country list as the mobile app (mobile/src/data/countries.ts).
 * The backend stores and validates numbers in E.164 (users/phone.py); these helpers
 * only build the "+<code><number>" string a user typed and split a stored one back.
 */
export interface DialCountry {
  name: string;
  iso: string;
  code: string; // e.g. "+91"
}

export const COUNTRIES: DialCountry[] = [
  { name: "Afghanistan", iso: "AF", code: "+93" },
  { name: "Australia", iso: "AU", code: "+61" },
  { name: "Austria", iso: "AT", code: "+43" },
  { name: "Bahrain", iso: "BH", code: "+973" },
  { name: "Bangladesh", iso: "BD", code: "+880" },
  { name: "Belgium", iso: "BE", code: "+32" },
  { name: "Bhutan", iso: "BT", code: "+975" },
  { name: "Brazil", iso: "BR", code: "+55" },
  { name: "Canada", iso: "CA", code: "+1" },
  { name: "China", iso: "CN", code: "+86" },
  { name: "Denmark", iso: "DK", code: "+45" },
  { name: "Egypt", iso: "EG", code: "+20" },
  { name: "Fiji", iso: "FJ", code: "+679" },
  { name: "Finland", iso: "FI", code: "+358" },
  { name: "France", iso: "FR", code: "+33" },
  { name: "Germany", iso: "DE", code: "+49" },
  { name: "Ghana", iso: "GH", code: "+233" },
  { name: "Greece", iso: "GR", code: "+30" },
  { name: "Hong Kong", iso: "HK", code: "+852" },
  { name: "India", iso: "IN", code: "+91" },
  { name: "Indonesia", iso: "ID", code: "+62" },
  { name: "Ireland", iso: "IE", code: "+353" },
  { name: "Israel", iso: "IL", code: "+972" },
  { name: "Italy", iso: "IT", code: "+39" },
  { name: "Japan", iso: "JP", code: "+81" },
  { name: "Jordan", iso: "JO", code: "+962" },
  { name: "Kenya", iso: "KE", code: "+254" },
  { name: "Kuwait", iso: "KW", code: "+965" },
  { name: "Malaysia", iso: "MY", code: "+60" },
  { name: "Maldives", iso: "MV", code: "+960" },
  { name: "Mauritius", iso: "MU", code: "+230" },
  { name: "Mexico", iso: "MX", code: "+52" },
  { name: "Myanmar", iso: "MM", code: "+95" },
  { name: "Nepal", iso: "NP", code: "+977" },
  { name: "Netherlands", iso: "NL", code: "+31" },
  { name: "New Zealand", iso: "NZ", code: "+64" },
  { name: "Nigeria", iso: "NG", code: "+234" },
  { name: "Norway", iso: "NO", code: "+47" },
  { name: "Oman", iso: "OM", code: "+968" },
  { name: "Pakistan", iso: "PK", code: "+92" },
  { name: "Philippines", iso: "PH", code: "+63" },
  { name: "Poland", iso: "PL", code: "+48" },
  { name: "Portugal", iso: "PT", code: "+351" },
  { name: "Qatar", iso: "QA", code: "+974" },
  { name: "Russia", iso: "RU", code: "+7" },
  { name: "Saudi Arabia", iso: "SA", code: "+966" },
  { name: "Singapore", iso: "SG", code: "+65" },
  { name: "South Africa", iso: "ZA", code: "+27" },
  { name: "South Korea", iso: "KR", code: "+82" },
  { name: "Spain", iso: "ES", code: "+34" },
  { name: "Sri Lanka", iso: "LK", code: "+94" },
  { name: "Sweden", iso: "SE", code: "+46" },
  { name: "Switzerland", iso: "CH", code: "+41" },
  { name: "Thailand", iso: "TH", code: "+66" },
  { name: "Trinidad and Tobago", iso: "TT", code: "+1" },
  { name: "Turkey", iso: "TR", code: "+90" },
  { name: "United Arab Emirates", iso: "AE", code: "+971" },
  { name: "United Kingdom", iso: "GB", code: "+44" },
  { name: "United States", iso: "US", code: "+1" },
  { name: "Vietnam", iso: "VN", code: "+84" },
  { name: "Zimbabwe", iso: "ZW", code: "+263" },
];

export const DEFAULT_DIAL_CODE = "+91";

/** India first (the default), then the rest alphabetically. */
export const COUNTRY_OPTIONS: DialCountry[] = [
  ...COUNTRIES.filter((c) => c.iso === "IN"),
  ...COUNTRIES.filter((c) => c.iso !== "IN"),
];

/**
 * "+<code><digits>" from a dial code and what the user typed. Drops spaces/dashes,
 * a national trunk "0" (Irish "087..." -> "87...") and a dial code pasted again
 * ("+91 9876..." or "919876..." while +91 is selected). A full number typed with
 * its own "+" is kept as typed (minus formatting). Returns "" for empty input.
 */
export function joinPhone(dialCode: string, typed: string): string {
  const raw = (typed || "").trim();
  if (!raw) return "";
  if (raw.startsWith("+")) return "+" + raw.replace(/[^0-9]/g, "");
  let digits = raw.replace(/[^0-9]/g, "");
  if (digits.startsWith("00")) return "+" + digits.slice(2);
  const codeDigits = dialCode.replace(/[^0-9]/g, "");
  if (codeDigits && digits.startsWith(codeDigits) && digits.length > codeDigits.length + 5) {
    digits = digits.slice(codeDigits.length);
  }
  digits = digits.replace(/^0+/, "");
  return digits ? `${dialCode}${digits}` : "";
}

/**
 * Splits a stored number into the dial code (longest matching country code) and the
 * rest, for editing. Numbers without "+" (legacy data) are returned as-is with the
 * default dial code so nothing is lost or guessed.
 */
export function splitPhone(stored: string | null | undefined): { code: string; national: string } {
  const value = (stored || "").trim();
  if (!value.startsWith("+")) return { code: DEFAULT_DIAL_CODE, national: value };
  const digits = value.replace(/[^0-9]/g, "");
  const match = [...COUNTRIES]
    .sort((a, b) => b.code.length - a.code.length)
    .find((c) => digits.startsWith(c.code.slice(1)));
  if (!match) return { code: DEFAULT_DIAL_CODE, national: value };
  return { code: match.code, national: digits.slice(match.code.length - 1) };
}
