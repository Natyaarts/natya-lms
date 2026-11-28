"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { X, Search, Loader2, CheckCircle2, AlertCircle, Video, BookOpen } from "lucide-react";
import { compareText, matchesAllWords, searchWords } from "@/lib/textSearch";

// A course from GET /api/courses/ (CourseListSerializer).
export interface CatalogCourse {
  id: number;
  title: string;
  course_type?: string | null;
  is_published?: boolean | null;
}

// A row from GET /api/users/admin-users/<id>/courses/ (what the user already has).
export interface AssignedCourse {
  course_id: number;
  role?: string | null;
}

export type CourseTypeFilter = "ALL" | "LIVE" | "RECORDED";

export type CatalogStatus = "loading" | "ready" | "error";

// Courses whose title contains every typed word (in any order), optionally limited to
// one course type, sorted A–Z with natural number order ("Unit 2" before "Unit 10").
// Case, spacing and punctuation are ignored (see lib/textSearch).
export function filterCourses<T extends CatalogCourse>(
  courses: T[],
  query: string,
  type: CourseTypeFilter = "ALL",
): T[] {
  const words = searchWords(query);
  return courses
    .filter((c) => type === "ALL" || c.course_type === type)
    .filter((c) => matchesAllWords(words, [c.title]))
    .sort((a, b) => compareText(a.title || "", b.title || ""));
}

const TYPE_TABS: { value: CourseTypeFilter; label: string }[] = [
  { value: "ALL", label: "All" },
  { value: "LIVE", label: "Live" },
  { value: "RECORDED", label: "Recorded" },
];

interface Props {
  userName: string;
  courses: CatalogCourse[];
  status: CatalogStatus;
  onRetry: () => void;
  assigned: AssignedCourse[];
  assigning: boolean;
  onAssign: (courseId: number) => void;
  onClose: () => void;
}

