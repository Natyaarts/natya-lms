import { describe, it, expect, vi, afterEach } from "vitest";
import {
  requestStaffJoinUrl,
  isZoomParticipantLink,
  navigatePendingTab,
  closePendingTab,
  openPendingTab,
  HOST_LINK_UNAVAILABLE,
} from "./staffHostJoin";

const HOST = "https://us06web.zoom.us/s/97810176485?zak=abc";
const PARTICIPANT = "https://zoom.us/j/97810176485?pwd=xyz";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("isZoomParticipantLink", () => {
  it("detects participant links and not host links", () => {
    expect(isZoomParticipantLink(PARTICIPANT)).toBe(true);
    expect(isZoomParticipantLink("https://us02web.zoom.us/w/123?tk=1")).toBe(true);
    expect(isZoomParticipantLink("https://zoom.us/wc/join/123")).toBe(true);
    expect(isZoomParticipantLink(HOST)).toBe(false);
    expect(isZoomParticipantLink("https://meet.google.com/abc-defg-hij")).toBe(false);
    expect(isZoomParticipantLink("not a url")).toBe(false);
  });
});

describe("requestStaffJoinUrl", () => {
  it("POSTs to the class's join/ endpoint and returns the host link for a recognised teacher", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(200, { status: "host_or_staff", meeting_url: HOST }));
    const result = await requestStaffJoinUrl(42, fetcher);
    expect(fetcher).toHaveBeenCalledWith("/api/courses/live-classes/42/join/", { method: "POST" });
    expect(result).toEqual({ ok: true, url: HOST, asParticipant: false });
  });

  it("allows non-Zoom host links (e.g. Google Meet) returned for host_or_staff", async () => {
    const url = "https://meet.google.com/abc-defg-hij";
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(200, { status: "host_or_staff", meeting_url: url }));
    expect(await requestStaffJoinUrl(1, fetcher)).toEqual({ ok: true, url, asParticipant: false });
  });

  it("rejects a Zoom participant link even when status is host_or_staff (no host_url stored)", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(200, { status: "host_or_staff", meeting_url: PARTICIPANT }));
    expect(await requestStaffJoinUrl(1, fetcher)).toEqual({ ok: false, error: HOST_LINK_UNAVAILABLE });
  });

  it("rejects a student-style 'joined' response for a teacher", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(200, { status: "joined", meeting_url: PARTICIPANT }));
    expect(await requestStaffJoinUrl(1, fetcher)).toEqual({ ok: false, error: HOST_LINK_UNAVAILABLE });
  });

  it("surfaces the backend detail on 403 and does not fall back for teachers", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(403, { detail: "You are not enrolled in this live class." }));
    const result = await requestStaffJoinUrl(1, fetcher);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("You are not enrolled in this live class.");
  });

  it("returns an error on network failure", async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const result = await requestStaffJoinUrl(1, fetcher);
    expect(result.ok).toBe(false);
  });

  it("returns an error on a non-JSON 500", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("oops", { status: 500 }));
    const result = await requestStaffJoinUrl(1, fetcher);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("HTTP 500");
  });

  it("mentor fallback: uses the participant link only after explicit confirmation", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(403, { detail: "nope" }));
    const confirmYes = vi.fn().mockReturnValue(true);
    expect(await requestStaffJoinUrl(1, fetcher, { url: PARTICIPANT, confirm: confirmYes })).toEqual({
      ok: true,
      url: PARTICIPANT,
      asParticipant: true,
    });
    expect(confirmYes).toHaveBeenCalledOnce();

    const confirmNo = vi.fn().mockReturnValue(false);
    const declined = await requestStaffJoinUrl(1, fetcher, { url: PARTICIPANT, confirm: confirmNo });
    expect(declined.ok).toBe(false);
  });

  it("mentor fallback is not offered when the host link succeeds", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(200, { status: "host_or_staff", meeting_url: HOST }));
    const confirm = vi.fn();
    expect(await requestStaffJoinUrl(1, fetcher, { url: PARTICIPANT, confirm })).toEqual({
      ok: true,
      url: HOST,
      asParticipant: false,
    });
    expect(confirm).not.toHaveBeenCalled();
  });
});

describe("pending tab helpers", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens a blank tab without an opener and navigates it to the URL", () => {
    const fakeTab = { closed: false, opener: {} as unknown, location: { href: "" }, close: vi.fn() };
    const openSpy = vi.spyOn(window, "open").mockReturnValue(fakeTab as unknown as Window);
    const opened = openPendingTab();
    expect(openSpy).toHaveBeenCalledWith("", "_blank");
    expect(opened?.opener).toBeNull();
    navigatePendingTab(opened, HOST);
    expect(fakeTab.location.href).toBe(HOST);
  });

  it("falls back to window.open when the pending tab was blocked", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    navigatePendingTab(null, HOST);
    expect(openSpy).toHaveBeenCalledWith(HOST, "_blank", "noopener,noreferrer");
  });

  it("closes the pending tab on error", () => {
    const tab = { closed: false, close: vi.fn() } as unknown as Window;
    closePendingTab(tab);
    expect(tab.close).toHaveBeenCalled();
    expect(() => closePendingTab(null)).not.toThrow();
  });
});
