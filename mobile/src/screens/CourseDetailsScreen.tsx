import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, Image, ActivityIndicator, SafeAreaView, ScrollView, TouchableOpacity, Linking, Alert, Platform } from 'react-native';
import client, { resolveMediaUrl } from '../api/client';
import Icon from '../components/Icon';
import { colors, spacing, radius, typography } from '../theme';

const FALLBACK_THUMB = 'https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop';

// A module's completion fields are only non-null when its content is
// actually unlocked for this user (see courses/serializers.py
// ModuleSerializer.to_representation -- locked modules get
// LOCKED_COMPLETION, whose is_completed is always None). This is a real
// per-request access signal from the API, not a guess -- used to decide
// whether to offer "Go to Course" instead of "Buy Course".
function hasCourseAccess(course: any) {
  return !!course?.modules?.some((m: any) => m.is_completed !== null);
}

export default function CourseDetailsScreen({ route, navigation }: any) {
  const { courseId } = route.params;
  const [course, setCourse] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchCourseDetails = async () => {
      try {
        const res = await client.get(`courses/${courseId}/`);
        setCourse(res.data);
      } catch (err) {
        console.error(err);
        Alert.alert('Error', 'Could not load course details');
        navigation.goBack();
      } finally {
        setLoading(false);
      }
    };
    fetchCourseDetails();
  }, [courseId]);

  if (loading || !course) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  const thumbUrl = resolveMediaUrl(course.thumbnail) || FALLBACK_THUMB;
  const owned = hasCourseAccess(course);

  const handleBuyCourse = () => {
    // Open the web version for checkout to bypass Google Play Billing 30% fee
    const url = `https://academy.natyaarts.com/courses/${courseId}`;
    Linking.openURL(url).catch((err) => console.error("Couldn't load page", err));
  };

  const handleGoToCourse = () => {
    navigation.navigate('Learn', { courseId, isEnrolled: true });
  };

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
        <View style={styles.artworkWrap}>
          <Image source={{ uri: thumbUrl }} style={styles.artwork} />
          <TouchableOpacity
            onPress={() => navigation.goBack()}
            style={styles.backButton}
            accessibilityRole="button"
            accessibilityLabel="Go back"
          >
            <Icon name="arrow-left" size={18} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.typeBadge}>
            <Text style={styles.typeBadgeText}>{course.course_type === 'LIVE' ? 'Live' : 'Recorded'}</Text>
          </View>
        </View>

        <View style={styles.content}>
          <Text style={styles.title}>{course.title}</Text>

          <View style={styles.metaRow}>
            {!owned && <Text style={styles.price}>₹{course.price}</Text>}
            {typeof course.total_module_count === 'number' && course.total_module_count > 0 && (
              <Text style={typography.meta}>{course.total_module_count} {course.total_module_count === 1 ? 'module' : 'modules'}</Text>
            )}
          </View>

          {typeof course.completion_percentage === 'number' && (
            <Text style={styles.progressNote}>
              {course.is_completed ? 'Course completed' : `${course.completion_percentage}% complete`}
            </Text>
          )}

          <Text style={styles.sectionTitle}>About this course</Text>
          <Text style={styles.description}>{course.description}</Text>

          <Text style={styles.sectionTitle}>Curriculum</Text>
          {course.modules?.map((mod: any, index: number) => (
            <View key={mod.id} style={styles.moduleCard}>
              <View style={styles.moduleHeaderRow}>
                <Text style={styles.moduleNumber}>{(index + 1).toString().padStart(2, '0')}</Text>
                <View style={styles.moduleInfo}>
                  <Text style={styles.moduleTitle}>{mod.title}</Text>
                  {!!mod.description && <Text style={styles.moduleDescription} numberOfLines={2}>{mod.description}</Text>}
                </View>
              </View>
              {mod.lessons?.map((lesson: any) => (
                <View key={lesson.id} style={styles.lessonRow}>
                  <Icon
                    name={lesson.is_completed ? 'check-circle' : lesson.is_locked ? 'lock' : 'play'}
                    size={15}
                    color={lesson.is_completed ? colors.success : lesson.is_locked ? colors.textTertiary : colors.accent}
                  />
                  <Text style={[styles.lessonText, lesson.is_locked && styles.lessonTextLocked]} numberOfLines={1}>
                    {lesson.title}
                  </Text>
                </View>
              ))}
            </View>
          ))}
        </View>
      </ScrollView>

      <View style={styles.footer}>
        {owned ? (
          <TouchableOpacity style={styles.primaryButton} onPress={handleGoToCourse}>
            <Icon name="play" size={16} color={colors.textInverse} />
            <Text style={styles.primaryButtonText}>Go to Course</Text>
          </TouchableOpacity>
        ) : Platform.OS === 'android' ? (
          <TouchableOpacity style={styles.primaryButton} onPress={handleBuyCourse}>
            <Text style={styles.primaryButtonText}>Buy Course for ₹{course.price}</Text>
          </TouchableOpacity>
        ) : (
          <View style={styles.infoBox}>
            <Icon name="shield" size={15} color={colors.textSecondary} />
            <Text style={styles.infoBoxText}>
              Courses enrolled on our web platform are automatically available to learn here.
            </Text>
          </View>
        )}
      </View>
    </SafeAreaView>
  );
}

