import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import WebVideoPlayer from "./WebVideoPlayer";

describe("WebVideoPlayer Component", () => {
  beforeEach(() => {
    // Mock HTMLMediaElement methods
    window.HTMLMediaElement.prototype.play = vi.fn().mockImplementation(() => Promise.resolve());
    window.HTMLMediaElement.prototype.pause = vi.fn().mockImplementation(() => {});
    window.HTMLMediaElement.prototype.load = vi.fn().mockImplementation(() => {});
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
    const { container } = render(
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
  });

  it("toggles playback speed and applies rate to video", () => {
    const { container } = render(
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
  });

  it("provides 10-second seek backward and forward controls", () => {
    const { container } = render(
      <WebVideoPlayer videoUrl="https://example.com/test-video.mp4" />
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(video, "duration", { value: 120, writable: true });
    video.currentTime = 30;

    const rewindBtn = screen.getByRole("button", { name: /seek backward 10 seconds/i });
    const forwardBtn = screen.getByRole("button", { name: /seek forward 10 seconds/i });

    fireEvent.click(rewindBtn);
    expect(video.currentTime).toBe(20);

    fireEvent.click(forwardBtn);
    expect(video.currentTime).toBe(30);
  });

  it("opens audio track selector and switches language", () => {
    render(
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
  });

  it("ignores keyboard shortcuts when focus is in an input or textarea", () => {
    const { container } = render(
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
  });

  it("handles video playback errors gracefully with retry button", () => {
    const { container } = render(
      <WebVideoPlayer videoUrl="https://example.com/broken-video.mp4" />
    );

    const video = container.querySelector("video") as HTMLVideoElement;
    fireEvent.error(video);

    expect(screen.getByText("Playback Error")).toBeDefined();
    const retryBtn = screen.getByRole("button", { name: /retry playback/i });
    expect(retryBtn).toBeDefined();

    fireEvent.click(retryBtn);
    expect(window.HTMLMediaElement.prototype.load).toHaveBeenCalled();
  });
});
