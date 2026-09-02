"""
Best-effort duration comparison between a lesson's original video and a
manually-uploaded translated audio track.

Translated audio is produced externally (AI service, human dubbing artist,
studio, etc.), so the LMS can't guarantee it lines up perfectly with the
original video. This module gives admins a heads-up when the two durations
look suspiciously different, without ever blocking the upload -- every
function here is designed to fail silently (return None) rather than raise,
since this is an advisory convenience, not a correctness requirement.

Implementation note: duration is probed via `ffprobe` (ships alongside the
`ffmpeg` binary this project already requires for AI dubbing -- see
services/ai_translator.py) reading only the file's metadata/headers, so it
stays cheap even for large video files and doesn't need any new dependency
or heavy infrastructure.
"""

import logging
import subprocess

from django.conf import settings

logger = logging.getLogger(__name__)

# Warn when durations differ by more than this many seconds, OR by more than
# DURATION_MISMATCH_RATIO of the video's length -- whichever is larger.
# Translated speech can legitimately run a bit shorter/longer than the
# original, so this is intentionally forgiving; it's meant to catch a
# clearly wrong/mismatched file, not flag every few seconds of drift.
DURATION_MISMATCH_MIN_SECONDS = 15
DURATION_MISMATCH_RATIO = 0.10
PROBE_TIMEOUT_SECONDS = 12


def _resolve_probe_url(file_field):
    """Best-effort absolute URL for a Django FileField, for ffprobe to read."""
    try:
        if not file_field:
            return None
        url = file_field.url
    except Exception:
        return None
    if url.startswith('/'):
        base = getattr(settings, 'MEDIA_PROBE_BASE_URL', 'http://localhost:8000')
        return f"{base}{url}"
    return url


def probe_duration_seconds(file_field):
    """
    Returns the duration (in seconds) of a Django FileField's underlying
    media file, or None if it can't be determined for any reason (missing
    file, ffprobe not installed, network error, timeout, etc). Never raises.
    """
    url = _resolve_probe_url(file_field)
    if not url:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                url,
            ],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.info(f"Duration probe skipped/failed for media file: {e}")
        return None


def format_mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def check_duration_mismatch(lesson, translated_audio):
    """
    Compares `lesson.video_file` duration against `translated_audio.audio_file`
    duration. Returns a human-readable warning string if they diverge beyond
    the threshold, or None if they match closely enough / can't be checked.
    Advisory only -- never raises, so it can never block an upload.
    """
    try:
        video_duration = probe_duration_seconds(lesson.video_file)
        audio_duration = probe_duration_seconds(translated_audio.audio_file)

        if not video_duration or not audio_duration:
            return None

        diff = abs(video_duration - audio_duration)
        threshold = max(DURATION_MISMATCH_MIN_SECONDS, video_duration * DURATION_MISMATCH_RATIO)

        if diff > threshold:
            return (
                f"Video is {format_mmss(video_duration)} but the uploaded audio is "
                f"{format_mmss(audio_duration)} (difference: {int(diff)}s). "
                f"Double-check this track is synced to the correct lesson."
            )
        return None
    except Exception as e:
        logger.info(f"Duration comparison skipped due to error: {e}")
        return None
