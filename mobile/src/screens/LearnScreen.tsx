import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, SafeAreaView, ActivityIndicator, ScrollView, Linking, Image, Alert } from 'react-native';
import client, { resolveMediaUrl } from '../api/client';
import CustomVideoPlayer, { AudioTrackOption } from '../components/CustomVideoPlayer';
import { usePreventScreenCapture } from 'expo-screen-capture';

// Assessment/assignment status -> badge label/color, reusing the exact
// status values the backend already computes (courses/serializers.py
// ModuleSerializer.get_assessments/get_assignments) -- never re-derived
// here, same principle the web frontend's learn page follows.
function assessmentStatusMeta(assessment: any) {
  switch (assessment.status) {
    case 'IN_PROGRESS': return { label: 'In Progress', color: '#60a5fa' };
    case 'PASSED': return { label: 'Passed', color: '#22c55e' };
    case 'FAILED': return { label: 'Failed', color: '#f87171' };
    case 'MAX_ATTEMPTS_REACHED': return { label: 'Max Attempts', color: '#71717a' };
    default: return { label: 'Not Started', color: '#71717a' };
  }
}

function assignmentStatusMeta(assignment: any) {
  switch (assignment.status) {
    case 'SUBMITTED': return { label: 'Submitted', color: '#60a5fa' };
    case 'GRADED': return { label: 'Graded', color: '#22c55e' };
    case 'RETURNED_FOR_REVISION': return { label: 'Revision Needed', color: '#fb923c' };
    default: return { label: 'Not Submitted', color: '#71717a' };
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
      if (lessonId && duration > 0) {
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

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;
  if (!course) return <View style={styles.centered}><Text style={{color: '#fff'}}>Course not found</Text></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}>
          <Text style={styles.backText}>← Back</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle} numberOfLines={1}>{course.title}</Text>
      </View>

      <View style={styles.videoContainer}>
        {!isEnrolled ? (
          <>
            <Image 
              source={{ uri: course.thumbnail?.startsWith('/') ? `https://academy-api.natyaarts.com${course.thumbnail}` : (course.thumbnail || 'https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop') }} 
              style={styles.previewImage} 
            />
            <View style={styles.previewOverlay}>
              <TouchableOpacity style={styles.purchaseButton} onPress={handlePurchase}>
                <Text style={styles.purchaseButtonText}>Purchase for ₹{course.price}</Text>
              </TouchableOpacity>
              <Text style={styles.secureCheckoutText}>Secure Checkout via Web</Text>
            </View>
          </>
        ) : videoSource ? (
          <CustomVideoPlayer
            source={videoSource}
            audioTracks={audioTracks}
            lessonKey={activeLesson?.id}
            onProgress={handleVideoProgress}
          />
        ) : (
          <View style={styles.noVideo}><Text style={{color: '#666'}}>No video uploaded for this lesson</Text></View>
        )}
      </View>

      {isEnrolled && activeLesson && videoSource && (
        <TouchableOpacity
          style={[styles.markCompleteButton, activeLesson.is_completed && styles.markCompleteButtonDone]}
          onPress={handleMarkComplete}
          disabled={marking || activeLesson.is_completed}
        >
          {marking ? (
            <ActivityIndicator color="#000" size="small" />
          ) : (
            <Text style={styles.markCompleteText}>
              {activeLesson.is_completed ? '✓ Lesson Completed' : 'Mark Lesson as Complete'}
            </Text>
          )}
        </TouchableOpacity>
      )}

      <ScrollView style={styles.curriculum}>
        <View style={styles.courseDetails}>
          <Text style={styles.courseDescription}>{course.description}</Text>
          {typeof course.completion_percentage === 'number' && (
            <View style={styles.courseProgressRow}>
              <View style={styles.progressBarTrack}>
                <View style={[styles.progressBarFill, { width: `${course.completion_percentage}%` }]} />
              </View>
              <Text style={styles.courseProgressText}>
                {course.is_completed ? '🎓 Course Completed' : `${course.completion_percentage}% complete`}
              </Text>
            </View>
          )}
        </View>

        <Text style={styles.curriculumHeader}>Course Curriculum</Text>
        {course.modules?.map((module: any, idx: number) => (
          <View key={module.id} style={styles.moduleCard}>
            <View style={styles.moduleHeaderRow}>
              <Text style={styles.moduleTitle}>Module {idx + 1}: {module.title}</Text>
              {module.is_completed && <Text style={styles.moduleDoneCheck}>✓</Text>}
            </View>
            {module.lessons?.map((lesson: any, lIdx: number) => (
              <TouchableOpacity
                key={lesson.id} 
                style={[
                  styles.lessonRow, 
                  activeLesson?.id === lesson.id && styles.activeLessonRow,
                  !isEnrolled && styles.lockedLessonRow
                ]}
                onPress={() => isEnrolled && setActiveLesson(lesson)}
                disabled={!isEnrolled}
              >
                <Text style={[
                  styles.lessonText,
                  activeLesson?.id === lesson.id && styles.activeLessonText,
                  !isEnrolled && styles.lockedLessonText
                ]}>
                  {lesson.is_completed ? '✓ ' : `${lIdx + 1}. `}{lesson.title} {!isEnrolled && "🔒"}
                </Text>
              </TouchableOpacity>
            ))}

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
                  <Text style={styles.taskIcon}>📝</Text>
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
                  <Text style={styles.taskIcon}>📄</Text>
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
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505' },
  header: { flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  backText: { color: '#facc15', fontSize: 16, marginRight: 16 },
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold', flex: 1 },
  videoContainer: { width: '100%', aspectRatio: 16/9, backgroundColor: '#000', position: 'relative' },
  noVideo: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  
  // Preview Overlay
  previewImage: { width: '100%', height: '100%', resizeMode: 'cover', opacity: 0.5 },
  previewOverlay: { position: 'absolute', inset: 0, justifyContent: 'center', alignItems: 'center', backgroundColor: 'rgba(0,0,0,0.6)' },
  purchaseButton: { backgroundColor: '#facc15', paddingHorizontal: 24, paddingVertical: 12, borderRadius: 30 },
  purchaseButtonText: { color: '#000', fontSize: 16, fontWeight: 'bold' },
  secureCheckoutText: { color: '#a1a1aa', fontSize: 12, marginTop: 8 },

  markCompleteButton: { backgroundColor: '#facc15', paddingVertical: 12, alignItems: 'center' },
  markCompleteButtonDone: { backgroundColor: '#18181b' },
  markCompleteText: { color: '#000', fontSize: 14, fontWeight: 'bold' },

  curriculum: { flex: 1, padding: 16 },
  courseDetails: { marginBottom: 24 },
  courseDescription: { color: '#a1a1aa', fontSize: 14, lineHeight: 20 },
  courseProgressRow: { marginTop: 16 },
  courseProgressText: { color: '#facc15', fontSize: 13, fontWeight: '600', marginTop: 8 },
  progressBarTrack: { height: 6, backgroundColor: '#18181b', borderRadius: 3, overflow: 'hidden' },
  progressBarFill: { height: '100%', backgroundColor: '#facc15', borderRadius: 3 },
  curriculumHeader: { color: '#fff', fontSize: 18, fontWeight: 'bold', marginBottom: 16 },

  moduleHeaderRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  moduleDoneCheck: { color: '#22c55e', fontSize: 14, fontWeight: 'bold' },

  taskRow: { flexDirection: 'row', alignItems: 'center', padding: 12, borderRadius: 8, marginBottom: 4, backgroundColor: 'rgba(255,255,255,0.03)' },
  taskIcon: { fontSize: 14, marginRight: 8 },
  taskText: { flex: 1, color: '#e4e4e7', fontSize: 15 },
  taskStatus: { fontSize: 11, fontWeight: 'bold', marginLeft: 8 },

  moduleCard: { marginBottom: 24 },
  moduleTitle: { color: '#a1a1aa', fontSize: 12, fontWeight: 'bold', textTransform: 'uppercase', marginBottom: 8, letterSpacing: 1 },
  lessonRow: { padding: 12, borderRadius: 8, marginBottom: 4 },
  activeLessonRow: { backgroundColor: 'rgba(250, 204, 21, 0.1)', borderWidth: 1, borderColor: 'rgba(250, 204, 21, 0.2)' },
  lockedLessonRow: { opacity: 0.6 },
  lessonText: { color: '#e4e4e7', fontSize: 15 },
  activeLessonText: { color: '#facc15', fontWeight: 'bold' },
  lockedLessonText: { color: '#71717a' }
});
