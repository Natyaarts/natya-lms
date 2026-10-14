import { describe, it, expect } from "vitest";
import {
  MAX_CHAT_ATTACHMENT_SIZE_BYTES,
  validateChatAttachment,
  formatFileSize,
} from "./chatApi";

describe("Chat Attachment Client Validation", () => {
  it("enforces exact 52,428,800 bytes boundary", () => {
    expect(MAX_CHAT_ATTACHMENT_SIZE_BYTES).toBe(52428800);
  });

  it("accepts valid PDF exactly at 50 MiB", () => {
    const file = new File([new Uint8Array(10)], "document.pdf", { type: "application/pdf" });
    Object.defineProperty(file, "size", { value: MAX_CHAT_ATTACHMENT_SIZE_BYTES });
    const res = validateChatAttachment(file);
    expect(res.valid).toBe(true);
    expect(res.error).toBeUndefined();
  });

  it("rejects file exceeding 50 MiB by 1 byte", () => {
    const file = new File([new Uint8Array(10)], "huge.pdf", { type: "application/pdf" });
    Object.defineProperty(file, "size", { value: MAX_CHAT_ATTACHMENT_SIZE_BYTES + 1 });
    const res = validateChatAttachment(file);
    expect(res.valid).toBe(false);
    expect(res.error).toContain("50 MB");
    expect(res.error).toContain("52,428,800 bytes");
  });

  it("rejects disallowed extensions like .exe or .sh", () => {
    const file = new File([new Uint8Array(10)], "script.sh", { type: "text/x-sh" });
    Object.defineProperty(file, "size", { value: 1000 });
    const res = validateChatAttachment(file);
    expect(res.valid).toBe(false);
    expect(res.error).toContain(".sh");
  });

  it("accepts valid extensions (.jpg, .png, .docx, .txt)", () => {
    const extensions = ["jpg", "jpeg", "png", "webp", "pdf", "docx", "txt"];
    extensions.forEach((ext) => {
      const file = new File([new Uint8Array(10)], `file.${ext}`, { type: "application/octet-stream" });
      Object.defineProperty(file, "size", { value: 1024 });
      const res = validateChatAttachment(file);
      expect(res.valid).toBe(true);
    });
  });

  it("formats file sizes cleanly", () => {
    expect(formatFileSize(500)).toBe("500 B");
    expect(formatFileSize(1024)).toBe("1 KB");
    expect(formatFileSize(1048576)).toBe("1 MB");
    expect(formatFileSize(52428800)).toBe("50 MB");
  });
});

describe("Chat API Contract & Deep Linking", () => {
  it("resolves canonical /read/ endpoint in markConversationRead", async () => {
    let requestedUrl = "";
    globalThis.fetch = (async (url: string) => {
      requestedUrl = url;
      return { ok: true, json: async () => ({ status: "ok" }) } as any;
    }) as any;

    const { markConversationRead } = await import("./chatApi");
    await markConversationRead("c03260be-aeb1-4603-9e4a-b50a3b2b9db4");
    expect(requestedUrl).toContain("/api/chat/conversations/c03260be-aeb1-4603-9e4a-b50a3b2b9db4/read/");
  });

  it("resolves canonical /unread-count/ and parses total_unread_count or unread_count", async () => {
    let requestedUrl = "";
    globalThis.fetch = (async (url: string) => {
      requestedUrl = url;
      return {
        ok: true,
        json: async () => ({ total_unread_count: 5, unread_count: 5 }),
      } as any;
    }) as any;

    const { fetchUnreadCount } = await import("./chatApi");
    const count = await fetchUnreadCount();
    expect(requestedUrl).toContain("/api/chat/conversations/unread-count/");
    expect(count).toBe(5);
  });

  it("queries unified /api/chat/recipients/ in fetchDirectChatRecipients", async () => {
    let requestedUrl = "";
    globalThis.fetch = (async (url: string) => {
      requestedUrl = url;
      return {
        ok: true,
        status: 200,
        json: async () => [
          { id: 10, username: "mentor1", first_name: "Priya", last_name: "Nair", role: "MENTOR" },
          { id: 15, username: "teacher1", first_name: "Kavita", last_name: "K", role: "TEACHER" },
        ],
      } as any;
    }) as any;

    const { fetchDirectChatRecipients } = await import("./chatApi");
    const list = await fetchDirectChatRecipients();
    expect(requestedUrl).toContain("/api/chat/recipients/");
    expect(list).toHaveLength(2);
    expect(list[0].role).toBe("MENTOR");
    expect(list[1].role).toBe("TEACHER");
  });

  it("throws authentication required error when fetchDirectChatRecipients gets 401", async () => {
    globalThis.fetch = (async () => {
      return {
        ok: false,
        status: 401,
        json: async () => ({ detail: "Authentication credentials were not provided." }),
      } as any;
    }) as any;

    const { fetchDirectChatRecipients } = await import("./chatApi");
    await expect(fetchDirectChatRecipients()).rejects.toThrow("Authentication required");
  });

  it("throws server error message when fetchDirectChatRecipients fails", async () => {
    globalThis.fetch = (async () => {
      return {
        ok: false,
        status: 500,
        json: async () => ({ error: "Internal service error" }),
      } as any;
    }) as any;

    const { fetchDirectChatRecipients } = await import("./chatApi");
    await expect(fetchDirectChatRecipients()).rejects.toThrow("Internal service error");
  });

  it("handles push deep-link route resolution with UUIDs, integers, and invalid paths", () => {
    // Pure function logic identical to mobile resolveActionUrlToRoute
    const resolveRoute = (actionUrl?: string | null) => {
      const fallback = { name: "MainTabs", params: { screen: "Home" } };
      if (!actionUrl || typeof actionUrl !== "string") return fallback;
      const url = actionUrl.trim();
      if (url === "/chat") return { name: "ChatList" };
      const match =
        url.match(/^\/chat\/conversations\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$/i) ||
        url.match(/^\/chat\/conversations\/(\d+)$/);
      if (match) {
        const rawId = match[1];
        const conversationId = /^\d+$/.test(rawId) ? Number(rawId) : rawId;
        return { name: "Conversation", params: { conversationId } };
      }
      return fallback;
    };

    // 1. Valid UUID deep link
    const uuidRoute = resolveRoute("/chat/conversations/c03260be-aeb1-4603-9e4a-b50a3b2b9db4");
    expect(uuidRoute).toEqual({
      name: "Conversation",
      params: { conversationId: "c03260be-aeb1-4603-9e4a-b50a3b2b9db4" },
    });

    // 2. Backward-compatible numeric ID
    const intRoute = resolveRoute("/chat/conversations/42");
    expect(intRoute).toEqual({
      name: "Conversation",
      params: { conversationId: 42 },
    });

    // 3. /chat list
    const chatListRoute = resolveRoute("/chat");
    expect(chatListRoute).toEqual({ name: "ChatList" });

    // 4. Invalid paths fall back to Home
    expect(resolveRoute("/chat/conversations/not-a-valid-uuid-or-int")).toEqual({
      name: "MainTabs",
      params: { screen: "Home" },
    });
    expect(resolveRoute("/unknown/path")).toEqual({
      name: "MainTabs",
      params: { screen: "Home" },
    });
    expect(resolveRoute(null)).toEqual({
      name: "MainTabs",
      params: { screen: "Home" },
    });
  });
});
