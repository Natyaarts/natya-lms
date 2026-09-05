"use client";

import { useMemo, useState } from "react";

// Phase 2: plain CSS-grid month calendar -- no new npm dependency, per the
// "do not overengineer" guidance and this stack having no calendar library.
// Week/day views are out of scope for this pass (list + Today/Upcoming/
// History tabs already cover that granularity); this is purely a visual
// "what's scheduled this month" overview shared by the admin and student
// live-classes pages.

export type CalendarClass = {
  id: number;
  title: string;
  scheduled_start: string;
  status: string;
};

const statusDot: Record<string, string> = {
  SCHEDULED: "bg-[#facc15]",
  LIVE: "bg-green-400",
  COMPLETED: "bg-zinc-500",
  CANCELLED: "bg-red-500",
};

export default function MonthCalendar({
  classes,
  onSelectClass,
}: {
  classes: CalendarClass[];
  onSelectClass?: (id: number) => void;
}) {
  const [cursor, setCursor] = useState(() => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1);
  });

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarClass[]>();
    for (const c of classes) {
      const d = new Date(c.scheduled_start);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(c);
    }
    return map;
  }, [classes]);

  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const firstOfMonth = new Date(year, month, 1);
  const startOffset = firstOfMonth.getDay(); // 0=Sun
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = new Date();

  const cells: (number | null)[] = [
    ...Array(startOffset).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];
  while (cells.length % 7 !== 0) cells.push(null);

  return (
    <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
        <button
          onClick={() => setCursor(new Date(year, month - 1, 1))}
          className="px-2 py-1 text-zinc-400 hover:text-white rounded-lg hover:bg-white/5 transition-colors"
        >
          &larr;
        </button>
        <div className="text-sm font-bold text-white">
          {cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" })}
        </div>
        <button
          onClick={() => setCursor(new Date(year, month + 1, 1))}
          className="px-2 py-1 text-zinc-400 hover:text-white rounded-lg hover:bg-white/5 transition-colors"
        >
          &rarr;
        </button>
      </div>

      <div className="grid grid-cols-7 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 border-b border-white/5">
        {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
          <div key={d} className="p-2 text-center">{d}</div>
        ))}
      </div>

      <div className="grid grid-cols-7">
        {cells.map((day, idx) => {
          if (day === null) {
            return <div key={idx} className="min-h-[92px] border-b border-r border-white/5" />;
          }
          const key = `${year}-${month}-${day}`;
          const dayClasses = byDay.get(key) || [];
          const isToday =
            today.getFullYear() === year && today.getMonth() === month && today.getDate() === day;
          return (
            <div key={idx} className="min-h-[92px] border-b border-r border-white/5 p-1.5 overflow-hidden">
              <div className={`text-[11px] font-semibold mb-1 w-5 h-5 flex items-center justify-center rounded-full ${
                isToday ? "bg-[#facc15] text-black" : "text-zinc-500"
              }`}>
                {day}
              </div>
              <div className="space-y-1">
                {dayClasses.slice(0, 3).map((c) => (
                  <button
                    key={c.id}
                    onClick={() => onSelectClass?.(c.id)}
                    title={c.title}
                    className="w-full flex items-center gap-1 px-1 py-0.5 rounded bg-white/5 hover:bg-white/10 text-left transition-colors"
                  >
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDot[c.status] || "bg-zinc-500"}`} />
                    <span className="text-[9px] text-zinc-300 truncate">{c.title}</span>
                  </button>
                ))}
                {dayClasses.length > 3 && (
                  <div className="text-[9px] text-zinc-500 px-1">+{dayClasses.length - 3} more</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
