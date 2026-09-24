import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, SafeAreaView, ActivityIndicator, ScrollView, Linking, Image, Alert } from 'react-native';
import client, { resolveMediaUrl } from '../api/client';
import CustomVideoPlayer, { AudioTrackOption } from '../components/CustomVideoPlayer';
import Icon from '../components/Icon';
import ProgressBar from '../components/ProgressBar';
import { colors, spacing, radius, typography } from '../theme';
import { usePreventScreenCapture } from 'expo-screen-capture';

const FALLBACK_THUMB = 'https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop';

// Assessment/assignment status -> badge label/color, reusing the exact
// status values the backend already computes (courses/serializers.py
// ModuleSerializer.get_assessments/get_assignments) -- never re-derived
// here, same principle the web frontend's learn page follows.
function assessmentStatusMeta(assessment: any) {
  switch (assessment.status) {
    case 'IN_PROGRESS': return { label: 'In Progress', color: colors.info };
    case 'PASSED': return { label: 'Passed', color: colors.success };
    case 'FAILED': return { label: 'Failed', color: colors.danger };
    case 'MAX_ATTEMPTS_REACHED': return { label: 'Max Attempts', color: colors.textTertiary };
    default: return { label: 'Not Started', color: colors.textTertiary };
  }
}

function assignmentStatusMeta(assignment: any) {
  switch (assignment.status) {
    case 'SUBMITTED': return { label: 'Submitted', color: colors.info };
    case 'GRADED': return { label: 'Graded', color: colors.success };
    case 'RETURNED_FOR_REVISION': return { label: 'Revision Needed', color: colors.warning };
    default: return { label: 'Not Submitted', color: colors.textTertiary };
  }
}

// Kept in sync with backend/courses/languages.py -- used as a fallback display
// name for legacy tracks that don't have language_name set.
const LANGUAGE_NAME_MAP: Record<string, string> = {
  ml: 'Malayalam', hi: 'Hindi', ta: 'Tamil', te: 'Telugu', kn: 'Kannada',
  bn: 'Bengali', mr: 'Marathi', gu: 'Gujarati', pa: 'Punjabi', ar: 'Arabic',
  fr: 'French', de: 'German', es: 'Spanish', pt: 'Portuguese', it: 'Italian',
  ja: 'Japanese', ko: 'Korean', zh: 'Chinese', ru: 'Russian',
};

