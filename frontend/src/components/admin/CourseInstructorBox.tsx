"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Search, X, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { compareText, matchesAllWords, searchWords } from "@/lib/textSearch";

export type InstructorGroup = "TEACHER" | "MENTOR";
export type CandidatesStatus = "idle" | "loading" | "ready" | "error";

// A row from GET /api/courses/<id>/instructors/ (CourseInstructorSerializer).
export interface CourseInstructorRow {
  id: number;
  user: number;
  user_name: string;
  user_email?: string | null;
  user_phone?: string | null;
  is_active_user?: boolean;
  role: string;
  is_primary?: boolean;
}

// A teacher or mentor account from GET /api/users/admin-users/?role=teachers|mentors.
export interface StaffCandidate {
  id: number;
  username: string;
  first_name?: string | null;
  last_name?: string | null;
  email?: string | null;
  phone_number?: string | null;
  is_active?: boolean;
}

const GROUPS = {
  TEACHER: {
    title: "Teachers",
    noun: "teacher",
    // Assistants work alongside the teacher (same edit access), so they are listed here.
    rowRoles: ["TEACHER", "ASSISTANT"],
    addRoles: [
      { value: "TEACHER", label: "Teacher" },
      { value: "ASSISTANT", label: "Assistant" },
    ],
    accent: "bg-blue-500/10 text-blue-400",
    count: "bg-blue-500/15 text-blue-300 border-blue-500/30",
  },
  MENTOR: {
    title: "Mentors",
    noun: "mentor",
    rowRoles: ["MENTOR"],
    addRoles: [{ value: "MENTOR", label: "Mentor" }],
    accent: "bg-purple-500/10 text-purple-400",
    count: "bg-purple-500/15 text-purple-300 border-purple-500/30",
  },
} as const;

export function personName(u: StaffCandidate): string {
  return `${u.first_name || ""} ${u.last_name || ""}`.trim() || u.username;
}

// People matching every typed word in their name, username, email or phone
// (any order; case, spacing and punctuation ignored). Active accounts first, then A–Z.
export function filterPeople<T extends StaffCandidate>(people: T[], query: string): T[] {
  const words = searchWords(query);
  return people
    .filter((u) => matchesAllWords(words, [personName(u), u.username, u.email, u.phone_number]))
    .sort(
      (a, b) =>
        Number(a.is_active === false) - Number(b.is_active === false) ||
        compareText(personName(a), personName(b)),
    );
}

interface Props {
  group: InstructorGroup;
  instructors: CourseInstructorRow[];
  loading: boolean;
  canManage: boolean;
  candidates: StaffCandidate[];
  candidatesStatus: CandidatesStatus;
  onLoadCandidates: () => void;
  // Resolves to an error message to show, or null when the person was added.
  onAdd: (userId: number, role: string, isPrimary: boolean) => Promise<string | null>;
  onRemove: (row: CourseInstructorRow) => void;
  removingId: number | null;
}

