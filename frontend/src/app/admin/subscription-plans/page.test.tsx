import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SubscriptionPlansPage from "./page";

const mockPlans = [
  {
    id: 1,
    name: "Kathak Masterclass All-Access",
    slug: "kathak-masterclass-all-access",
    description: "Complete access to all Kathak foundation and advanced modules",
    billing_interval: "MONTHLY",
    price: "1499.00",
    currency: "INR",
    is_active: true,
    subscriber_count: 5,
    courses: [
      { id: 101, title: "Kathak Foundation Level 1", thumbnail: null, price: "999.00", course_type: "COURSE", is_published: true },
      { id: 102, title: "Kathak Intermediate Footwork", thumbnail: null, price: "1299.00", course_type: "COURSE", is_published: true }
    ]
  },
  {
    id: 2,
    name: "Bharatanatyam Annual Pass",
    slug: "bharatanatyam-annual-pass",
    description: "Yearly pass for all classical dance courses",
    billing_interval: "YEARLY",
    price: "9999.00",
    currency: "INR",
    is_active: false,
    subscriber_count: 0,
    courses: []
  }
];

describe("SubscriptionPlansPage Admin Dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/orders/subscription-plans/")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockPlans)
        });
      }
      if (url.includes("/api/courses/")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { id: 101, title: "Kathak Foundation Level 1", price: "999.00", is_published: true },
            { id: 102, title: "Kathak Intermediate Footwork", price: "1299.00", is_published: true }
          ])
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
  });

  it("renders page header, metrics, and plan list", async () => {
    render(<SubscriptionPlansPage />);

    expect(screen.getByText("Subscription Plans")).toBeInTheDocument();
    expect(screen.getByText("Create Subscription Plan")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Kathak Masterclass All-Access")).toBeInTheDocument();
      expect(screen.getByText("Bharatanatyam Annual Pass")).toBeInTheDocument();
    });

    // Check pricing and status tags
    expect(screen.getAllByText("ACTIVE").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("INACTIVE").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/1,499/)).toBeInTheDocument();
  });

  it("opens create plan modal when button is clicked", async () => {
    render(<SubscriptionPlansPage />);

    const createBtn = screen.getByText("Create Subscription Plan");
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(screen.getByText("New Catalog Offering")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("e.g. Kathak Masterclass All-Access")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("1499.00")).toBeInTheDocument();
    });
  });

  it("filters plans by search keyword", async () => {
    render(<SubscriptionPlansPage />);

    await waitFor(() => {
      expect(screen.getByText("Kathak Masterclass All-Access")).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/Search plans by name/i);
    fireEvent.change(searchInput, { target: { value: "Bharatanatyam" } });

    expect(screen.queryByText("Kathak Masterclass All-Access")).not.toBeInTheDocument();
    expect(screen.getByText("Bharatanatyam Annual Pass")).toBeInTheDocument();
  });

  it("triggers safeguard modal when attempting to delete plan with subscribers", async () => {
    render(<SubscriptionPlansPage />);

    await waitFor(() => {
      expect(screen.getByText("Kathak Masterclass All-Access")).toBeInTheDocument();
    });

    // Plan #1 has subscriber_count: 5. Find its delete button
    const deleteButtons = screen.getAllByTitle("Delete Plan");
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(screen.getByText("Plan Cannot Be Deleted")).toBeInTheDocument();
      expect(screen.getByText(/5 active or historical student subscription/i)).toBeInTheDocument();
      expect(screen.getByText("Deactivate Plan Instead")).toBeInTheDocument();
    });
  });
});
