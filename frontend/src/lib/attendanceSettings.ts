/**
 * Zoom Attendance Percentage Settings & Evaluation Logic
 * Natya LMS
 */

export interface AttendanceMetrics {
  attendancePercentage: number;
  suggestedStatus: "PRESENT" | "LATE" | "ABSENT";
  effectiveThreshold: number;
  isOverride: boolean;
  scheduledDurationMinutes: number;
}

/**
 * Calculates attendance percentage strictly using configured scheduled class duration as denominator.
 * Formula: (aggregated Zoom duration in seconds / (scheduledMinutes * 60)) * 100
 *
 * @param attendedDurationSeconds Aggregated participation seconds across all student Zoom sessions
 * @param scheduledDurationMinutes Configured scheduled duration of the live class in minutes
 */
export function calculateAttendancePercentage(
  attendedDurationSeconds: number,
  scheduledDurationMinutes: number
): number {
  const schedSec = Math.max(1, (scheduledDurationMinutes || 60) * 60);
  const zoomSec = Math.max(0, attendedDurationSeconds || 0);
  const rawPct = (zoomSec / schedSec) * 100.0;
  // Displayed percentage must not exceed 100% even if attended time exceeds scheduled duration
  return Math.min(100.0, Math.round(rawPct * 10) / 10);
}

export interface SessionInterval {
  join_time: string | Date;
  leave_time?: string | Date | null;
  duration_seconds?: number;
  is_active?: boolean;
}

/**
 * Calculates the union of attended time intervals across multiple sessions without double counting.
 */
export function calculateSessionsIntervalUnionSeconds(
  sessions: SessionInterval[],
  now: Date = new Date()
): number {
  if (!sessions || sessions.length === 0) return 0;

  const intervals: Array<{ start: number; end: number }> = [];

  for (const s of sessions) {
    if (!s.join_time) continue;
    const start = new Date(s.join_time).getTime();
    if (isNaN(start)) continue;

    let end: number;
    if (s.leave_time) {
      end = new Date(s.leave_time).getTime();
    } else if (s.is_active) {
      end = now.getTime();
    } else if (s.duration_seconds && s.duration_seconds > 0) {
      end = start + s.duration_seconds * 1000;
    } else {
      end = start;
    }

    if (end > start) {
      intervals.push({ start, end });
    }
  }

  if (intervals.length === 0) return 0;

  intervals.sort((a, b) => a.start - b.start);

  const merged: Array<{ start: number; end: number }> = [intervals[0]];
  for (let i = 1; i < intervals.length; i++) {
    const prev = merged[merged.length - 1];
    const curr = intervals[i];
    if (curr.start <= prev.end) {
      prev.end = Math.max(prev.end, curr.end);
    } else {
      merged.push({ start: curr.start, end: curr.end });
    }
  }

  const totalMs = merged.reduce((sum, intv) => sum + (intv.end - intv.start), 0);
  return Math.max(0, Math.round(totalMs / 1000));
}

/**
 * Evaluates suggested attendance status based on effective threshold.
 * - Percentage >= Threshold: PRESENT
 * - Percentage > 0 and < Threshold: LATE
 * - Percentage <= 0: ABSENT
 *
 * Note: The administrator retains 100% final authority. Suggestions are visual guides only.
 */
export function getSuggestedAttendanceStatus(
  attendancePercentage: number,
  effectiveThreshold: number
): "PRESENT" | "LATE" | "ABSENT" {
  if (attendancePercentage <= 0) {
    return "ABSENT";
  }
  if (attendancePercentage >= effectiveThreshold) {
    return "PRESENT";
  }
  return "LATE";
}

/**
 * Resolves effective threshold: returns class override if set, else global default.
 */
export function getEffectiveThreshold(
  classOverride: number | null | undefined,
  globalDefault: number = 75
): number {
  if (classOverride !== null && classOverride !== undefined && classOverride >= 1 && classOverride <= 100) {
    return classOverride;
  }
  return globalDefault;
}

/**
 * Validates whether an attendance percentage threshold is valid (integer between 1 and 100).
 */
export function validateThresholdPercentage(value: number | string | null | undefined): {
  isValid: boolean;
  error?: string;
  parsedValue?: number;
} {
  if (value === null || value === undefined || value === "") {
    return { isValid: false, error: "Percentage is required." };
  }
  const num = Number(value);
  if (isNaN(num) || !Number.isInteger(num)) {
    return { isValid: false, error: "Percentage must be a whole number." };
  }
  if (num < 1 || num > 100) {
    return { isValid: false, error: "Percentage must be between 1 and 100." };
  }
  return { isValid: true, parsedValue: num };
}