export default function CourseInstructorBox({
  group,
  instructors,
  loading,
  canManage,
  candidates,
  candidatesStatus,
  onLoadCandidates,
  onAdd,
  onRemove,
  removingId,
}: Props) {
  const cfg = GROUPS[group];
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [addRole, setAddRole] = useState<string>(cfg.addRoles[0].value);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [isPrimary, setIsPrimary] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const rows = useMemo(
    () => instructors.filter((r) => (cfg.rowRoles as readonly string[]).includes(r.role)),
    [instructors, cfg],
  );
  // People who already hold the role being added (the server rejects a second copy).
  const takenIds = useMemo(
    () => new Set(instructors.filter((r) => r.role === addRole).map((r) => Number(r.user))),
    [instructors, addRole],
  );
  const results = useMemo(() => filterPeople(candidates, query), [candidates, query]);
  const selected = candidates.find((c) => c.id === selectedId) ?? null;
  const active = Math.min(activeIndex, results.length - 1);
  const addLabel = cfg.addRoles.find((r) => r.value === addRole)?.label ?? cfg.title;

  useEffect(() => {
    if (adding) inputRef.current?.focus();
  }, [adding]);

  const openPanel = () => {
    setAdding(true);
    setError("");
    if (candidatesStatus === "idle" || candidatesStatus === "error") onLoadCandidates();
  };

  const resetPanel = () => {
    setAdding(false);
    setQuery("");
    setSelectedId(null);
    setIsPrimary(false);
    setAddRole(cfg.addRoles[0].value);
    setActiveIndex(0);
    setError("");
  };

  const closePanel = () => {
    if (!saving) resetPanel();
  };

  const pick = (u: StaffCandidate, index: number) => {
    setActiveIndex(index);
    if (!takenIds.has(u.id)) setSelectedId(u.id);
  };

  const changeRole = (role: string) => {
    setAddRole(role);
    const nowTaken = instructors.some((r) => r.role === role && Number(r.user) === selectedId);
    if (nowTaken) setSelectedId(null);
  };

  const moveActive = (next: number) => {
    setActiveIndex(next);
    listRef.current?.querySelector(`[data-index="${next}"]`)?.scrollIntoView?.({ block: "nearest" });
  };

  const onSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      closePanel();
      return;
    }
    if (results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveActive(Math.min(active + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveActive(Math.max(active - 1, 0));
    } else if (e.key === "Enter") {
      // Enter only picks the highlighted person; adding always needs the button.
      e.preventDefault();
      if (active >= 0) pick(results[active], active);
    }
  };

  const submit = async () => {
    if (!selected || saving) return;
    setSaving(true);
    setError("");
    try {
      const message = await onAdd(selected.id, addRole, isPrimary);
      if (message) setError(message);
      else resetPanel();
    } finally {
      setSaving(false);
    }
  };

  const listId = `${group.toLowerCase()}-candidates`;

  return (
    <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6" data-testid={`instructor-box-${group.toLowerCase()}`}>
      <div className="flex items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-bold">{cfg.title}</h2>
          {!loading && (
            <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold border ${cfg.count}`}>{rows.length}</span>
          )}
        </div>
        {canManage && !adding && (
          <button
            type="button"
            onClick={openPanel}
            className="text-xs font-bold text-[#facc15] hover:text-yellow-400 transition-colors"
          >
            + Add {cfg.title.slice(0, -1)}
          </button>
        )}
      </div>

      {loading ? (
        <div className="text-xs text-zinc-500 py-4">Loading {cfg.title.toLowerCase()}...</div>
      ) : rows.length === 0 ? (
        !adding && (
          <div className="text-center py-6 text-zinc-500 text-xs border border-dashed border-white/10 rounded-xl">
            No {cfg.noun} assigned yet.
          </div>
        )
      ) : (
        <div className="space-y-2">
          {rows.map((inst) => (
            <div key={inst.id} className="flex items-center justify-between bg-zinc-950 border border-white/5 rounded-xl px-4 py-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-white text-sm truncate">{inst.user_name}</span>
                  {inst.is_primary && (
                    <span className="px-1.5 py-0.5 bg-[#facc15]/20 text-[#facc15] text-[9px] font-bold rounded uppercase tracking-wide">Primary</span>
                  )}
                  {inst.is_active_user === false && (
                    <span className="px-1.5 py-0.5 bg-red-500/20 text-red-400 text-[9px] font-bold rounded uppercase tracking-wide">Inactive</span>
                  )}
                </div>
                <div className="text-[11px] text-zinc-500 mt-0.5 truncate">
                  {inst.user_email || inst.user_phone || "No contact info"}
                </div>
                <span className={`inline-block mt-1.5 px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${
                  inst.role === "ASSISTANT" ? "bg-zinc-700/50 text-zinc-300" : cfg.accent
                }`}>
                  {inst.role}
                </span>
              </div>
              {canManage && (
                <button
                  type="button"
                  onClick={() => onRemove(inst)}
                  disabled={removingId === inst.id}
                  className="p-2 hover:bg-red-500/10 rounded-lg transition-colors text-zinc-500 hover:text-red-400 disabled:opacity-50 shrink-0"
                  title={`Remove ${cfg.noun}`}
                  aria-label={`Remove ${inst.user_name}`}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {adding && (
        <div className="mt-4 bg-black border border-white/10 rounded-xl p-4 space-y-3" role="group" aria-label={`Add ${cfg.noun}`}>
          {cfg.addRoles.length > 1 && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wide">Add as</span>
              <div className="inline-flex p-0.5 bg-zinc-900 border border-white/10 rounded-lg">
                {cfg.addRoles.map((r) => (
                  <button
                    key={r.value}
                    type="button"
                    onClick={() => changeRole(r.value)}
                    aria-pressed={addRole === r.value}
                    className={`px-2.5 py-1 rounded-md text-[11px] font-semibold transition-colors ${
                      addRole === r.value ? "bg-[#facc15] text-black" : "text-zinc-400 hover:text-white"
                    }`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="relative">
            <Search className="w-3.5 h-3.5 text-zinc-500 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              ref={inputRef}
              type="text"
              role="combobox"
              aria-label={`Search ${cfg.title.toLowerCase()}`}
              aria-expanded="true"
              aria-controls={listId}
              aria-activedescendant={active >= 0 ? `${listId}-${results[active].id}` : undefined}
              aria-autocomplete="list"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={onSearchKeyDown}
              placeholder={`Search ${cfg.title.toLowerCase()} by name, phone or email…`}
              autoComplete="off"
              spellCheck={false}
              className="w-full pl-9 pr-9 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm placeholder:text-zinc-600 focus:outline-none focus:border-[#facc15]"
            />
            {query && (
              <button
                type="button"
                onClick={() => {
                  setQuery("");
                  setActiveIndex(0);
                  inputRef.current?.focus();
                }}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-md text-zinc-500 hover:text-white"
                aria-label="Clear search"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          <div className="border border-white/10 rounded-xl bg-zinc-950/60 max-h-60 overflow-y-auto">
            {candidatesStatus !== "ready" && candidatesStatus !== "error" && (
              <div className="flex items-center justify-center gap-2 py-8 text-xs text-zinc-400">
                <Loader2 className="w-3.5 h-3.5 animate-spin text-[#facc15]" /> Loading {cfg.title.toLowerCase()}…
              </div>
            )}
            {candidatesStatus === "error" && (
              <div className="flex flex-col items-center gap-2 py-6 text-center">
                <AlertCircle className="w-4 h-4 text-red-400" />
                <p className="text-xs text-zinc-300">Couldn&apos;t load the {cfg.noun} list.</p>
                <button
                  type="button"
                  onClick={onLoadCandidates}
                  className="px-3 py-1 text-[11px] font-semibold rounded-lg bg-white/10 hover:bg-white/15 text-white"
                >
                  Try again
                </button>
              </div>
            )}
            {candidatesStatus === "ready" && results.length === 0 && (
              <p className="py-6 px-3 text-center text-xs text-zinc-400">
                {candidates.length === 0
                  ? `No ${cfg.noun} accounts yet. Create one under Users first.`
                  : <>No {cfg.noun} matches &ldquo;{query.trim()}&rdquo;.</>}
              </p>
            )}
            {candidatesStatus === "ready" && results.length > 0 && (
              <ul ref={listRef} id={listId} role="listbox" aria-label={cfg.title} className="p-1 space-y-0.5">
                {results.map((u, i) => {
                  const taken = takenIds.has(u.id);
                  const isSelected = u.id === selectedId;
                  const contact = u.phone_number || u.email || `@${u.username}`;
                  return (
                    <li
                      key={u.id}
                      id={`${listId}-${u.id}`}
                      data-index={i}
                      role="option"
                      aria-selected={isSelected}
                      aria-disabled={taken}
                      onClick={() => pick(u, i)}
                      onMouseEnter={() => setActiveIndex(i)}
                      className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg border transition-colors ${
                        taken ? "cursor-not-allowed opacity-60" : "cursor-pointer"
                      } ${
                        isSelected
                          ? "border-[#facc15]/60 bg-[#facc15]/10"
                          : i === active
                            ? "border-white/10 bg-white/5"
                            : "border-transparent"
                      }`}
                    >
                      <span
                        className={`w-3.5 h-3.5 rounded-full border-2 shrink-0 flex items-center justify-center ${
                          isSelected ? "border-[#facc15]" : "border-zinc-600"
                        }`}
                        aria-hidden="true"
                      >
                        {isSelected && <span className="w-1.5 h-1.5 rounded-full bg-[#facc15]" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold text-white truncate">{personName(u)}</span>
                        <span className="block text-[11px] text-zinc-500 truncate">{contact}</span>
                      </span>
                      {u.is_active === false && (
                        <span className="text-[9px] font-bold px-1.5 py-0.5 rounded uppercase bg-red-500/15 text-red-400 shrink-0">Inactive</span>
                      )}
                      {taken && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 shrink-0">
                          <CheckCircle2 className="w-3 h-3" /> Already {addLabel.toLowerCase()}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          {candidatesStatus === "ready" && candidates.length > 0 && (
            <p className="text-[10px] text-zinc-500" aria-live="polite">
              {query ? `Showing ${results.length} of ${candidates.length} ${cfg.title.toLowerCase()}` : `${candidates.length} ${cfg.title.toLowerCase()}`}
            </p>
          )}

          <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
            <input
              type="checkbox"
              checked={isPrimary}
              onChange={(e) => setIsPrimary(e.target.checked)}
              className="accent-[#facc15]"
            />
            Set as primary instructor
          </label>
          {error && <p className="text-xs text-red-400" role="alert">{error}</p>}
          <div className="flex items-center justify-between gap-2 pt-1">
            <span className="text-[11px] text-zinc-500 truncate min-w-0">
              {selected ? <>Selected: <span className="text-white font-semibold">{personName(selected)}</span></> : `No ${cfg.noun} selected`}
            </span>
            <div className="flex gap-2 shrink-0">
              <button
                type="button"
                onClick={closePanel}
                disabled={saving}
                className="px-3 py-1.5 text-xs text-zinc-400 hover:text-white disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={saving || !selected}
                className="inline-flex items-center gap-1.5 px-4 py-1.5 text-xs bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50"
              >
                {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {saving ? "Adding…" : `Add ${addLabel}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
