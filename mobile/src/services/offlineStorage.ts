import * as FileSystem from 'expo-file-system/legacy';
import AsyncStorage from '@react-native-async-storage/async-storage';
import client from '../api/client';

export interface OfflineWatermark {
  enabled?: boolean;
  text?: string;
  student_id?: string;
  moving?: boolean;
  interval_seconds?: number;
}

export interface OfflineProtection {
  watermark_enabled?: boolean;
  download_enabled?: boolean;
  screen_capture_protection_enabled?: boolean;
}

export interface OfflineLessonMeta {
  lessonId: number;
  courseId: number;
  courseTitle: string;
  lessonTitle: string;
  moduleTitle?: string;
  moduleIndex?: number;
  lessonIndex?: number;
  localUri: string;
  fileSize: number;
  downloadedAt: string;
  duration?: number;
  watermark?: OfflineWatermark;
  protection?: OfflineProtection;
}

export interface OfflineProgressItem {
  lessonId: number;
  position: number;
  duration: number;
  completed: boolean;
  timestamp: number;
}

const OFFLINE_INDEX_KEY = '@offline_lesson_index';
const OFFLINE_PROGRESS_QUEUE_KEY = '@offline_progress_queue';
const MIN_FREE_DISK_BYTES = 250 * 1024 * 1024; // 250 MB minimum free space

// Active in-memory resumable downloads map
const activeDownloads = new Map<number, FileSystem.DownloadResumable>();

function getLessonStorageKey(lessonId: number): string {
  return `@offline_lesson_${lessonId}`;
}

export function getDownloadsBaseDir(): string {
  const docDir = FileSystem.documentDirectory || '';
  return `${docDir}downloads/`;
}

export function getLessonFilePath(courseId: number, lessonId: number): string {
  return `${getDownloadsBaseDir()}${courseId}/${lessonId}.mp4`;
}

export async function ensureCourseDir(courseId: number): Promise<string> {
  const courseDir = `${getDownloadsBaseDir()}${courseId}/`;
  const info = await FileSystem.getInfoAsync(courseDir);
  if (!info.exists) {
    await FileSystem.makeDirectoryAsync(courseDir, { intermediates: true });
  }
  return courseDir;
}

export async function getDownloadedLesson(lessonId: number): Promise<OfflineLessonMeta | null> {
  try {
    const raw = await AsyncStorage.getItem(getLessonStorageKey(lessonId));
    if (!raw) return null;
    const meta: OfflineLessonMeta = JSON.parse(raw);
    // Verify file actually exists on disk (e.g. not cleared by OS)
    const fileInfo = await FileSystem.getInfoAsync(meta.localUri);
    if (!fileInfo.exists) {
      // Clean up orphaned metadata
      await cleanupOrphanedRecord(meta.courseId, lessonId);
      return null;
    }
    return meta;
  } catch (err) {
    console.warn(`Error reading offline lesson metadata for ${lessonId}:`, err);
    return null;
  }
}

export async function isLessonDownloaded(lessonId: number): Promise<boolean> {
  const lesson = await getDownloadedLesson(lessonId);
  return lesson !== null;
}

export function isLessonDownloading(lessonId: number): boolean {
  return activeDownloads.has(lessonId);
}

export async function getAllDownloadedLessons(): Promise<OfflineLessonMeta[]> {
  try {
    const rawIndex = await AsyncStorage.getItem(OFFLINE_INDEX_KEY);
    if (!rawIndex) return [];
    const index: number[] = JSON.parse(rawIndex);
    const results: OfflineLessonMeta[] = [];
    const validIds: number[] = [];

    for (const id of index) {
      const lesson = await getDownloadedLesson(id);
      if (lesson) {
        results.push(lesson);
        validIds.push(id);
      }
    }

    if (validIds.length !== index.length) {
      await AsyncStorage.setItem(OFFLINE_INDEX_KEY, JSON.stringify(validIds));
    }

    return results;
  } catch (err) {
    console.warn('Error fetching all downloaded lessons:', err);
    return [];
  }
}

export async function getDownloadedLessonsForCourse(courseId: number): Promise<OfflineLessonMeta[]> {
  const all = await getAllDownloadedLessons();
  return all.filter((l) => l.courseId === courseId);
}