const ARTWORK_HEIGHT = 260;

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { justifyContent: 'center', alignItems: 'center' },

  scrollContent: { paddingBottom: 110 },

  artworkWrap: { width: '100%', height: ARTWORK_HEIGHT, position: 'relative', backgroundColor: colors.card },
  artwork: { width: '100%', height: '100%', resizeMode: 'cover' },
  backButton: {
    position: 'absolute', top: spacing.lg, left: spacing.lg,
    width: 38, height: 38, borderRadius: 19, backgroundColor: colors.scrim,
    alignItems: 'center', justifyContent: 'center',
  },
  typeBadge: {
    position: 'absolute', top: spacing.lg, right: spacing.lg,
    backgroundColor: colors.scrim, borderRadius: radius.pill,
    paddingHorizontal: spacing.md, paddingVertical: 6,
  },
  typeBadgeText: { color: colors.text, fontSize: 11, fontWeight: '700', letterSpacing: 0.4 },

  content: { padding: spacing.lg },
  title: { ...typography.hero, marginBottom: spacing.sm },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, marginBottom: spacing.sm },
  price: { color: colors.accent, fontSize: 20, fontWeight: '700', marginRight: spacing.md },
  progressNote: { ...typography.meta, color: colors.accent, marginBottom: spacing.lg },

  sectionTitle: { ...typography.section, marginTop: spacing.xl, marginBottom: spacing.md },
  description: { ...typography.bodyRegular, lineHeight: 22 },

  moduleCard: {
    backgroundColor: colors.card, borderRadius: radius.lg, padding: spacing.lg,
    marginBottom: spacing.md, borderWidth: 1, borderColor: colors.border,
  },
  moduleHeaderRow: { flexDirection: 'row', marginBottom: spacing.sm },
  moduleNumber: { color: colors.accent, fontSize: 16, fontWeight: '700', width: 34 },
  moduleInfo: { flex: 1 },
  moduleTitle: { ...typography.body },
  moduleDescription: { ...typography.meta, marginTop: 2 },

  lessonRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.sm, gap: spacing.sm },
  lessonText: { ...typography.bodyRegular, color: colors.text, flex: 1, marginLeft: spacing.sm },
  lessonTextLocked: { color: colors.textTertiary },

  footer: {
    position: 'absolute', bottom: 0, left: 0, right: 0, padding: spacing.lg,
    backgroundColor: colors.bg, borderTopWidth: 1, borderTopColor: colors.border,
  },
  primaryButton: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm,
    backgroundColor: colors.accent, paddingVertical: spacing.md, borderRadius: radius.md,
  },
  primaryButtonText: { color: colors.textInverse, fontSize: 16, fontWeight: '700', marginLeft: spacing.sm },
  infoBox: {
    flexDirection: 'row', alignItems: 'center', backgroundColor: colors.card,
    borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.md,
    gap: spacing.sm, borderWidth: 1, borderColor: colors.border,
  },
  infoBoxText: {
    ...typography.meta, color: colors.textSecondary, flex: 1, lineHeight: 18,
  },
});
