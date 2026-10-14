import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import AdminChatPage from "./page";
import * as adminChatApi from "@/lib/adminChatApi";

describe("AdminChatPage Component", () => {
  const mockConversation: adminChatApi.AdminConversation = {
    id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    conversation_type: "DIRECT",
    conversation_type_display: "Direct",
    title: "Ananya & Guru Prasad",
    course: null,
    course_title: null,
    batch: null,
    batch_name: null,
    created_by: 1,
    created_by_user: {
      id: 1,
      username: "ananya",
      first_name: "Ananya",
      last_name: "Sharma",
      role: "STUDENT",
    },
    last_message_at: "2026-10-14T10:00:00Z",
    last_message_preview: "Namaste Guru ji",
    message_count: 2,
    created_at: "2026-10-14T09:00:00Z",
    updated_at: "2026-10-14T10:00:00Z",
    participants: [
      {
        id: 101,
        user: {
          id: 1,
          username: "ananya",
          first_name: "Ananya",
          last_name: "Sharma",
          full_name: "Ananya Sharma",
          email: "ananya@example.com",
          phone_number: "+919876543210",
          role: "STUDENT",
          is_teacher: false,
          is_mentor: false,
          is_student: true,
          is_staff: false,
          is_superuser: false,
        },
        is_admin: false,
        is_muted: false,
        joined_at: "2026-10-14T09:00:00Z",
      },
      {
        id: 102,
        user: {
          id: 2,
          username: "guru_prasad",
          first_name: "Guru",
          last_name: "Prasad",
          full_name: "Guru Prasad",
          email: "guru@example.com",
          role: "MENTOR",
          is_teacher: false,
          is_mentor: true,
          is_student: false,
          is_staff: false,
          is_superuser: false,
        },
        is_admin: false,
        is_muted: false,
        joined_at: "2026-10-14T09:00:00Z",
      },
    ],
  };

  const mockMessage: adminChatApi.AdminMessage = {
    id: 501,
    conversation: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    sender: {
      id: 1,
      username: "ananya",
      first_name: "Ananya",
      last_name: "Sharma",
      full_name: "Ananya Sharma",
      email: "ananya@example.com",
      role: "STUDENT",
      is_teacher: false,
      is_mentor: false,
      is_student: true,
      is_staff: false,
      is_superuser: false,
    },
    content: "Namaste Guru ji, I have completed the Adavus practice.",
    message_type: "TEXT",
    has_attachment: false,
    is_deleted: false,
    created_at: "2026-10-14T10:00:00Z",
  };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders page header, search controls, and loaded conversation list", async () => {
    vi.spyOn(adminChatApi, "fetchAdminConversations").mockResolvedValue({
      count: 1,
      next: null,
      previous: null,
      results: [mockConversation],
    });

    vi.spyOn(adminChatApi, "fetchAdminConversationDetail").mockResolvedValue(mockConversation);
    vi.spyOn(adminChatApi, "fetchAdminMessages").mockResolvedValue({
      count: 1,
      next: null,
      previous: null,
      results: [mockMessage],
    });

    render(<AdminChatPage />);

    expect(screen.getByText("Chat Management")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Search by participant name/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("Ananya & Guru Prasad").length).toBeGreaterThanOrEqual(1);
    });

    expect(screen.getAllByText("Direct (1:1)").length).toBeGreaterThanOrEqual(1);
  });

  it("opens moderation modal when Moderate button is clicked", async () => {
    vi.spyOn(adminChatApi, "fetchAdminConversations").mockResolvedValue({
      count: 1,
      next: null,
      previous: null,
      results: [mockConversation],
    });

    vi.spyOn(adminChatApi, "fetchAdminConversationDetail").mockResolvedValue(mockConversation);
    vi.spyOn(adminChatApi, "fetchAdminMessages").mockResolvedValue({
      count: 1,
      next: null,
      previous: null,
      results: [mockMessage],
    });

    render(<AdminChatPage />);

    await waitFor(() => {
      expect(screen.getByText("Namaste Guru ji, I have completed the Adavus practice.")).toBeInTheDocument();
    });

    const moderateBtn = screen.getByRole("button", { name: /Moderate/i });
    fireEvent.click(moderateBtn);

    expect(screen.getByText(/Moderate Message \(Soft Delete\)/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Violation of community standards/i)).toBeInTheDocument();
  });
});
