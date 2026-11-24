// Minimal WebVTT parser for historical-recording subtitles.
//
// expo-video can only show subtitle tracks embedded in the media, not a
// separate .vtt file, so the app parses the file itself and draws the active
// cue over the video. Pure functions, no dependencies.

export interface SubtitleCue {
  start: number; // seconds
  end: number; // seconds
  text: string;
}

const TIMING_RE =
  /^\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s+-->\s+((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})/;

function toSeconds(stamp: string): number {
  const [clock, frac = '0'] = stamp.replace(',', '.').split('.');
  const parts = clock.split(':').map(Number);
  const [h, m, s] = parts.length === 3 ? parts : [0, parts[0], parts[1]];
  return h * 3600 + m * 60 + s + Number(`0.${frac}`);
}

function cleanText(raw: string): string {
  return raw
    .replace(/<[^>]*>/g, '') // <v Speaker>, <i>, <c.class>, inline timestamps
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .trim();
}

export function parseVtt(input: string): SubtitleCue[] {
  const text = (input || '').replace(/^﻿/, '').replace(/\r\n?/g, '\n');
  const blocks = text.split(/\n{2,}/);
  const cues: SubtitleCue[] = [];
  for (const block of blocks) {
    const lines = block.split('\n').filter((l) => l.length > 0);
    if (lines.length === 0) continue;
    const first = lines[0].trim();
    if (first.startsWith('WEBVTT') || first.startsWith('NOTE') || first === 'STYLE' || first === 'REGION') continue;
    const timingIndex = lines.findIndex((l) => TIMING_RE.test(l));
    if (timingIndex === -1) continue;
    const m = TIMING_RE.exec(lines[timingIndex]);
    if (!m) continue;
    const start = toSeconds(m[1]);
    const end = toSeconds(m[2]);
    const body = cleanText(lines.slice(timingIndex + 1).join('\n'));
    if (!body || !(end > start)) continue;
    cues.push({ start, end, text: body });
  }
  return cues.sort((a, b) => a.start - b.start || a.end - b.end);
}

/** Text of the cue(s) showing at `time` (overlapping cues are joined), or ''. */
export function activeCueText(cues: SubtitleCue[], time: number): string {
  if (!cues.length || !(time >= 0)) return '';
  // Binary search: last cue starting at or before `time`.
  let lo = 0;
  let hi = cues.length - 1;
  let idx = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid].start <= time) {
      idx = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  const out: string[] = [];
  // Walk back over earlier cues that may still be on screen (overlaps).
  for (let i = idx; i >= 0 && i > idx - 8; i--) {
    if (time < cues[i].end) out.unshift(cues[i].text);
  }
  return out.join('\n');
}
