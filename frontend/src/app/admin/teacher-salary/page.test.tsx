import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import TeacherSalaryPage from "./page";

const TEACHERS = {
  teachers: [
    { id: 7, name: "Meera Nair", username: "meera", is_active: true, rate_cards: 1 },
    { id: 8, name: "Ravi Kumar", username: "ravi", is_active: true, rate_cards: 0 },
  ],
  courses: [{ id: 3, title: "Bharatanatyam" }],
};

const STATEMENT = {
  id: 11, teacher_id: 7, teacher_name: "Meera Nair", month: "2026-11", period_start: "2026-11-01", period_end: "2026-11-30",
  status: "DRAFT", currency: "INR", class_count: 2, total_minutes: 112, classes_amount: "1373.33", adjustment_amount: "0.00",
  adjustment_reason: "", net_amount: "1373.33", needs_review: 1, notes: "", method: "", reference_number: "",
  generated_at: null, approved_at: null, approved_by_name: "", paid_at: null, paid_by_name: "", cancelled_at: null,
};

const LINE = {
  id: 21, live_class_id: 5, class_title: "Adavus 1", course_title: "Bharatanatyam", scheduled_start: "2026-11-10T12:00:00Z",
  is_substitute: false, scheduled_minutes: 60, zoom_minutes: 52, attendee_count: 4, attendance_finalized: false,
  minutes_override: null, attendee_count_override: null, is_excluded: false, admin_note: "", per_class_amount: "100.00",
  per_student_amount: "20.00", paid_minutes: 52, students_counted: 4, hourly_rate: "800.00", tier_label: "2-5 students",
  hours_amount: "693.33", class_amount: "100.00", student_amount: "80.00", amount: "873.33",
  flags: ["ATTENDANCE_NOT_FINALIZED"], blocking: false,
};
const BLOCKED = {
  ...LINE, id: 22, class_title: "Adavus 2", zoom_minutes: null, paid_minutes: 0, amount: "0.00", hours_amount: "0.00",
  class_amount: "0.00", student_amount: "0.00", flags: ["NO_ZOOM_TIME"], blocking: true,
};

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("TeacherSalaryPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (url.includes("/settings/")) {
          const base = {
            count_late_as_attended: true, count_excused_as_attended: false, cap_zoom_time_at_schedule: true,
            missing_zoom_time: "REVIEW", updated_at: null, updated_by_name: "",
          };
          if (init?.method === "PUT") {
            return json({ ...base, ...JSON.parse(String(init.body)), updated_at: "2026-11-25T06:00:00Z", updated_by_name: "natyalms" });
          }
          return json(base);
        }
        if (url.includes("/teachers/")) return json(TEACHERS);
        if (url.includes("/statements/11/")) return json({ ...STATEMENT, lines: [LINE, BLOCKED] });
        if (url.includes("/statements/?month=")) return json([STATEMENT]);
        if (url.includes("/rate-cards/?teacher_id=7")) {
          return json([{
            id: 1, teacher_id: 7, teacher_name: "Meera Nair", course_id: null, course_title: "All other courses (default)",
            per_class_amount: "100.00", per_student_amount: "20.00", currency: "INR", is_active: true, notes: "",
            tiers: [{ min_students: 1, max_students: 1, hourly_rate: "500.00" }, { min_students: 2, max_students: null, hourly_rate: "800.00" }],
            updated_at: "2026-11-25T00:00:00Z",
          }]);
        }
        return json({ error: "unexpected" }, 404);
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists the month's statements with totals and opens the review panel", async () => {
    render(<TeacherSalaryPage />);
    expect(await screen.findByText("Meera Nair")).toBeTruthy();
    expect(screen.getAllByText("₹1,373.33").length).toBeGreaterThan(0);
    expect(screen.getByText("1 to review")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Adavus 1")).toBeTruthy();
    expect(within(dialog).getAllByText("2-5 students").length).toBe(2);
    expect(within(dialog).getByText("₹873.33")).toBeTruthy();
    expect(within(dialog).getByText("No Zoom time – enter minutes")).toBeTruthy();
    // A blocked class disables approval.
    expect((within(dialog).getByRole("button", { name: /Approve/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a teacher's rate card with hourly tiers", async () => {
    render(<TeacherSalaryPage />);
    fireEvent.click(screen.getByRole("button", { name: "Rate Cards" }));
    fireEvent.click(await screen.findByRole("button", { name: /Meera Nair/ }));
    expect(await screen.findByText("All other courses (default)")).toBeTruthy();
    expect(screen.getByText("₹800.00/hr")).toBeTruthy();
    expect(screen.getByText("no rates")).toBeTruthy(); // Ravi has no rate card yet
  });

  it("edits and saves the calculation settings", async () => {
    render(<TeacherSalaryPage />);
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const late = (await screen.findByRole("checkbox", { name: /Count Late students/ })) as HTMLInputElement;
    expect(late.checked).toBe(true);
    fireEvent.click(late);
    fireEvent.click(screen.getByRole("radio", { name: /Pay the scheduled duration/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText(/Saved\. Click Calculate/)).toBeTruthy();
    const put = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({
      count_late_as_attended: false, count_excused_as_attended: false,
      cap_zoom_time_at_schedule: true, missing_zoom_time: "USE_SCHEDULED",
    });
  });
});