export default function AssignCourseModal({
  userName,
  courses,
  status,
  onRetry,
  assigned,
  assigning,
  onAssign,
  onClose,
}: Props) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState<CourseTypeFilter>("ALL");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // Role per course the user already has ("Enrolled", "Batch Student", ...).
  const assignedRoles = useMemo(
    () => new Map(assigned.map((a) => [Number(a.course_id), a.role || "Assigned"])),
    [assigned],
  );
  const results = useMemo(() => filterCourses(courses, query, type), [courses, query, type]);
  const counts = useMemo(
    () => ({
      ALL: filterCourses(courses, query, "ALL").length,
      LIVE: filterCourses(courses, query, "LIVE").length,
      RECORDED: filterCourses(courses, query, "RECORDED").length,
    }),
    [courses, query],
  );
  const selected = courses.find((c) => c.id === selectedId) ?? null;
  const selectedRole = selected ? assignedRoles.get(selected.id) : undefined;
  const active = Math.min(activeIndex, results.length - 1);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !assigning) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, assigning]);

  const moveActive = (next: number) => {
    setActiveIndex(next);
    listRef.current
      ?.querySelector(`[data-index="${next}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  };

  const onSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveActive(Math.min(active + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveActive(Math.max(active - 1, 0));
    } else if (e.key === "Enter") {
      // Enter only picks the highlighted course; assigning always needs the button.
      e.preventDefault();
      if (active >= 0) setSelectedId(results[active].id);
    }
  };

  const changeQuery = (value: string) => {
    setQuery(value);
    setActiveIndex(0);
  };

  const close = () => {
    if (!assigning) onClose();
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex justify-center p-4 overflow-y-auto"
      onClick={close}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="assign-course-title"
        className="my-auto bg-zinc-900 border border-white/10 rounded-2xl w-full max-w-lg shadow-2xl flex flex-col max-h-[calc(100vh-2rem)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 p-6 pb-4">
          <div className="min-w-0">
            <h2 id="assign-course-title" className="text-xl font-bold">Assign Course</h2>
            <p className="text-zinc-400 text-xs mt-1">
              Give <span className="text-zinc-200 font-semibold">{userName}</span> direct access to a course.
            </p>
          </div>
          <button
            type="button"
            onClick={close}
            disabled={assigning}
            className="p-1.5 -mr-1.5 rounded-lg text-zinc-500 hover:text-white hover:bg-white/5 disabled:opacity-40 transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Search + type filter */}
        <div className="px-6 space-y-3">
          <div className="relative">
            <Search className="w-4 h-4 text-zinc-500 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              ref={inputRef}
              type="text"
              role="combobox"
              aria-label="Search courses"
              aria-expanded="true"
              aria-controls="assign-course-options"
              aria-activedescendant={active >= 0 ? `assign-course-option-${results[active].id}` : undefined}
              aria-autocomplete="list"
              value={query}
              onChange={(e) => changeQuery(e.target.value)}
              onKeyDown={onSearchKeyDown}
              placeholder="Search courses by name…"
              autoComplete="off"
              spellCheck={false}
              className="w-full bg-zinc-800 border border-white/10 rounded-xl py-2.5 pl-10 pr-10 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-[#facc15]"
            />
            {query && (
              <button
                type="button"
                onClick={() => {
                  changeQuery("");
                  inputRef.current?.focus();
                }}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-md text-zinc-500 hover:text-white hover:bg-white/5"
                aria-label="Clear search"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>

          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="inline-flex p-1 bg-zinc-800/70 border border-white/5 rounded-xl" role="group" aria-label="Course type">
              {TYPE_TABS.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => {
                    setType(t.value);
                    setActiveIndex(0);
                  }}
                  aria-pressed={type === t.value}
                  className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors ${
                    type === t.value ? "bg-[#facc15] text-black" : "text-zinc-400 hover:text-white"
                  }`}
                >
                  {t.label}
                  {status === "ready" && (
                    <span className={`ml-1.5 ${type === t.value ? "text-black/60" : "text-zinc-500"}`}>{counts[t.value]}</span>
                  )}
                </button>
              ))}
            </div>
            {status === "ready" && (
              <span className="text-[11px] text-zinc-500" aria-live="polite">
                {query || type !== "ALL"
                  ? `Showing ${results.length} of ${courses.length} courses`
                  : `${courses.length} courses`}
              </span>
            )}
          </div>
        </div>

        {/* Course list */}
        <div className="px-6 pt-3 pb-4 min-h-0 flex-1">
          <div className="border border-white/10 rounded-xl bg-black/20 overflow-y-auto max-h-[22rem] min-h-[10rem]">
            {status === "loading" && (
              <div className="flex items-center justify-center gap-2 py-12 text-sm text-zinc-400">
                <Loader2 className="w-4 h-4 animate-spin text-[#facc15]" /> Loading courses…
              </div>
            )}

            {status === "error" && (
              <div className="flex flex-col items-center gap-3 py-10 px-4 text-center">
                <AlertCircle className="w-5 h-5 text-red-400" />
                <p className="text-sm text-zinc-300">Couldn&apos;t load the course list.</p>
                <button
                  type="button"
                  onClick={onRetry}
                  className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-white/10 hover:bg-white/15 text-white transition-colors"
                >
                  Try again
                </button>
              </div>
            )}

            {status === "ready" && results.length === 0 && (
              <div className="flex flex-col items-center gap-2 py-10 px-4 text-center">
                <p className="text-sm text-zinc-300">
                  {courses.length === 0
                    ? "No courses in the catalog yet."
                    : query
                      ? <>No courses match &ldquo;{query.trim()}&rdquo;.</>
                      : "No courses of this type."}
                </p>
                {(query || type !== "ALL") && courses.length > 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      changeQuery("");
                      setType("ALL");
                      inputRef.current?.focus();
                    }}
                    className="text-xs font-semibold text-[#facc15] hover:text-yellow-400"
                  >
                    Clear search and filters
                  </button>
                )}
              </div>
            )}

            {status === "ready" && results.length > 0 && (
              <ul ref={listRef} id="assign-course-options" role="listbox" aria-label="Courses" className="p-1.5 space-y-1">
                {results.map((c, i) => {
                  const isSelected = c.id === selectedId;
                  const isActive = i === active;
                  const role = assignedRoles.get(c.id);
                  const isLive = c.course_type === "LIVE";
                  return (
                    <li
                      key={c.id}
                      id={`assign-course-option-${c.id}`}
                      data-index={i}
                      role="option"
                      aria-selected={isSelected}
                      onClick={() => {
                        setSelectedId(c.id);
                        setActiveIndex(i);
                      }}
                      onMouseEnter={() => setActiveIndex(i)}
                      className={`flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer border transition-colors ${
                        isSelected
                          ? "border-[#facc15]/60 bg-[#facc15]/10"
                          : isActive
                            ? "border-white/10 bg-white/5"
                            : "border-transparent"
                      }`}
                    >
                      <span
                        className={`w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center ${
                          isSelected ? "border-[#facc15]" : "border-zinc-600"
                        }`}
                        aria-hidden="true"
                      >
                        {isSelected && <span className="w-2 h-2 rounded-full bg-[#facc15]" />}
                      </span>
                      <span
                        className={`w-8 h-8 rounded-lg shrink-0 flex items-center justify-center ${
                          isLive ? "bg-rose-500/10 text-rose-300" : "bg-sky-500/10 text-sky-300"
                        }`}
                        aria-hidden="true"
                      >
                        {isLive ? <Video className="w-4 h-4" /> : <BookOpen className="w-4 h-4" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold text-white truncate" title={c.title}>
                          {c.title}
                        </span>
                        <span className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                          <span className={`text-[10px] font-semibold ${isLive ? "text-rose-300" : "text-sky-300"}`}>
                            {isLive ? "Live" : c.course_type === "RECORDED" ? "Recorded" : "Course"}
                          </span>
                          {c.is_published === false && (
                            <span className="text-[10px] font-semibold px-1.5 py-px rounded bg-zinc-700/60 text-zinc-300">Draft</span>
                          )}
                          {role && (
                            <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-px rounded bg-emerald-500/10 text-emerald-300">
                              <CheckCircle2 className="w-3 h-3" /> Already assigned · {role}
                            </span>
                          )}
                        </span>
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          {status === "ready" && courses.length > 0 && (
            <p className="text-[11px] text-zinc-500 mt-2">Tip: use ↑ ↓ to move and Enter to select.</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-6 py-4 border-t border-white/5 flex-wrap">
          <div className="min-w-0 flex-1 text-xs" aria-live="polite">
            {selected ? (
              <>
                <div className="text-zinc-500">Selected</div>
                <div className="text-white font-semibold truncate" title={selected.title}>{selected.title}</div>
                {selectedRole && (
                  <div className="text-emerald-300/80 mt-0.5">
                    Already assigned ({selectedRole}). Assigning again won&apos;t create a duplicate.
                  </div>
                )}
              </>
            ) : (
              <span className="text-zinc-500">No course selected</span>
            )}
          </div>
          <div className="flex gap-3 shrink-0">
            <button
              type="button"
              onClick={close}
              disabled={assigning}
              className="px-4 py-2 text-sm font-semibold text-zinc-400 hover:text-white disabled:opacity-50 transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => selected && onAssign(selected.id)}
              disabled={assigning || !selected}
              className="inline-flex items-center gap-2 px-5 py-2 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 disabled:opacity-50 transition-colors text-sm"
            >
              {assigning && <Loader2 className="w-4 h-4 animate-spin" />}
              {assigning ? "Assigning…" : "Assign Access"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
