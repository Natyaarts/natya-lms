import { useState, useRef, useEffect } from "react";

const COUNTRIES = [
  { name: 'India', code: '+91', iso: 'IN' },
  { name: 'United States', code: '+1', iso: 'US' },
  { name: 'United Kingdom', code: '+44', iso: 'GB' },
  { name: 'Australia', code: '+61', iso: 'AU' },
  { name: 'United Arab Emirates', code: '+971', iso: 'AE' },
  { name: 'Canada', code: '+1', iso: 'CA' },
  { name: 'Singapore', code: '+65', iso: 'SG' },
  { name: 'Malaysia', code: '+60', iso: 'MY' },
  { name: 'New Zealand', code: '+64', iso: 'NZ' },
  { name: 'South Africa', code: '+27', iso: 'ZA' },
  { name: 'Germany', code: '+49', iso: 'DE' },
  { name: 'France', code: '+33', iso: 'FR' },
  { name: 'Italy', code: '+39', iso: 'IT' },
  { name: 'Spain', code: '+34', iso: 'ES' },
  { name: 'Netherlands', code: '+31', iso: 'NL' },
  { name: 'Saudi Arabia', code: '+966', iso: 'SA' },
  { name: 'Qatar', code: '+974', iso: 'QA' },
  { name: 'Oman', code: '+968', iso: 'OM' },
  { name: 'Kuwait', code: '+965', iso: 'KW' },
  { name: 'Bahrain', code: '+973', iso: 'BH' },
];

interface CountrySelectProps {
  value: string;
  onChange: (value: string) => void;
}

export default function CountrySelect({ value, onChange }: CountrySelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);

  const filteredCountries = COUNTRIES.filter(c => 
    c.name.toLowerCase().includes(search.toLowerCase()) || 
    c.code.includes(search)
  );

  const selectedCountry = COUNTRIES.find(c => c.code === value) || COUNTRIES[0];

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="h-full px-4 py-3 bg-zinc-900 border border-zinc-800 rounded-xl text-white focus:outline-none focus:border-zinc-500 transition-colors flex items-center justify-between gap-3 min-w-[110px]"
      >
        <span className="font-medium text-sm">{selectedCountry.iso} <span className="text-zinc-400">{selectedCountry.code}</span></span>
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`opacity-50 transition-transform ${isOpen ? 'rotate-180' : ''}`}><path d="m6 9 6 6 6-6"/></svg>
      </button>

      {isOpen && (
        <div className="absolute top-[calc(100%+8px)] left-0 w-[260px] bg-[#0f0f0f] border border-zinc-800 rounded-xl shadow-[0_10px_40px_rgba(0,0,0,0.5)] z-50 overflow-hidden flex flex-col">
          <div className="p-3 border-b border-zinc-800/50 bg-[#151515]">
            <input 
              type="text" 
              placeholder="Search country..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-zinc-900/50 text-white px-4 py-2.5 rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-[#facc15]/50 border border-zinc-800 transition-all placeholder:text-zinc-600"
              autoFocus
            />
          </div>
          <div className="max-h-[240px] overflow-y-auto custom-scrollbar bg-[#0f0f0f]">
            {filteredCountries.map((country, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => {
                  onChange(country.code);
                  setIsOpen(false);
                  setSearch("");
                }}
                className="w-full text-left px-4 py-3 text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors flex items-center justify-between group"
              >
                <span className="truncate pr-4">{country.name} <span className="text-zinc-500 group-hover:text-zinc-400">({country.iso})</span></span>
                <span className="text-zinc-500 font-medium whitespace-nowrap group-hover:text-zinc-300">{country.code}</span>
              </button>
            ))}
            {filteredCountries.length === 0 && (
              <div className="px-4 py-8 text-sm text-zinc-500 text-center">No countries found</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
