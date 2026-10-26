import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import WebVideoPlayer from "./WebVideoPlayer";

vi.mock("framer-motion", () => ({
  motion: new Proxy(
    {},
    {
      get: (_target, prop: string) => {
        return ({ children, ...props }: any) => {
          const Tag = prop as any;
          return <Tag {...props}>{children}</Tag>;
        };
      },
    }
  ),
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

describe("WebVideoPlayer Component", () => {
  beforeEach(() => {
    // Mock HTMLMediaElement methods
    window.HTMLMediaElement.prototype.play = vi.fn().mockImplementation(() => Promise.resolve());
    window.HTMLMediaElement.prototype.pause = vi.fn().mockImplementation(() => {});
    window.HTMLMediaElement.prototype.load = vi.fn().mockImplementation(() => {});
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
  });

  const sampleTracks = [
    {
      id: 1,
      language_code: "hi-IN",
      language_name: "Hindi",
      audio_file: "https://example.com/audio-hi.mp3",
      status: "completed",
    },
    {
      id: 2,
      language_code: "ta-IN",
      language_name: "Tamil",
      audio_file: "https://example.com/audio-ta.mp3",
      status: "completed",
    },
  ];

  it("renders video element with given source and default English audio option", () => {
    const { container, unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/test-video.mp4"
        title="Lesson 1: Introduction"
        translatedAudios={sampleTracks}
      />
    );

    const video = container.querySelector("video");
    expect(video).toBeDefined();
    expect(video?.getAttribute("src")).toBe("https://example.com/test-video.mp4");
    expect(screen.getByText("English")).toBeDefined();
    expect(screen.getByText("1x")).toBeDefined();
    unmount();
  });

  it("toggles playback speed and applies rate to video", () => {
    const { container, unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/test-video.mp4"
        translatedAudios={sampleTracks}
      />
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    const speedButton = screen.getByRole("button", { name: /playback speed menu/i });
    fireEvent.click(speedButton);

    // Click 1.5x option
    const speed15x = screen.getByText("1.5x");
    fireEvent.click(speed15x);

    expect(video.playbackRate).toBe(1.5);
    expect(screen.getAllByText("1.5x").length).toBeGreaterThan(0);
    unmount();
  });

  it("provides 10-second seek backward and forward controls", () => {
    const { container, unmount } = render(
      <WebVideoPlayer videoUrl="https://example.com/test-video.mp4" />
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    let curTime = 30;
    Object.defineProperty(video, "duration", { value: 120, writable: true });
    Object.defineProperty(video, "currentTime", {
      get: () => curTime,
      set: (val: number) => {
        curTime = val;
      },
      configurable: true,
    });

    const rewindBtn = container.querySelector('button[aria-label="Seek backward 10 seconds"]') as HTMLButtonElement;
    const forwardBtn = container.querySelector('button[aria-label="Seek forward 10 seconds"]') as HTMLButtonElement;

    fireEvent.click(rewindBtn);
    expect(video.currentTime).toBe(20);

    fireEvent.click(forwardBtn);
    expect(video.currentTime).toBe(30);
    unmount();
  });

  it("opens audio track selector and switches language", () => {
    const { unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/test-video.mp4"
        translatedAudios={sampleTracks}
      />
    );

    const audioMenuBtn = screen.getByRole("button", { name: /audio language menu/i });
    fireEvent.click(audioMenuBtn);

    expect(screen.getByText("English (Original)")).toBeDefined();
    expect(screen.getByText("Hindi")).toBeDefined();
    expect(screen.getByText("Tamil")).toBeDefined();

    // Select Hindi
    fireEvent.click(screen.getByText("Hindi"));
    expect(screen.getAllByText("Hindi").length).toBeGreaterThan(0);
    unmount();
  });

  it("ignores keyboard shortcuts when focus is in an input or textarea", () => {
    const { container, unmount } = render(
      <div>
        <input data-testid="chat-input" type="text" />
        <WebVideoPlayer videoUrl="https://example.com/test-video.mp4" />
      </div>
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(video, "duration", { value: 100, writable: true });
    video.currentTime = 25;

    const input = screen.getByTestId("chat-input");
    input.focus();

    // Fire spacebar in input
    fireEvent.keyDown(input, { key: " ", code: "Space" });
    // Video play/pause should not have been toggled
    expect(window.HTMLMediaElement.prototype.play).not.toHaveBeenCalled();

    // Fire ArrowLeft in input
    fireEvent.keyDown(input, { key: "ArrowLeft" });
    expect(video.currentTime).toBe(25); // Position did not change
    unmount();
  });

  it("renders moving watermark overlay with stable student id", () => {
    const { unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/test-video.mp4"
        watermark={{
          enabled: true,
          text: "NAT-99AA88",
          student_id: "NAT-99AA88",
          moving: true,
          interval_seconds: 15,
        }}
      />
    );

    const watermark = screen.getByTestId("student-watermark");
    expect(watermark).toBeDefined();
    expect(watermark.textContent).toContain("NAT-99AA88");
    expect(watermark.textContent).toContain("Natya LMS");
    unmount();
  });

  it("hides watermark when watermark is explicitly disabled", () => {
    const { unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/test-video.mp4"
        watermark={{
          enabled: false,
          text: "NAT-99AA88",
        }}
      />
    );

    expect(screen.queryByTestId("student-watermark")).toBeNull();
    unmount();
  });

  it("enforces nodownload, disablePictureInPicture, and suppresses contextmenu when download is disabled", () => {
    const { container, unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/test-video.mp4"
        downloadEnabled={false}
      />
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video.getAttribute("controlslist")).toBe("nodownload");
    expect(video.hasAttribute("disablepictureinpicture")).toBe(true);

    // Context menu events on both container and video should prevent default
    const playerContainer = container.querySelector("#web-video-player") as HTMLElement;
    const contextMenuContainerEvent = new MouseEvent("contextmenu", { cancelable: true, bubbles: true });
    playerContainer.dispatchEvent(contextMenuContainerEvent);
    expect(contextMenuContainerEvent.defaultPrevented).toBe(true);

    const contextMenuVideoEvent = new MouseEvent("contextmenu", { cancelable: true, bubbles: true });
    video.dispatchEvent(contextMenuVideoEvent);
    expect(contextMenuVideoEvent.defaultPrevented).toBe(true);
    unmount();
  });

  it("removes nodownload restriction when downloadEnabled is true", () => {
    const { container, unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/test-video.mp4"
        downloadEnabled={true}
      />
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video.getAttribute("controlslist")).toBeNull();
    expect(video.hasAttribute("disablepictureinpicture")).toBe(false);
    unmount();
  });

  it("proactively refreshes signed URLs before expiry and updates video src", async () => {
    const refreshMock = vi.fn().mockResolvedValue({
      videoUrl: "https://example.com/refreshed-video.mp4?exp=999",
      expiresIn: 900,
    });

    const { container, unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/initial-video.mp4?exp=111"
        expiresIn={180.05} // triggers delay = 0.05s (50ms)
        onRefreshUrls={refreshMock}
      />
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video.getAttribute("src")).toBe("https://example.com/initial-video.mp4?exp=111");

    await waitFor(
      () => {
        expect(refreshMock).toHaveBeenCalledTimes(1);
        expect(video.getAttribute("src")).toBe("https://example.com/refreshed-video.mp4?exp=999");
      },
      { timeout: 1500 }
    );
    unmount();
  });

  it("recovers from playback error by invoking refresh without duplicate concurrent calls", async () => {
    let callCount = 0;
    const refreshMock = vi.fn().mockImplementation(async () => {
      callCount++;
      return {
        videoUrl: "https://example.com/error-recovered-video.mp4",
        expiresIn: 900,
      };
    });

    const { container, unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/initial-video.mp4"
        expiresIn={900}
        onRefreshUrls={refreshMock}
      />
    );

    const video = container.querySelector("video") as HTMLVideoElement;

    // Trigger video error event twice rapidly
    fireEvent.error(video);
    fireEvent.error(video);

    await waitFor(
      () => {
        expect(refreshMock).toHaveBeenCalledTimes(1);
        expect(video.getAttribute("src")).toBe("https://example.com/error-recovered-video.mp4");
      },
      { timeout: 1500 }
    );
    unmount();
  });

  it("handles revoked authorization on refresh and halts further retries", async () => {
    const authRevokedMock = vi.fn();
    const refreshMock = vi.fn().mockResolvedValue(null); // null indicates 403 / revoked

    const { container, unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/initial-video.mp4"
        expiresIn={900}
        onRefreshUrls={refreshMock}
        onAuthRevoked={authRevokedMock}
      />
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    fireEvent.error(video);

    await waitFor(
      () => {
        expect(refreshMock).toHaveBeenCalledTimes(1);
        expect(authRevokedMock).toHaveBeenCalledWith("Access revoked");
        expect(screen.getByText(/Your access to this video has expired or was revoked/i)).toBeDefined();
      },
      { timeout: 1500 }
    );
    unmount();
  });

  it("preserves playback position and play state across refreshed video URL", async () => {
    const refreshMock = vi.fn().mockResolvedValue({
      videoUrl: "https://example.com/refreshed-video.mp4",
      expiresIn: 900,
    });

    const { container, unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/initial-video.mp4"
        expiresIn={180.04}
        onRefreshUrls={refreshMock}
      />
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    let curTime = 42;
    Object.defineProperty(video, "currentTime", {
      get: () => curTime,
      set: (val: number) => {
        curTime = val;
      },
      configurable: true,
    });

    await waitFor(
      () => {
        expect(refreshMock).toHaveBeenCalledTimes(1);
      },
      { timeout: 1500 }
    );

    // Simulate loadedmetadata event on the refreshed video to trigger position restoration
    fireEvent.loadedMetadata(video);

    expect(video.currentTime).toBe(42);
    unmount();
  });

  it("refreshes dubbed audio track URLs without resetting selected language", async () => {
    const initialAudios = [
      {
        id: 1,
        language_code: "hi-IN",
        language_name: "Hindi",
        audio_file: "https://example.com/initial-hi.mp3?exp=111",
        status: "completed",
      },
    ];

    const refreshMock = vi.fn().mockResolvedValue({
      videoUrl: "https://example.com/refreshed-video.mp4",
      expiresIn: 900,
      translatedAudios: [
        {
          id: 1,
          language_code: "hi-IN",
          language_name: "Hindi",
          audio_file: "https://example.com/refreshed-hi.mp3?exp=999",
          status: "completed",
        },
      ],
    });

    const { container, unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/initial-video.mp4"
        translatedAudios={initialAudios}
        expiresIn={180.05}
        onRefreshUrls={refreshMock}
      />
    );

    // Switch to Hindi
    const audioMenuBtn = screen.getByRole("button", { name: /audio language menu/i });
    fireEvent.click(audioMenuBtn);
    fireEvent.click(screen.getByText("Hindi"));

    const audio = container.querySelector("audio") as HTMLAudioElement;
    expect(audio).toBeDefined();

    await waitFor(
      () => {
        expect(refreshMock).toHaveBeenCalledTimes(1);
        expect(audio.getAttribute("src")).toBe("https://example.com/refreshed-hi.mp3?exp=999");
      },
      { timeout: 1500 }
    );
    unmount();
  });

  it("cancels proactive refresh timers on unmount", async () => {
    const refreshMock = vi.fn().mockResolvedValue({
      videoUrl: "https://example.com/refreshed.mp4",
      expiresIn: 900,
    });

    const { unmount } = render(
      <WebVideoPlayer
        videoUrl="https://example.com/initial-video.mp4"
        expiresIn={180.05}
        onRefreshUrls={refreshMock}
      />
    );

    // Unmount before 50ms expires
    unmount();

    // Wait past the timer duration
    await new Promise((resolve) => setTimeout(resolve, 80));

    expect(refreshMock).not.toHaveBeenCalled();
  });
});