export default function LearnScreen({ route, navigation }: any) {
  usePreventScreenCapture();
  const { courseId, isEnrolled } = route.params;
  const [course, setCourse] = useState<any>(null);
  const [activeLesson, setActiveLesson] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [marking, setMarking] = useState(false);
  const [savedPosition, setSavedPosition] = useState<number>(0);
  const activeProgressLessonIdRef = useRef<number | null>(null);

  // Phase 4.9: lesson completion + progress. Latest known playback position
  // for the CURRENTLY active lesson, kept in a ref (not state) so the
  // lesson-switch effect's cleanup can read the up-to-date value without
  // re-subscribing on every tick -- exactly the problem a ref solves that a
  // plain closed-over state variable wouldn't.
  const positionRef = useRef({ lessonId: null as number | null, position: 0, duration: 0 });
  const lastSavedRef = useRef(0);

  const fetchCourse = async () => {
    try {
      const res = await client.get(`courses/${courseId}/`);
      setCourse(res.data);
      if (isEnrolled && res.data.modules?.length > 0 && res.data.modules[0].lessons?.length > 0) {
        setActiveLesson(res.data.modules[0].lessons[0]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCourse();
  }, [courseId]);

  // Phase 2C: Fetch saved lesson progress when a lesson is opened or switched
  useEffect(() => {
    if (!activeLesson?.id) {
      setSavedPosition(0);
      lastSavedRef.current = 0;
      positionRef.current = { lessonId: null, position: 0, duration: 0 };
      return;
    }

    const lessonId = activeLesson.id;
    activeProgressLessonIdRef.current = lessonId;
    setSavedPosition(0);
    lastSavedRef.current = 0;
    positionRef.current = { lessonId, position: 0, duration: 0 };

    const fetchLessonProgress = async () => {
      try {
        const res = await client.get(`courses/lessons/${lessonId}/progress/`);
        // Guard against race conditions where the user switched lessons before response returned
        if (activeProgressLessonIdRef.current === lessonId && res.data) {
          const pos = typeof res.data.last_watched_position === 'number'
            ? res.data.last_watched_position
            : 0;
          setSavedPosition(pos);
          lastSavedRef.current = pos;
        }
      } catch (err) {
        if (activeProgressLessonIdRef.current === lessonId) {
          setSavedPosition(0);
        }
      }
    };

    fetchLessonProgress();
  }, [activeLesson?.id]);

  const saveProgress = async (lessonId: number, position: number, duration: number, completed: boolean) => {
    try {
      await client.post(`courses/lessons/${lessonId}/progress/`, {
        last_watched_position: position,
        video_duration: duration,
        completed,
      });
    } catch (err) {
      console.error('Failed to save lesson progress:', err);
    }
  };

  // Called on every player timeUpdate tick. Throttled to an API call at
  // most once per ~15s of actual playback (matches the web player's own
  // periodic-save cadence) -- the ref above still tracks the exact latest
  // position every tick, for the switch/unmount flush below to use.
  const handleVideoProgress = (currentTime: number, duration: number) => {
    if (!activeLesson) return;
    positionRef.current = { lessonId: activeLesson.id, position: currentTime, duration };
    if (currentTime - lastSavedRef.current >= 15) {
      lastSavedRef.current = currentTime;
      const nearEnd = duration > 0 && currentTime / duration >= 0.9;
      saveProgress(activeLesson.id, currentTime, duration, nearEnd || !!activeLesson.is_completed);
    }
  };

  // Flush the previous lesson's final position whenever the active lesson
  // changes (or the screen unmounts) -- mirrors the web learn page's own
  // "save progress of previous lesson when switching or unmounting"
  // cleanup, using the ref so this always reads the truly-latest tick, not
  // a stale value captured when the effect was set up.
  useEffect(() => {
    return () => {
      const { lessonId, position, duration } = positionRef.current;
      if (lessonId && duration > 0 && position > 0) {
        const nearEnd = position / duration >= 0.9;
        saveProgress(lessonId, position, duration, nearEnd);
      }
    };
  }, [activeLesson?.id]);

  const handleMarkComplete = async () => {
    if (!activeLesson) return;
    setMarking(true);
    try {
      const { position, duration } = positionRef.current;
      await saveProgress(activeLesson.id, position || 0, duration || 0, true);
      setActiveLesson((prev: any) => (prev ? { ...prev, is_completed: true } : prev));
      setCourse((prev: any) => {
        if (!prev) return prev;
        const modules = prev.modules.map((m: any) => ({
          ...m,
          lessons: m.lessons?.map((l: any) => (l.id === activeLesson.id ? { ...l, is_completed: true } : l)),
        }));
        return { ...prev, modules };
      });
    } catch (err) {
      Alert.alert('Error', 'Could not mark this lesson as complete.');
    } finally {
      setMarking(false);
    }
  };

  const videoSource = activeLesson?.video_file
    ? resolveMediaUrl(activeLesson.video_file)
    : null;

  // Manually-uploaded translated audio tracks for the active lesson (backend-driven,
  // see courses/serializers.py TranslatedAudioSerializer). Only completed tracks are
  // shown to students -- matches the web player's language menu.
  const audioTracks: AudioTrackOption[] = (activeLesson?.translated_audios || [])
    .filter((a: any) => a.status === 'completed' && a.audio_file)
    .map((a: any) => ({
      code: a.language_code.split('-')[0],
      name: a.language_name || LANGUAGE_NAME_MAP[a.language_code.split('-')[0].toLowerCase()] || a.language_code,
      url: resolveMediaUrl(a.audio_file),
    }));

  const handlePurchase = () => {
    // Redirect to web app for purchase to bypass app store 30% fee
    Linking.openURL(`https://academy.natyaarts.com/courses/${courseId}`);
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color={colors.accent} size="large" /></View>;
  if (!course) return <View style={styles.centered}><Text style={{ color: colors.text }}>Course not found</Text></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Go back">
          <Icon name="arrow-left" size={20} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle} numberOfLines={1}>{course.title}</Text>
        <View style={{ width: 20 }} />
      </View>

      <View style={styles.videoContainer}>
        {!isEnrolled ? (
          <>
            <Image source={{ uri: resolveMediaUrl(course.thumbnail) || FALLBACK_THUMB }} style={styles.previewImage} />
            <View style={styles.previewOverlay}>
              <TouchableOpacity style={styles.purchaseButton} onPress={handlePurchase}>
                <Text style={styles.purchaseButtonText}>Purchase for ₹{course.price}</Text>
              </TouchableOpacity>
              <Text style={styles.secureCheckoutText}>Secure checkout via web</Text>
            </View>
          </>
        ) : videoSource ? (
          <CustomVideoPlayer
            source={videoSource}
            audioTracks={audioTracks}
            lessonKey={activeLesson?.id}
            initialPosition={savedPosition}
            onProgress={handleVideoProgress}
          />
        ) : (
          <View style={styles.noVideo}>
            <Icon name="film" size={22} color={colors.textTertiary} />
            <Text style={styles.noVideoText}>No video uploaded for this lesson</Text>
          </View>
        )}
      </View>

      {isEnrolled && activeLesson && (
        <View style={styles.lessonMetaBlock}>
          <Text style={styles.lessonTitle} numberOfLines={2}>{activeLesson.title}</Text>
          {videoSource && (
            <TouchableOpacity
              style={[styles.markCompleteButton, activeLesson.is_completed && styles.markCompleteButtonDone]}
              onPress={handleMarkComplete}
              disabled={marking || activeLesson.is_completed}
              accessibilityRole="button"
              accessibilityLabel={activeLesson.is_completed ? 'Lesson completed' : 'Mark lesson as complete'}
            >
              {marking ? (
                <ActivityIndicator color={colors.textInverse} size="small" />
              ) : (
                <>
                  <Icon
                    name={activeLesson.is_completed ? 'check-circle' : 'check'}
                    size={14}
                    color={activeLesson.is_completed ? colors.accent : colors.textInverse}
                  />
                  <Text style={[styles.markCompleteText, activeLesson.is_completed && styles.markCompleteTextDone]}>
                    {activeLesson.is_completed ? 'Lesson completed' : 'Mark as complete'}
                  </Text>
                </>
              )}
            </TouchableOpacity>
          )}
        </View>
      )}

      <ScrollView style={styles.curriculum} showsVerticalScrollIndicator={false}>
        <View style={styles.courseDetails}>
          <Text style={styles.courseDescription}>{course.description}</Text>
          {typeof course.completion_percentage === 'number' && (
            <View style={styles.courseProgressRow}>
              <ProgressBar percent={course.completion_percentage} />
              <Text style={styles.courseProgressText}>
                {course.is_completed ? 'Course completed' : `${course.completion_percentage}% complete`}
              </Text>
            </View>
          )}
        </View>

        <Text style={styles.curriculumHeader}>Course Curriculum</Text>
        {course.modules?.map((module: any, idx: number) => (
          <View key={module.id} style={styles.moduleCard}>
            <View style={styles.moduleHeaderRow}>
              <Text style={styles.moduleTitle}>Module {idx + 1}: {module.title}</Text>
              {module.is_completed && <Icon name="check-circle" size={14} color={colors.success} />}
            </View>
            {module.lessons?.map((lesson: any, lIdx: number) => {
              const locked = isEnrolled ? !!lesson.is_locked : true;
              const active = activeLesson?.id === lesson.id;
              return (
                <TouchableOpacity
                  key={lesson.id}
                  style={[styles.lessonRow, active && styles.activeLessonRow]}
                  onPress={() => !locked && setActiveLesson(lesson)}
                  disabled={locked}
                  accessibilityRole="button"
                  accessibilityLabel={lesson.title}
                >
                  <Icon
                    name={lesson.is_completed ? 'check-circle' : locked ? 'lock' : 'play'}
                    size={14}
                    color={lesson.is_completed ? colors.success : locked ? colors.textTertiary : active ? colors.accent : colors.textSecondary}
                  />
                  <Text
                    style={[styles.lessonText, active && styles.activeLessonText, locked && styles.lockedLessonText]}
                    numberOfLines={1}
                  >
                    {lIdx + 1}. {lesson.title}
                  </Text>
                </TouchableOpacity>
              );
            })}

            {/* Assessments -- a separate destination screen, not shown
                inline in the video player, mirroring the web learn page. */}
            {module.assessments?.map((assessment: any) => {
              const meta = assessmentStatusMeta(assessment);
              return (
                <TouchableOpacity
                  key={`assessment-${assessment.id}`}
                  style={styles.taskRow}
                  onPress={() => navigation.navigate('Assessment', { assessmentId: assessment.id })}
                >
                  <Icon name="edit-2" size={13} color={colors.textSecondary} />
                  <Text style={styles.taskText} numberOfLines={1}>{assessment.title}</Text>
                  <Text style={[styles.taskStatus, { color: meta.color }]}>{meta.label}</Text>
                </TouchableOpacity>
              );
            })}

            {/* Assignments -- same pattern as assessments above. */}
            {module.assignments?.map((assignment: any) => {
              const meta = assignmentStatusMeta(assignment);
              return (
                <TouchableOpacity
                  key={`assignment-${assignment.id}`}
                  style={styles.taskRow}
                  onPress={() => navigation.navigate('Assignment', { assignmentId: assignment.id })}
                >
                  <Icon name="download" size={13} color={colors.textSecondary} />
                  <Text style={styles.taskText} numberOfLines={1}>{assignment.title}</Text>
                  <Text style={[styles.taskStatus, { color: meta.color }]}>{meta.label}</Text>
                </TouchableOpacity>
              );
            })}
          </View>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.bg },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: spacing.lg, paddingVertical: spacing.md,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  headerTitle: { ...typography.section, flex: 1, marginHorizontal: spacing.md, textAlign: 'center' },
  videoContainer: { width: '100%', aspectRatio: 16 / 9, backgroundColor: '#000', position: 'relative' },
  noVideo: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: spacing.sm },
  noVideoText: { color: colors.textTertiary, marginTop: spacing.sm },

  previewImage: { width: '100%', height: '100%', resizeMode: 'cover', opacity: 0.5 },
  previewOverlay: { position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.overlay },
  purchaseButton: { backgroundColor: colors.accent, paddingHorizontal: spacing.xxl, paddingVertical: spacing.md, borderRadius: radius.pill },
  purchaseButtonText: { color: colors.textInverse, fontSize: 16, fontWeight: '700' },
  secureCheckoutText: { ...typography.meta, marginTop: spacing.sm },

  lessonMetaBlock: { padding: spacing.lg, borderBottomWidth: 1, borderBottomColor: colors.border, gap: spacing.md },
  lessonTitle: { ...typography.title },
  markCompleteButton: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm,
    backgroundColor: colors.accent, paddingVertical: spacing.md, borderRadius: radius.md, alignSelf: 'flex-start',
    paddingHorizontal: spacing.xl,
  },
  markCompleteButtonDone: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border },
  markCompleteText: { color: colors.textInverse, fontSize: 13, fontWeight: '700', marginLeft: spacing.sm },
  markCompleteTextDone: { color: colors.accent },

  curriculum: { flex: 1, padding: spacing.lg },
  courseDetails: { marginBottom: spacing.xxl },
  courseDescription: { ...typography.bodyRegular, lineHeight: 20 },
  courseProgressRow: { marginTop: spacing.lg },
  courseProgressText: { ...typography.meta, color: colors.accent, fontWeight: '600', marginTop: spacing.sm },
  curriculumHeader: { ...typography.section, marginBottom: spacing.lg },

  moduleHeaderRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: spacing.sm },

  taskRow: {
    flexDirection: 'row', alignItems: 'center', padding: spacing.md, borderRadius: radius.sm,
    marginBottom: 4, backgroundColor: colors.cardAlt, gap: spacing.sm,
  },
  taskText: { flex: 1, color: colors.text, fontSize: 14, marginLeft: spacing.sm },
  taskStatus: { fontSize: 11, fontWeight: '700', marginLeft: spacing.sm },

  moduleCard: { marginBottom: spacing.xxl },
  moduleTitle: { ...typography.caption, textTransform: 'uppercase' },
  lessonRow: { flexDirection: 'row', alignItems: 'center', padding: spacing.md, borderRadius: radius.sm, marginBottom: 4, gap: spacing.sm },
  activeLessonRow: { backgroundColor: colors.accentMuted, borderWidth: 1, borderColor: colors.accentBorder },
  lessonText: { color: colors.text, fontSize: 14, flex: 1, marginLeft: spacing.sm },
  activeLessonText: { color: colors.accent, fontWeight: '600' },
  lockedLessonText: { color: colors.textTertiary },
});
