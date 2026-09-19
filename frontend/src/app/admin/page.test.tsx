import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import AdminDashboard from "./page";

// Regression test for a real production incident: academy.natyaarts.com/admin
// crashed with "TypeError: Cannot read properties of undefined (reading
// 'toLocaleString')" even though GET /api/users/admin-stats/ returned 200.
//
// Root cause: AdminStatsView gained several new fields (total_mentors,
// active_subscriptions_count, draft_payouts_count,
// approved_payouts_pending_count, pending_refunds_count,
// pending_assignment_grading_count, upcoming_live_classes_count,
// recent_refunds, upcoming_live_classes) after this page started reading
// them. A version-skewed deployment (this newer frontend against an older
// backend build that predates those fields) returns a 200 response that
// simply omits them -- and the page's own setStats(data) REPLACED the
// safe-default state outright, turning every missing field into
// `undefined`, which then crashed on `.toLocaleString()`.
//
// This test reproduces exactly that: a 200 response shaped like the OLD
// AdminStatsView (missing all nine newer fields), rendered through the
// real AdminDashboard component.
describe("AdminDashboard", () => {
  const OLD_BACKEND_RESPONSE = {
    total_students: 120,
    new_students_week: 4,
    new_students_month: 15,
    active_students: 110,
    inactive_students: 10,
    total_teachers: 8,

    total_courses: 12,
    active_courses: 10,
    draft_courses: 2,
    top_courses: [],

    total_revenue: 50000,
    current_month_revenue: 5000,
    success_payments: 30,
    pending_payments: 2,
    failed_payments: 1,
    revenue_breakdown: [],

    total_enrollments: 200,
    new_enrollments_month: 20,
    paid_enrollments_count: 150,
    manual_enrollments_count: 50,

    recent_registrations: [],
    recent_payments: [],
    recent_enrollments: [],
    // Deliberately absent, matching an old AdminStatsView build:
    // total_mentors, active_subscriptions_count, draft_payouts_count,
    // approved_payouts_pending_count, pending_refunds_count,
    // pending_assignment_grading_count, upcoming_live_classes_count,
    // recent_refunds, upcoming_live_classes.
  };

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => OLD_BACKEND_RESPONSE,
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not crash when the API response omits newer stat fields (old-backend / version-skew scenario)", async () => {
    render(<AdminDashboard />);

    // Loading state first, then the real content once fetchStats resolves.
    await waitFor(() => expect(screen.getByText("Admin Overview")).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText("Compiling analytics data...")).not.toBeInTheDocument());

    // Fields present in the response render their real values.
    expect(screen.getByText("120")).toBeInTheDocument(); // total_students

    // Fields ABSENT from the response (the exact crash trigger) must fall
    // back to a safe "0", not throw and not render "undefined"/"NaN".
    const mentorsCard = screen.getByText("Mentors").closest("a");
    expect(mentorsCard).not.toBeNull();
    expect(mentorsCard).toHaveTextContent("0");

    const activeSubsCard = screen.getByText("Active Subs").closest("a");
    expect(activeSubsCard).toHaveTextContent("0");

    const pendingRefundsCard = screen.getByText("Pending Refunds").closest("a");
    expect(pendingRefundsCard).toHaveTextContent("0");

    // The array-backed "Recent Refunds" / "Upcoming Live Classes" sections
    // (also absent from the old response) must render their empty states,
    // not throw on .length/.map() of an undefined value.
    expect(screen.getByText("No refunds requested yet.")).toBeInTheDocument();
    expect(screen.getByText("No upcoming live classes scheduled.")).toBeInTheDocument();
  });

  it("renders real values when the API response includes every current field (happy path, unchanged)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          ...OLD_BACKEND_RESPONSE,
          total_mentors: 5,
          active_subscriptions_count: 42,
          draft_payouts_count: 3,
          approved_payouts_pending_count: 7,
          pending_refunds_count: 2,
          pending_assignment_grading_count: 9,
          upcoming_live_classes_count: 1,
          recent_refunds: [],
          upcoming_live_classes: [],
        }),
      }),
    );

    render(<AdminDashboard />);

    await waitFor(() => expect(screen.queryByText("Compiling analytics data...")).not.toBeInTheDocument());

    expect(screen.getByText("Mentors").closest("a")).toHaveTextContent("5");
    expect(screen.getByText("Active Subs").closest("a")).toHaveTextContent("42");
  });
});
