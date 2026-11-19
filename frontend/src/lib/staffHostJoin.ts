/**
 * Teacher / mentor "Start Class" & "Enter Class" link resolution.
 *
 * Since commit 51715d3 the live-class list/detail API no longer exposes
 * `host_url` to teachers (only admins see it there). The host link is
 * instead handed out on demand by the authenticated
 * `POST /api/courses/live-classes/{id}/join/` endpoint, which returns
 * `{ status: "host_or_staff", meeting_url: <host link> }` for the class's
 * assigned (or effective/substitute) instructor.
 *
 * A teacher must NEVER be silently sent to the participant link: on a
 * Zoom meeting created under a pool host account with join_before_host
 * off, that just strands them on "Waiting for the host".
 */

export type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

export type StaffJoinResult =
  | { ok: true; url: string; asParticipant: boolean }
  | { ok: false; error: string };

export const HOST_LINK_UNAVAILABLE =
  "The Zoom host link for this class is not available for your account. " +
  "Please contact an admin -- do not join with the student link, or the class will wait for a host.";

/**
 * True for a Zoom participant join link (zoom.us/j/..., /w/..., /wc/join/...).
 * A Zoom host (start) link uses /s/... instead.
 */
export function isZoomParticipantLink(url: string): boolean {
  try {
    const u = new URL(url);
    return /(^|\.)zoom\.us$/i.test(u.hostname) && /^\/(j|w|wc\/join)\//i.test(u.pathname);
  } catch {
    return false;
  }
}

/**
 * Ask the backend for this user's host link via `join/`.
 *
 * Teachers: call with no `participantFallback` -- any failure is returned as an error.
 * Mentors: may pass `participantFallback` (the class's participant link plus a
 * confirm callback). Only used if the host link can't be obtained AND the
 * mentor explicitly confirms joining as a participant.
 */
export async function requestStaffJoinUrl(
  liveClassId: number | string,
  fetcher: Fetcher,
  participantFallback?: { url?: string | null; confirm: (message: string) => boolean }
): Promise<StaffJoinResult> {
  const hostError = await (async (): Promise<StaffJoinResult> => {
    let res: Response;
    try {
      res = await fetcher(`/api/courses/live-classes/${encodeURIComponent(String(liveClassId))}/join/`, {
        method: "POST",
      });
    } catch {
      return { ok: false, error: "Network error while requesting the host link. Please try again." };
    }
    const data = (await res.json().catch(() => null)) as
      | { status?: string; meeting_url?: unknown; detail?: string; error?: string }
      | null;
    if (!res.ok) {
      const detail = data?.detail || data?.error;
      return {
        ok: false,
        error: detail
          ? `Could not get the host link: ${detail}`
          : `Could not get the host link (HTTP ${res.status}). Please try again or contact an admin.`,
      };
    }
    if (data?.status !== "host_or_staff") {
      return { ok: false, error: HOST_LINK_UNAVAILABLE };
    }
    const url = typeof data?.meeting_url === "string" ? data.meeting_url.trim() : "";
    if (!/^https?:\/\//i.test(url) || isZoomParticipantLink(url)) {
      // Backend fell back to the participant link (no host_url stored).
      return { ok: false, error: HOST_LINK_UNAVAILABLE };
    }
    return { ok: true, url, asParticipant: false };
  })();

  if (hostError.ok) return hostError;

  const fallbackUrl = participantFallback?.url?.trim();
  if (
    fallbackUrl &&
    /^https?:\/\//i.test(fallbackUrl) &&
    participantFallback!.confirm(`${hostError.error}\n\nJoin as a participant instead?`)
  ) {
    return { ok: true, url: fallbackUrl, asParticipant: true };
  }
  return hostError;
}

/**
 * Open a blank tab synchronously inside the click handler, so the browser's
 * popup blocker allows it even though the real URL arrives after an await.
 */
export function openPendingTab(): Window | null {
  try {
    const tab = window.open("", "_blank");
    if (tab) tab.opener = null;
    return tab;
  } catch {
    return null;
  }
}

export function navigatePendingTab(tab: Window | null, url: string): void {
  if (tab && !tab.closed) {
    tab.location.href = url;
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

export function closePendingTab(tab: Window | null): void {
  try {
    if (tab && !tab.closed) tab.close();
  } catch {
    /* ignore */
  }
}
