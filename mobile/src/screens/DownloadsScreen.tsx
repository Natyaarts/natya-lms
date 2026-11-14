import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, StyleSheet, FlatList, TouchableOpacity, Alert, ActivityIndicator, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from '../components/Icon';
import EmptyState from '../components/EmptyState';
import {
  getAllDownloadedLessons,
  deleteDownloadedLesson,
  getTotalOfflineStorageUsed,
  formatBytes,
  syncOfflineProgress,
  OfflineLessonMeta,
} from '../services/offlineStorage';
import { colors, spacing, radius, typography, headerLayout } from '../theme';

interface GroupedCourse {
  courseId: number;
  courseTitle: string;
  totalSize: number;
  lessons: OfflineLessonMeta[];
}

export default function DownloadsScreen({ navigation }: any) {
  const [lessons, setLessons] = useState<OfflineLessonMeta[]>([]);
  const [totalStorage, setTotalStorage] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);

  const loadDownloads = useCallback(async () => {
    try {
      // Sync any pending watch progress in background
      syncOfflineProgress().catch(() => {});
      const [list, storage] = await Promise.all([
        getAllDownloadedLessons(),
        getTotalOfflineStorageUsed(),
      ]);
      setLessons(list);
      setTotalStorage(storage);
    } catch (err) {
      console.warn('Failed to load offline downloads:', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadDownloads();
  }, [loadDownloads]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    loadDownloads();
  }, [loadDownloads]);

  const handleDelete = (lesson: OfflineLessonMeta) => {
    Alert.alert(
      'Delete Download',
      `Delete "${lesson.lessonTitle}" from offline storage?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            try {
              await deleteDownloadedLesson(lesson.courseId, lesson.lessonId);
              await loadDownloads();
            } catch (err) {
              Alert.alert('Error', 'Could not delete the downloaded lesson.');
            }
          },
        },
      ]
    );
  };

  const handlePlay = (lesson: OfflineLessonMeta) => {
    navigation.navigate('Learn', {
      courseId: lesson.courseId,
      initialLessonId: lesson.lessonId,
      isEnrolled: true,
      isOffline: true,
    });
  };

  // Group lessons by course
  const groupedCourses: GroupedCourse[] = React.useMemo(() => {
    const map = new Map<number, GroupedCourse>();
    for (const item of lessons) {
      const existing = map.get(item.courseId);
      if (existing) {
        existing.lessons.push(item);
        existing.totalSize += item.fileSize || 0;
      } else {
        map.set(item.courseId, {
          courseId: item.courseId,
          courseTitle: item.courseTitle || 'Course',
          totalSize: item.fileSize || 0,
          lessons: [item],
        });
      }
    }
    return Array.from(map.values());
  }, [lessons]);

  if (loading) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top', 'left', 'right', 'bottom']}>
      <View style={styles.header}>
        <TouchableOpacity
          onPress={() => navigation.goBack()}
          style={styles.backButton}
          accessibilityRole="button"
          accessibilityLabel="Go back"
        >
          <Icon name="arrow-left" size={20} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Offline Downloads</Text>
        <View style={{ width: 28 }} />
      </View>

      {lessons.length > 0 && (
        <View style={styles.storageCard}>
          <View style={styles.storageRow}>
            <View style={styles.storageIconWrapper}>
              <Icon name="hard-drive" size={16} color={colors.accent} />
            </View>
            <View style={styles.storageInfo}>
              <Text style={styles.storageTitle}>Offline Storage</Text>
              <Text style={styles.storageSubtitle}>
                {lessons.length} {lessons.length === 1 ? 'lesson' : 'lessons'} available offline ({formatBytes(totalStorage)})
              </Text>
            </View>
          </View>
        </View>
      )}

      {lessons.length === 0 ? (
        <View style={styles.emptyContainer}>
          <EmptyState
            icon="download"
            title="No offline downloads"
            message="Lessons you download will be saved here so you can watch them anytime, even in airplane mode."
            ctaLabel="Go to My Courses"
            onPressCta={() => navigation.navigate('My Learning')}
          />
        </View>
      ) : (
        <FlatList
          data={groupedCourses}
          keyExtractor={(item) => item.courseId.toString()}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
          contentContainerStyle={styles.listContent}
          renderItem={({ item: course }) => (
            <View style={styles.courseGroup}>
              <View style={styles.courseHeader}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.courseTitle} numberOfLines={1}>{course.courseTitle}</Text>
                  <Text style={styles.courseMeta}>
                    {course.lessons.length} {course.lessons.length === 1 ? 'lesson' : 'lessons'} · {formatBytes(course.totalSize)}
                  </Text>
                </View>
              </View>

              {course.lessons.map((lesson, idx) => (
                <View key={lesson.lessonId} style={[styles.lessonRow, idx === course.lessons.length - 1 && styles.lessonRowLast]}>
                  <TouchableOpacity
                    style={styles.lessonInfoTouch}
                    onPress={() => handlePlay(lesson)}
                    activeOpacity={0.7}
                  >
                    <View style={styles.playIconBox}>
                      <Icon name="play" size={14} color={colors.accent} />
                    </View>
                    <View style={{ flex: 1, marginRight: spacing.md }}>
                      <Text style={styles.lessonTitle} numberOfLines={1}>
                        {lesson.lessonTitle}
                      </Text>
                      <Text style={styles.lessonSubtext}>
                        {lesson.moduleTitle ? `${lesson.moduleTitle} · ` : ''}{formatBytes(lesson.fileSize)}
                      </Text>
                    </View>
                  </TouchableOpacity>

                  <TouchableOpacity
                    style={styles.deleteButton}
                    onPress={() => handleDelete(lesson)}
                    accessibilityRole="button"
                    accessibilityLabel={`Delete ${lesson.lessonTitle}`}
                  >
                    <Icon name="trash-2" size={16} color={colors.danger} />
                  </TouchableOpacity>
                </View>
              ))}
            </View>
          )}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { justifyContent: 'center', alignItems: 'center' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingTop: headerLayout.compactHeaderPaddingTop,
    paddingBottom: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  backButton: { width: 36, height: 36, justifyContent: 'center', alignItems: 'flex-start' },
  headerTitle: { ...typography.section, textAlign: 'center' },
  storageCard: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.md,
    marginBottom: spacing.sm,
    backgroundColor: colors.card,
    borderRadius: radius.md,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  storageRow: { flexDirection: 'row', alignItems: 'center' },
  storageIconWrapper: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.accentMuted,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: spacing.md,
  },
  storageInfo: { flex: 1 },
  storageTitle: { color: colors.text, fontSize: 14, fontWeight: '600' },
  storageSubtitle: { color: colors.textSecondary, fontSize: 12, marginTop: 2 },
  emptyContainer: { flex: 1, justifyContent: 'center', padding: spacing.xxl },
  listContent: { padding: spacing.lg, paddingTop: spacing.sm },
  courseGroup: {
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: spacing.lg,
    overflow: 'hidden',
  },
  courseHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: spacing.md,
    backgroundColor: colors.cardAlt,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  courseTitle: { color: colors.text, fontSize: 15, fontWeight: '700' },
  courseMeta: { color: colors.textSecondary, fontSize: 12, marginTop: 2 },
  lessonRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  lessonRowLast: { borderBottomWidth: 0 },
  lessonInfoTouch: { flex: 1, flexDirection: 'row', alignItems: 'center' },
  playIconBox: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: colors.accentMuted,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: spacing.md,
  },
  lessonTitle: { color: colors.text, fontSize: 14, fontWeight: '500' },
  lessonSubtext: { color: colors.textTertiary, fontSize: 12, marginTop: 2 },
  deleteButton: {
    padding: spacing.sm,
    justifyContent: 'center',
    alignItems: 'center',
  },
});

