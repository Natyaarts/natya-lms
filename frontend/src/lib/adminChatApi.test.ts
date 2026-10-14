import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fetchAdminConversations,
  fetchAdminConversationDetail,
  fetchAdminMessages,
  moderateAdminMessage,
  getAdminAttachmentPresignedUrl,
  formatFileSize,
} from "./adminChatApi";

describe("Admin Chat API Client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("formats file sizes correctly", () => {
    expect(formatFileSize(0)).toBe("0 B");
    expect(formatFileSize(1024)).toBe("1 KB");
    expect(formatFileSize(52428800)).toBe("50 MB");
  });

  it("constructs query params correctly for fetchAdminConversations", async () => {
    let capturedUrl = "";
    globalThis.fetch = vi.fn(async (url: any) => {
      capturedUrl = url;
      return {
        ok: true,
        json: async () => ({ count: 1, next: null, previous: null, results: [] }),
      } as any;
    });

    await fetchAdminConversations({
      page: 2,
      page_size: 20,
      search: "Ananya",
      type: "DIRECT",
      course_id: 10,
      batch_id: 5,
      date_from: "2026-01-01",
      date_to: "2026-01-31",
    });

    const parsed = new URL(capturedUrl);
    expect(parsed.pathname).toBe("/api/chat/admin/conversations/");
    expect(parsed.searchParams.get("page")).toBe("2");
    expect(parsed.searchParams.get("page_size")).toBe("20");
    expect(parsed.searchParams.get("search")).toBe("Ananya");
    expect(parsed.searchParams.get("type")).toBe("DIRECT");
    expect(parsed.searchParams.get("course_id")).toBe("10");
    expect(parsed.searchParams.get("batch_id")).toBe("5");
    expect(parsed.searchParams.get("date_from")).toBe("2026-01-01");
    expect(parsed.searchParams.get("date_to")).toBe("2026-01-31");
  });

  it("throws permission denied error on HTTP 403 Forbidden", async () => {
    globalThis.fetch = vi.fn(async () => {
      return {
        ok: false,
        status: 403,
        json: async () => ({ detail: "You do not have permission to perform this action." }),
      } as any;
    });

    await expect(fetchAdminConversations()).rejects.toThrow(
      "Access denied: You do not have administrator permissions to view all conversations."
    );
  });

  it("calls detail endpoint and message endpoint with conversation ID", async () => {
    let capturedDetailUrl = "";
    let capturedMessagesUrl = "";

    globalThis.fetch = vi.fn(async (url: any) => {
      if (url.includes("/messages/")) {
        capturedMessagesUrl = url;
        return {
          ok: true,
          json: async () => ({ count: 0, next: null, previous: null, results: [] }),
        } as any;
      } else {
        capturedDetailUrl = url;
        return {
          ok: true,
          json: async () => ({ id: "conv-123", title: "Test Chat" }),
        } as any;
      }
    });

    const detail = await fetchAdminConversationDetail("conv-123");
    expect(capturedDetailUrl).toContain("/api/chat/admin/conversations/conv-123/");
    expect(detail.title).toBe("Test Chat");

    await fetchAdminMessages("conv-123", 2);
    expect(capturedMessagesUrl).toContain("/api/chat/admin/conversations/conv-123/messages/?page=2");
  });

  it("posts moderation action and reason to moderateAdminMessage", async () => {
    let capturedOptions: any = null;
    let capturedUrl = "";

    globalThis.fetch = vi.fn(async (url: any, options: any) => {
      capturedUrl = url;
      capturedOptions = options;
      return {
        ok: true,
        json: async () => ({ id: 42, is_deleted: true, moderation_reason: "Violates guidelines" }),
      } as any;
    });

    const res = await moderateAdminMessage(42, "soft_delete", "Violates guidelines");
    expect(capturedUrl).toContain("/api/chat/admin/messages/42/moderate/");
    expect(capturedOptions.method).toBe("POST");
    expect(JSON.parse(capturedOptions.body)).toEqual({
      action: "soft_delete",
      reason: "Violates guidelines",
    });
    expect(res.is_deleted).toBe(true);
  });

  it("requests presigned URL for attachment download", async () => {
    let capturedUrl = "";
    globalThis.fetch = vi.fn(async (url: any) => {
      capturedUrl = url;
      return {
        ok: true,
        json: async () => ({ download_url: "https://s3.example.com/test.pdf", expires_in: 900 }),
      } as any;
    });

    const res = await getAdminAttachmentPresignedUrl(99);
    expect(capturedUrl).toContain("/api/chat/messages/99/attachment-url/");
    expect(res.download_url).toBe("https://s3.example.com/test.pdf");
    expect(res.expires_in).toBe(900);
  });
});
