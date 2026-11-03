import { describe, it, expect } from "vitest";
import {
  calculateAttendancePercentage,
  getSuggestedAttendanceStatus,
  getEffectiveThreshold,
  validateThresholdPercentage,
  calculateSessionsIntervalUnionSeconds,
} from "./attendanceSettings";

describe("Attendance Percentage Settings Calculations", () => {
  it("verifies 10-minute class with 75% threshold boundary cases (7m29s, 7m30s, 8m)", () => {
    // 10-minute class = 600s, 75% threshold
    // 7m 29s = 449s -> 74.8% -> below threshold -> LATE
    const pct7m29 = calculateAttendancePercentage(7 * 60 + 29, 10);
    expect(pct7m29).toBe(74.8);
    expect(getSuggestedAttendanceStatus(pct7m29, 75)).toBe("LATE");

    // 7m 30s = 450s -> 75.0% -> exactly qualifies -> PRESENT
    const pct7m30 = calculateAttendancePercentage(7 * 60 + 30, 10);
    expect(pct7m30).toBe(75.0);
    expect(getSuggestedAttendanceStatus(pct7m30, 75)).toBe("PRESENT");

    // 8m 00s = 480s -> 80.0% -> qualifies -> PRESENT
    const pct8m = calculateAttendancePercentage(8 * 60, 10);
    expect(pct8m).toBe(80.0);
    expect(getSuggestedAttendanceStatus(pct8m, 75)).toBe("PRESENT");
  });

  it("caps displayed percentage at 100% when attendance exceeds class duration (12m in 10m class)", () => {
    // 10-minute class, 12 minutes attended (720s)
    const pct = calculateAttendancePercentage(12 * 60, 10);
    expect(pct).toBe(100.0);
    expect(getSuggestedAttendanceStatus(pct, 75)).toBe("PRESENT");
  });

  it("calculates union of overlapping intervals without double counting", () => {
    const base = new Date("2026-11-03T10:00:00Z");
    // Session 1: 10:00 to 10:08 (8 minutes = 480s)
    // Session 2: 10:04 to 10:10 (6 minutes = 360s, overlapping by 4m)
    // Union should be 10:00 to 10:10 = 10 minutes = 600s (not 840s)
    const sessions = [
      {
        join_time: new Date(base.getTime()),
        leave_time: new Date(base.getTime() + 8 * 60 * 1000),
      },
      {
        join_time: new Date(base.getTime() + 4 * 60 * 1000),
        leave_time: new Date(base.getTime() + 10 * 60 * 1000),
      },
    ];
    const unionSec = calculateSessionsIntervalUnionSeconds(sessions);
    expect(unionSec).toBe(600);
  });

  it("handles duplicate sessions with interval union", () => {
    const base = new Date("2026-11-03T10:00:00Z");
    // Session 1: 10:00 to 10:10 (600s)
    // Session 2: 10:00 to 10:10 (600s duplicate)
    // Union should be 600s
    const sessions = [
      {
        join_time: new Date(base.getTime()),
        leave_time: new Date(base.getTime() + 10 * 60 * 1000),
      },
      {
        join_time: new Date(base.getTime()),
        leave_time: new Date(base.getTime() + 10 * 60 * 1000),
      },
    ];
    const unionSec = calculateSessionsIntervalUnionSeconds(sessions);
    expect(unionSec).toBe(600);
  });

  it("calculates attendance percentage using prompt example (10m class, 8m attended = 80%)", () => {
    const pct = calculateAttendancePercentage(8 * 60, 10);
    expect(pct).toBe(80.0);
  });

  it("evaluates suggested status for prompt example (80% attendance vs 75% threshold = PRESENT)", () => {
    const status = getSuggestedAttendanceStatus(80.0, 75);
    expect(status).toBe("PRESENT");
  });

  it("evaluates LATE status when attendance is positive but below threshold (50% vs 75% = LATE)", () => {
    const status = getSuggestedAttendanceStatus(50.0, 75);
    expect(status).toBe("LATE");
  });

  it("evaluates ABSENT status when attendance is 0%", () => {
    const status = getSuggestedAttendanceStatus(0.0, 75);
    expect(status).toBe("ABSENT");
  });

  it("aggregates multiple Zoom sessions properly (3m + 5m = 8m / 10m = 80%)", () => {
    const session1Sec = 3 * 60; // 180s
    const session2Sec = 5 * 60; // 300s
    const totalSec = session1Sec + session2Sec;
    const pct = calculateAttendancePercentage(totalSec, 10);
    expect(pct).toBe(80.0);
    expect(getSuggestedAttendanceStatus(pct, 75)).toBe("PRESENT");
  });

  it("strictly uses configured scheduled class duration as denominator, never wall-clock time", () => {
    // 60 minute class, student attended 45 minutes (2700s) -> 75%
    const pct = calculateAttendancePercentage(2700, 60);
    expect(pct).toBe(75.0);
  });

  it("safely handles 0 or missing scheduled duration without division by zero", () => {
    const pct = calculateAttendancePercentage(600, 0);
    expect(pct).toBeDefined();
    expect(isFinite(pct)).toBe(true);
  });

  it("correctly resolves effective threshold with overrides and global fallback", () => {
    // Inherits global default (75) when class override is null/undefined
    expect(getEffectiveThreshold(null, 75)).toBe(75);
    expect(getEffectiveThreshold(undefined, 75)).toBe(75);

    // Uses class override when valid (e.g. 90)
    expect(getEffectiveThreshold(90, 75)).toBe(90);
    expect(getEffectiveThreshold(50, 75)).toBe(50);
  });

  it("validates percentages between 1 and 100", () => {
    expect(validateThresholdPercentage(75).isValid).toBe(true);
    expect(validateThresholdPercentage("80").isValid).toBe(true);
    expect(validateThresholdPercentage(1).isValid).toBe(true);
    expect(validateThresholdPercentage(100).isValid).toBe(true);

    expect(validateThresholdPercentage(0).isValid).toBe(false);
    expect(validateThresholdPercentage(101).isValid).toBe(false);
    expect(validateThresholdPercentage(-5).isValid).toBe(false);
    expect(validateThresholdPercentage("abc").isValid).toBe(false);
    expect(validateThresholdPercentage(null).isValid).toBe(false);
  });
});

