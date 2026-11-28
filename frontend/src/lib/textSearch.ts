// Shared matching rules for the admin search boxes (course picker, teacher/mentor pickers).

// Lower-cases, drops accents and turns punctuation into spaces, so "(CM DIPLOMA)",
// "Unit-2", "+91 70120" and plain words all compare the same way. Letters, digits and
// combining marks are kept, so text in Malayalam script still matches as typed.
export function normalizeSearchText(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}]+/gu, " ")
    .trim();
}

// The typed words, normalized; an empty list means "no search".
export function searchWords(query: string): string[] {
  return normalizeSearchText(query).split(" ").filter(Boolean);
}

// True when every word appears somewhere in the given fields, in any order.
// Spacing is ignored too, so "unit2" finds "UNIT 2" and "unit 2" finds "UNIT2".
export function matchesAllWords(words: string[], fields: (string | null | undefined)[]): boolean {
  if (words.length === 0) return true;
  const text = normalizeSearchText(fields.filter(Boolean).join(" "));
  const haystack = `${text} ${text.replace(/ /g, "")}`;
  return words.every((w) => haystack.includes(w));
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

// A–Z with natural number order ("Unit 2" before "Unit 10"), ignoring case.
export function compareText(a: string, b: string): number {
  return collator.compare(a, b);
}
