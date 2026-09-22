import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import AppHeader from "./AppHeader";
import AccountMenu from "./AccountMenu";
import MobileNavDrawer from "./MobileNavDrawer";
import * as AuthContextModule from "@/context/AuthContext";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
}));

vi.mock("@/components/NotificationBell", () => ({
  default: () => <div data-testid="notification-bell">Bell</div>,
}));

describe("Navigation Components", () => {
  const mockUser: AuthContextModule.User = {
    id: 1,
    username: "swathi",
    email: "swathi@example.com",
    first_name: "Swathi",
    last_name: "Nair",
    is_student: true,
    is_teacher: false,
    is_superuser: false,
    is_onboarded: true,
  };

  const mockAuthValue = {
    user: mockUser,
    loading: false,
    isAuthenticated: true,
    isTeacher: false,
    isAdmin: false,
    refreshUser: vi.fn(),
    logout: vi.fn(),
  };

  beforeEach(() => {
    vi.spyOn(AuthContextModule, "useAuth").mockReturnValue(mockAuthValue);
  });

  it("renders desktop navigation links and brand logo in AppHeader", () => {
    render(<AppHeader />);
    expect(screen.getByText("Explore")).toBeDefined();
    expect(screen.getByText("Bundles")).toBeDefined();
    expect(screen.getByText("My Learning")).toBeDefined();
    expect(screen.getByText("Live Classes")).toBeDefined();
    expect(screen.getByTestId("notification-bell")).toBeDefined();
  });

  it("opens and interacts with the AccountMenu dropdown", () => {
    render(<AccountMenu />);
    const button = screen.getByRole("button", { name: /account menu/i });
    expect(button).toBeDefined();

    fireEvent.click(button);
    expect(screen.getByText("Swathi Nair")).toBeDefined();
    expect(screen.getByText("My Profile")).toBeDefined();
    expect(screen.getByText("My Orders")).toBeDefined();
    expect(screen.getByText("Subscriptions")).toBeDefined();
    expect(screen.getByText("Invoices")).toBeDefined();

    const logoutBtn = screen.getByText("Log Out");
    fireEvent.click(logoutBtn);
    expect(mockAuthValue.logout).toHaveBeenCalled();
  });

  it("renders MobileNavDrawer when open with all navigation sections", () => {
    const handleClose = vi.fn();
    render(<MobileNavDrawer isOpen={true} onClose={handleClose} />);

    expect(screen.getByText("Learning")).toBeDefined();
    expect(screen.getByText("Account & Billing")).toBeDefined();
    expect(screen.getByText("Explore Courses")).toBeDefined();
    expect(screen.getByText("Course Bundles")).toBeDefined();
    expect(screen.getByText("My Learning")).toBeDefined();
    expect(screen.getByText("Live Classes")).toBeDefined();
    expect(screen.getByText("Notifications")).toBeDefined();
  });
});