export async function startLessonDownload(
  params: {
    courseId: number;
    lessonId: number;
    courseTitle: string;
    lessonTitle: string;
    moduleTitle?: string;
    moduleIndex?: number;
    lessonIndex?: number;
    videoUrl: string;
    duration?: number;
    watermark?: OfflineWatermark;
    protection?: OfflineProtection;
  },
  onProgress?: (progress: number) => void
): Promise<OfflineLessonMeta> {
  const { courseId, lessonId, courseTitle, lessonTitle, moduleTitle, moduleIndex, lessonIndex, videoUrl, duration, watermark, protection } = params;

  if (activeDownloads.has(lessonId)) {
    throw new Error('Download is already in progress for this lesson.');
  }

  // 1. Check free disk storage
  try {
    const freeSpace = await FileSystem.getFreeDiskStorageAsync();
    if (freeSpace < MIN_FREE_DISK_BYTES) {
      throw new Error(`Insufficient storage. At least 250 MB free disk space required (current: ${formatBytes(freeSpace)}).`);
    }
  } catch (err: any) {
    if (err.message?.includes('Insufficient storage')) throw err;
    console.warn('Could not query free disk space, proceeding with download attempt:', err);
  }

  // 2. Prepare directory
  await ensureCourseDir(courseId);
  const targetPath = getLessonFilePath(courseId, lessonId);

  // 3. Setup resumable download
  const downloadResumable = FileSystem.createDownloadResumable(
    videoUrl,
    targetPath,
    {},
    (downloadProgress) => {
      if (downloadProgress.totalBytesExpectedToWrite > 0) {
        const progress = downloadProgress.totalBytesWritten / downloadProgress.totalBytesExpectedToWrite;
        onProgress?.(Math.min(1, Math.max(0, progress)));
      }
    }
  );

  activeDownloads.set(lessonId, downloadResumable);

  try {
    const result = await downloadResumable.downloadAsync();
    activeDownloads.delete(lessonId);

    if (!result || !result.uri) {
      throw new Error('Download was interrupted or cancelled.');
    }

    const fileInfo = await FileSystem.getInfoAsync(result.uri);
    const fileSize = fileInfo.exists && 'size' in fileInfo ? fileInfo.size : 0;

    const meta: OfflineLessonMeta = {
      lessonId,
      courseId,
      courseTitle,
      lessonTitle,
      moduleTitle,
      moduleIndex,
      lessonIndex,
      localUri: result.uri,
      fileSize,
      downloadedAt: new Date().toISOString(),
      duration,
      watermark,
      protection,
    };

    // Save metadata
    await AsyncStorage.setItem(getLessonStorageKey(lessonId), JSON.stringify(meta));

    // Update index
    const rawIndex = await AsyncStorage.getItem(OFFLINE_INDEX_KEY);
    const index: number[] = rawIndex ? JSON.parse(rawIndex) : [];
    if (!index.includes(lessonId)) {
      index.push(lessonId);
      await AsyncStorage.setItem(OFFLINE_INDEX_KEY, JSON.stringify(index));
    }

    return meta;
  } catch (err) {
    activeDownloads.delete(lessonId);
    // Cleanup incomplete file
    try {
      await FileSystem.deleteAsync(targetPath, { idempotent: true });
    } catch (_) {}
    throw err;
  }
}

export async function cancelLessonDownload(lessonId: number): Promise<void> {
  const download = activeDownloads.get(lessonId);
  if (download) {
    try {
      await download.pauseAsync();
    } catch (_) {}
    activeDownloads.delete(lessonId);
  }
}

export async function deleteDownloadedLesson(courseId: number, lessonId: number): Promise<void> {
  await cancelLessonDownload(lessonId);

  const targetPath = getLessonFilePath(courseId, lessonId);
  try {
    await FileSystem.deleteAsync(targetPath, { idempotent: true });
  } catch (err) {
    console.warn(`Could not delete file ${targetPath}:`, err);
  }

  await cleanupOrphanedRecord(courseId, lessonId);
}

async function cleanupOrphanedRecord(courseId: number, lessonId: number): Promise<void> {
  await AsyncStorage.removeItem(getLessonStorageKey(lessonId));

  const rawIndex = await AsyncStorage.getItem(OFFLINE_INDEX_KEY);
  if (rawIndex) {
    const index: number[] = JSON.parse(rawIndex);
    const updated = index.filter((id) => id !== lessonId);
    await AsyncStorage.setItem(OFFLINE_INDEX_KEY, JSON.stringify(updated));
  }
}

export async function getTotalOfflineStorageUsed(): Promise<number> {
  const lessons = await getAllDownloadedLessons();
  return lessons.reduce((sum, item) => sum + (item.fileSize || 0), 0);
}

export async function queueOfflineProgress(item: Omit<OfflineProgressItem, 'timestamp'>): Promise<void> {
  try {
    const raw = await AsyncStorage.getItem(OFFLINE_PROGRESS_QUEUE_KEY);
    const queue: OfflineProgressItem[] = raw ? JSON.parse(raw) : [];

    // Replace existing entry for same lesson if present, or append
    const existingIdx = queue.findIndex((q) => q.lessonId === item.lessonId);
    const progressEntry: OfflineProgressItem = {
      ...item,
      timestamp: Date.now(),
    };

    if (existingIdx >= 0) {
      queue[existingIdx] = progressEntry;
    } else {
      queue.push(progressEntry);
    }

    await AsyncStorage.setItem(OFFLINE_PROGRESS_QUEUE_KEY, JSON.stringify(queue));
  } catch (err) {
    console.warn('Failed to queue offline progress:', err);
  }
}

export async function syncOfflineProgress(): Promise<void> {
  try {
    const raw = await AsyncStorage.getItem(OFFLINE_PROGRESS_QUEUE_KEY);
    if (!raw) return;
    const queue: OfflineProgressItem[] = JSON.parse(raw);
    if (!queue.length) return;

    const remaining: OfflineProgressItem[] = [];
    for (const item of queue) {
      try {
        await client.post(`courses/lessons/${item.lessonId}/progress/`, {
          last_watched_position: item.position,
          video_duration: item.duration,
          completed: item.completed,
        });
      } catch (err: any) {
        // Keep in queue if network error or timeout
        if (!err.response || err.message === 'Network Error' || err.code === 'ECONNABORTED') {
          remaining.push(item);
        }
        // Non-network errors (e.g. 400/404) are dropped so we don't block subsequent syncs
      }
    }
    await AsyncStorage.setItem(OFFLINE_PROGRESS_QUEUE_KEY, JSON.stringify(remaining));
  } catch (err) {
    console.warn('Failed to sync offline progress:', err);
  }
}

export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

