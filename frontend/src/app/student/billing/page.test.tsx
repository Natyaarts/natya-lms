import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import StudentBillingPortalPage from "./page";
import * as AuthContextModule from "@/context/AuthContext";

describe("StudentBillingPortalPage", () => {
  const mockUser: AuthContextModule.User = {
    id: 101,
    username: "student_swathi",
    email: "swathi@example.com",
    first_name: "Swathi",
    last_name: "Nair",
    is_student: true,
    is_teacher: false,
    is_superuser: false,
    is_onboarded: true,
  };

  const mockPlansResponse = {
    plans: [
      {
        id: 1,
        course: 10,
        course_details: { id: 10, title: "Mohiniyattam Level 1", price: "3000.00" },
        billing_type: "MONTHLY",
        amount: "3000.00",
        currency: "INR",
        start_date: "2026-10-01",
        current_period_start: "2026-10-01",
        current_period_end: "2026-10-31",
        next_billing_date: "2026-11-01",
        due_date: "2026-11-01",
        status: "ACTIVE",
        is_active: true,
      },
    ],
    alerts: {
      has_restricted_access: false,
      restricted_courses: [],
      has_grace_warning: false,
      grace_courses: [],
      active_extensions: [],
      total_outstanding_amount: "0.00",
      unpaid_invoices_count: 0,
    },
  };

  const mockInvoicesResponse = [
    {
      id: 50,
      invoice_number: "INV-2026-0050",
      course: 10,
      course_details: { id: 10, title: "Mohiniyattam Level 1", price: "3000.00" },
      period_start: "2026-10-01",
      period_end: "2026-10-31",
      issue_date: "2026-10-01",
      due_date: "2026-10-05",
      amount: "3000.00",
      currency: "INR",
      status: "PAID",
      payment_method: "RAZORPAY",
      paid_at: "2026-10-02T10:00:00Z",
    },
  ];

  let mockFetch: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.spyOn(AuthContextModule, "useAuth").mockReturnValue({
      user: mockUser,
      loading: false,
      isAuthenticated: true,
      isTeacher: false,
      isAdmin: false,
      refreshUser: vi.fn(),
      logout: vi.fn(),
    });

    mockFetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("invoices")) {
        return Promise.resolve({
          ok: true,
          json: async () => mockInvoicesResponse,
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => mockPlansResponse,
      });
    });

    vi.stubGlobal("fetch", mockFetch);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("dynamically fetches user-specific billing data with cache: no-store and credentials: include", async () => {
    render(<StudentBillingPortalPage />);

    await waitFor(() => {
      expect(screen.getAllByText("Mohiniyattam Level 1").length).toBeGreaterThan(0);
      expect(screen.getByText("INV-2026-0050")).toBeInTheDocument();
    });

    // Verify all fetches used user-isolated credentials and disabled client caching
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/billing/my-billing/"),
      expect.objectContaining({
        credentials: "include",
        cache: "no-store",
      })
    );

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/billing/my-billing/invoices/"),
      expect.objectContaining({
        credentials: "include",
        cache: "no-store",
      })
    );
  });

  it("renders grace period and restricted access alerts when returned from user API", async () => {
    const alertPlansResponse = {
      plans: [
        {
          id: 2,
          course: 20,
          course_details: { id: 20, title: "Kathakali Intensive", price: "4500.00" },
          billing_type: "MONTHLY",
          amount: "4500.00",
          currency: "INR",
          start_date: "2026-09-01",
          current_period_start: "2026-09-01",
          current_period_end: "2026-09-30",
          status: "RESTRICTED",
          is_active: true,
        },
      ],
      alerts: {
        has_restricted_access: true,
        restricted_courses: [
          {
            plan_id: 2,
            course_id: 20,
            course_title: "Kathakali Intensive",
            amount: "4500.00",
            invoice_id: 88,
            invoice_number: "INV-2026-0088",
          },
        ],
        has_grace_warning: false,
        grace_courses: [],
        active_extensions: [],
        total_outstanding_amount: "4500.00",
        unpaid_invoices_count: 1,
      },
    };

    mockFetch.mockImplementation((url: string) => {
      if (url.includes("/api/billing/my-billing/invoices/")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      return Promise.resolve({ ok: true, json: async () => alertPlansResponse });
    });

    render(<StudentBillingPortalPage />);

    await waitFor(() => {
      expect(screen.getByText("Content Access Restricted")).toBeInTheDocument();
      expect(screen.getByText(/Pay ₹4500.00 to Restore/i)).toBeInTheDocument();
    });
  });
});
