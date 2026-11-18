"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, Video, AlertCircle, ExternalLink } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function JoinLiveClassPage() {
  const params = useParams();
  const router = useRouter();
  const id = params?.id ? String(params.id) : "";

  const [status, setStatus] = useState<"checking" | "joining" | "redirecting" | "error">("checking");
  const [errorMsg, setErrorMsg] = useState("");
  const [joinUrl, setJoinUrl] = useState("");

  useEffect(() => {
    if (!id) {
      setStatus("error");
      setErrorMsg("Invalid live class link.");
      return;
    }

    let cancelled = false;

    const run = async () => {
      try {
        const authRes = await fetch(`${API}/api/auth/user/`, { credentials: "include" });
        if (!authRes.ok) {
          if (!cancelled) {
            router.replace(`/login?next=${encodeURIComponent(`/live-classes/join/${id}`)}`);
          }
          return;
        }

        if (cancelled) return;
        setStatus("joining");

        const joinRes = await fetch(`${API}/api/courses/live-classes/${encodeURIComponent(id)}/join/`, {
          method: "POST",
          credentials: "include",
        });

        const data = await joinRes.json().catch(() => ({}));
        if (!joinRes.ok) {
          if (!cancelled) {
            setStatus("error");
            setErrorMsg(
              data.detail ||
                data.error ||
                (joinRes.status === 403
                  ? "You are not enrolled in this live class or batch."
                  : "Unable to join this live class.")
            );
          }
          return;
        }

        const targetUrl = typeof data.meeting_url === "string" ? data.meeting_url.trim() : "";
        if (!targetUrl || (!targetUrl.startsWith("https://") && !targetUrl.startsWith("http://"))) {
          if (!cancelled) {
            setStatus("error");
            setErrorMsg("Live class meeting link is not available yet.");
          }
          return;
        }

        if (!cancelled) {
          setJoinUrl(targetUrl);
          setStatus("redirecting");
          window.location.assign(targetUrl);
        }
      } catch {
        if (!cancelled) {
          setStatus("error");
          setErrorMsg("Network error while verifying your class access. Please try again.");
        }
      }
    };

    run();
    return () => {
      cancelled = true;
    };
  }, [id, router]);

  return (
    <div className="min-h-screen bg-black text-white flex items-center justify-center px-6">
      <div className="max-w-md w-full bg-zinc-900 border border-white/10 rounded-2xl p-8 text-center space-y-4">
        <div className="w-12 h-12 rounded-full bg-[#facc15]/10 border border-[#facc15]/20 flex items-center justify-center text-[#facc15] mx-auto">
          {status === "error" ? <AlertCircle className="w-6 h-6 text-red-400" /> : <Video className="w-6 h-6" />}
        </div>

        {status === "checking" || status === "joining" ? (
          <>
            <h1 className="text-xl font-bold">
              {status === "checking" ? "Verifying your account..." : "Checking class enrollment..."}
            </h1>
            <p className="text-sm text-zinc-400">
              Please wait while we verify your access and connect you to your live class.
            </p>
            <Loader2 className="w-5 h-5 animate-spin text-[#facc15] mx-auto" />
          </>
        ) : status === "redirecting" ? (
          <>
            <h1 className="text-xl font-bold">Connecting to Live Class</h1>
            <p className="text-sm text-zinc-400">
              Your enrollment is verified. If the meeting does not open automatically, click below.
            </p>
            <a
              href={joinUrl}
              className="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-[#facc15] text-black font-bold text-sm hover:bg-yellow-400 transition-colors"
            >
              <ExternalLink className="w-4 h-4" /> Open Live Class
            </a>
          </>
        ) : (
          <>
            <h1 className="text-xl font-bold text-red-400">Unable to Join Class</h1>
            <p className="text-sm text-zinc-400">{errorMsg}</p>
            <div className="pt-2">
              <Link
                href="/live-classes"
                className="inline-block px-5 py-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-white font-semibold text-sm transition-colors"
              >
                Go to Live Classes
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

