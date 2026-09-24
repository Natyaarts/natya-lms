export interface Country {
  name: string;
  iso2: string; // ISO 3166-1 alpha-2
  dialCode: string; // e.g. "+91"
}

// A curated, alphabetically-sorted set of countries covering this app's
// realistic user base (India first-and-foremost, plus the Indian diaspora's
// major destinations and neighboring South Asian countries). Not the full
// ISO-3166 list of ~195 countries -- if a country is missing, it can be
// appended here without touching any other file.
export const COUNTRIES: Country[] = [
  { name: 'Afghanistan', iso2: 'AF', dialCode: '+93' },
  { name: 'Australia', iso2: 'AU', dialCode: '+61' },
  { name: 'Austria', iso2: 'AT', dialCode: '+43' },
  { name: 'Bahrain', iso2: 'BH', dialCode: '+973' },
  { name: 'Bangladesh', iso2: 'BD', dialCode: '+880' },
  { name: 'Belgium', iso2: 'BE', dialCode: '+32' },
  { name: 'Bhutan', iso2: 'BT', dialCode: '+975' },
  { name: 'Brazil', iso2: 'BR', dialCode: '+55' },
  { name: 'Canada', iso2: 'CA', dialCode: '+1' },
  { name: 'China', iso2: 'CN', dialCode: '+86' },
  { name: 'Denmark', iso2: 'DK', dialCode: '+45' },
  { name: 'Egypt', iso2: 'EG', dialCode: '+20' },
  { name: 'Fiji', iso2: 'FJ', dialCode: '+679' },
  { name: 'Finland', iso2: 'FI', dialCode: '+358' },
  { name: 'France', iso2: 'FR', dialCode: '+33' },
  { name: 'Germany', iso2: 'DE', dialCode: '+49' },
  { name: 'Ghana', iso2: 'GH', dialCode: '+233' },
  { name: 'Greece', iso2: 'GR', dialCode: '+30' },
  { name: 'Hong Kong', iso2: 'HK', dialCode: '+852' },
  { name: 'India', iso2: 'IN', dialCode: '+91' },
  { name: 'Indonesia', iso2: 'ID', dialCode: '+62' },
  { name: 'Ireland', iso2: 'IE', dialCode: '+353' },
  { name: 'Israel', iso2: 'IL', dialCode: '+972' },
  { name: 'Italy', iso2: 'IT', dialCode: '+39' },
  { name: 'Japan', iso2: 'JP', dialCode: '+81' },
  { name: 'Jordan', iso2: 'JO', dialCode: '+962' },
  { name: 'Kenya', iso2: 'KE', dialCode: '+254' },
  { name: 'Kuwait', iso2: 'KW', dialCode: '+965' },
  { name: 'Malaysia', iso2: 'MY', dialCode: '+60' },
  { name: 'Maldives', iso2: 'MV', dialCode: '+960' },
  { name: 'Mauritius', iso2: 'MU', dialCode: '+230' },
  { name: 'Mexico', iso2: 'MX', dialCode: '+52' },
  { name: 'Myanmar', iso2: 'MM', dialCode: '+95' },
  { name: 'Nepal', iso2: 'NP', dialCode: '+977' },
  { name: 'Netherlands', iso2: 'NL', dialCode: '+31' },
  { name: 'New Zealand', iso2: 'NZ', dialCode: '+64' },
  { name: 'Nigeria', iso2: 'NG', dialCode: '+234' },
  { name: 'Norway', iso2: 'NO', dialCode: '+47' },
  { name: 'Oman', iso2: 'OM', dialCode: '+968' },
  { name: 'Pakistan', iso2: 'PK', dialCode: '+92' },
  { name: 'Philippines', iso2: 'PH', dialCode: '+63' },
  { name: 'Poland', iso2: 'PL', dialCode: '+48' },
  { name: 'Portugal', iso2: 'PT', dialCode: '+351' },
  { name: 'Qatar', iso2: 'QA', dialCode: '+974' },
  { name: 'Russia', iso2: 'RU', dialCode: '+7' },
  { name: 'Saudi Arabia', iso2: 'SA', dialCode: '+966' },
  { name: 'Singapore', iso2: 'SG', dialCode: '+65' },
  { name: 'South Africa', iso2: 'ZA', dialCode: '+27' },
  { name: 'South Korea', iso2: 'KR', dialCode: '+82' },
  { name: 'Spain', iso2: 'ES', dialCode: '+34' },
  { name: 'Sri Lanka', iso2: 'LK', dialCode: '+94' },
  { name: 'Sweden', iso2: 'SE', dialCode: '+46' },
  { name: 'Switzerland', iso2: 'CH', dialCode: '+41' },
  { name: 'Thailand', iso2: 'TH', dialCode: '+66' },
  { name: 'Trinidad and Tobago', iso2: 'TT', dialCode: '+1' },
  { name: 'Turkey', iso2: 'TR', dialCode: '+90' },
  { name: 'United Arab Emirates', iso2: 'AE', dialCode: '+971' },
  { name: 'United Kingdom', iso2: 'GB', dialCode: '+44' },
  { name: 'United States', iso2: 'US', dialCode: '+1' },
  { name: 'Vietnam', iso2: 'VN', dialCode: '+84' },
  { name: 'Zimbabwe', iso2: 'ZW', dialCode: '+263' },
];

export const DEFAULT_COUNTRY: Country = COUNTRIES.find((c) => c.iso2 === 'IN')!;

// Derives a flag emoji purely from the ISO 3166-1 alpha-2 code (Unicode
// Regional Indicator Symbols) -- no flag image assets/dependency needed.
export function flagEmoji(iso2: string): string {
  return iso2
    .toUpperCase()
    .replace(/./g, (char) => String.fromCodePoint(127397 + char.charCodeAt(0)));
}
