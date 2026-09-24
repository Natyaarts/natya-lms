import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, Image, ActivityIndicator, RefreshControl, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import client, { resolveMediaUrl } from '../api/client';
import { registerForPushNotificationsAsync, unregisterForPushNotificationsAsync } from '../api/pushNotifications';
import Icon from '../components/Icon';
import CourseCard from '../components/CourseCard';
import SectionHeader from '../components/SectionHeader';
import EmptyState from '../components/EmptyState';
import ProgressBar from '../components/ProgressBar';
import { colors, spacing, radius, typography } from '../theme';

// Walks the real, already-fetched modules/lessons of an enrolled course
// (the same nested shape LearnScreen already reads) to find the first
// not-yet-completed lesson -- this is a derivation from real API data,
// never a fabricated "recommendation". Falls back to the very first
// lesson so a fully-completed (or lesson-less) course still has
// something for "Continue" to resume/rewatch.
function findContinueLesson(course: any) {
  for (const mod of course?.modules || []) {
    for (const lesson of mod.lessons || []) {
      if (!lesson.is_completed) return lesson;
    }
  }
  return course?.modules?.[0]?.lessons?.[0] || null;
}

function liveClassSchedule(item: any) {
  const start = new Date(item.scheduled_start);
  const isLive = item.status === 'LIVE';
  return {
    isLive,
    label: isLive ? 'Live now' : start.toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' }),
  };
}

export default function DashboardScreen({ navigation }: any) {
  const [enrolledCourses, setEnrolledCourses] = useState<any[]>([]);
  const [liveClasses, setLiveClasses] = useState<any[]>([]);
  const [exploreCourses, setExploreCourses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchDashboardData = async () => {
    // Every section below is backed by one of these three real,
    // already-existing endpoints -- no fabricated data anywhere. A
    // failure in one (e.g. live classes) never blocks the others.
    const [coursesRes, liveRes, catalogRes] = await Promise.allSettled([
      client.get('courses/my_courses/'),
      client.get('courses/live-classes/'),
      client.get('courses/'),
    ]);
    if (coursesRes.status === 'fulfilled') setEnrolledCourses(coursesRes.value.data || []);
    if (liveRes.status === 'fulfilled') {
      const list = liveRes.value.data?.results || liveRes.value.data || [];
      setLiveClasses(list.filter((c: any) => c.status === 'LIVE' || c.status === 'SCHEDULED'));
    }
    if (catalogRes.status === 'fulfilled') setExploreCourses(catalogRes.value.data || []);
    setLoading(false);
    setRefreshing(false);
  };

  useEffect(() => {
    fetchDashboardData();
    // Phase 4.10: this is the one screen every authenticated session
    // always passes through first (cold start already logged in, fresh
    // OTP/Google login, or right after onboarding all land on MainTabs,
    // whose first tab is this screen) -- registering here once, rather
    // than duplicating the call in LoginScreen/OnboardingScreen/App.tsx,
    // covers every path with a single call site. Registration itself is
    // an idempotent upsert (see DeviceTokenView) and never blocks this
    // screen -- it fails silently if permission is denied or offline.
    registerForPushNotificationsAsync();
  }, []);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchDashboardData();
  }, []);

  const handleLogout = async () => {
    await unregisterForPushNotificationsAsync();
    await AsyncStorage.removeItem('access_token');
    await AsyncStorage.removeItem('refresh_token');
    navigation.replace('Login');
  };

  const continueCourse = useMemo(
    () => enrolledCourses.find((c) => !c.is_completed) || enrolledCourses[0] || null,
    [enrolledCourses]
  );
  const continueLesson = useMemo(() => (continueCourse ? findContinueLesson(continueCourse) : null), [continueCourse]);

  const exploreList = useMemo(() => {
    const enrolledIds = new Set(enrolledCourses.map((c) => c.id));
    return exploreCourses.filter((c) => !enrolledIds.has(c.id)).slice(0, 10);
  }, [enrolledCourses, exploreCourses]);

  const goToCourse = (course: any) => navigation.navigate('Learn', { courseId: course.id || course.course_id, isEnrolled: true });

  if (loading) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <View>
          <Text style={styles.eyebrow}>WELCOME BACK</Text>
          <Text style={styles.headerTitle}>Home</Text>
        </View>
        <View style={styles.headerActions}>
          <TouchableOpacity
            onPress={() => navigation.navigate('Notifications')}
            style={styles.iconButton}
            accessibilityRole="button"
            accessibilityLabel="Notifications"
          >
            <Icon name="bell" size={20} color={colors.text} />
          </TouchableOpacity>
          <TouchableOpacity
            onPress={handleLogout}
            style={styles.iconButton}
            accessibilityRole="button"
            accessibilityLabel="Log out"
          >
            <Icon name="log-out" size={20} color={colors.textSecondary} />
          </TouchableOpacity>
        </View>
      </View>

      <ScrollView
        contentContainerStyle={styles.scrollContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
        showsVerticalScrollIndicator={false}
      >
        {enrolledCourses.length === 0 ? (
          <EmptyState
            icon="book-open"
            title="No courses yet"
            message="You haven't enrolled in any masterclasses yet. Explore the catalog to start learning."
            ctaLabel="Explore catalog"
            onPressCta={() => navigation.navigate('Explore')}
          />
        ) : (
          <>
            {continueCourse && (
              <TouchableOpacity
                style={styles.heroCard}
                onPress={() => goToCourse(continueCourse)}
                activeOpacity={0.9}
                accessibilityRole="button"
                accessibilityLabel={`Continue ${continueCourse.title}`}
              >
                <Image source={{ uri: resolveMediaUrl(continueCourse.thumbnail) || undefined }} style={styles.heroImage} />
                <View style={styles.heroScrim} />
                <View style={styles.heroContent}>
                  <Text style={styles.heroEyebrow}>CONTINUE LEARNING</Text>
                  <Text style={styles.heroTitle} numberOfLines={1}>{continueCourse.title}</Text>
                  {!!continueLesson && (
                    <Text style={styles.heroLesson} numberOfLines={1}>{continueLesson.title}</Text>
                  )}
                  {typeof continueCourse.completion_percentage === 'number' && (
                    <View style={styles.heroProgressRow}>
                      <View style={styles.heroProgressBar}>
                        <ProgressBar percent={continueCourse.completion_percentage} height={4} />
                      </View>
                      <Text style={styles.heroProgressText}>{continueCourse.completion_percentage}%</Text>
                    </View>
                  )}
                  <View style={styles.heroButton}>
                    <Icon name="play" size={14} color={colors.textInverse} />
                    <Text style={styles.heroButtonText}>Continue</Text>
                  </View>
                </View>
              </TouchableOpacity>
            )}

            <SectionHeader title="My Courses" onPressSeeAll={() => navigation.navigate('My Learning')} />
            <FlatList
              data={enrolledCourses}
              horizontal
              showsHorizontalScrollIndicator={false}
              keyExtractor={(item) => (item.id || item.course_id).toString()}
              contentContainerStyle={styles.railList}
              renderItem={({ item }) => (
                <CourseCard
                  variant="rail"
                  title={item.title}
                  thumbnail={item.thumbnail}
                  moduleCount={item.modules?.length}
                  completionPercent={typeof item.completion_percentage === 'number' ? item.completion_percentage : null}
                  isCompleted={!!item.is_completed}
                  onPress={() => goToCourse(item)}
                />
              )}
            />

            {liveClasses.length > 0 && (
              <>
                <SectionHeader title="Upcoming Live Classes" onPressSeeAll={() => navigation.navigate('LiveClasses')} />
                <View style={styles.liveList}>
                  {liveClasses.slice(0, 3).map((item) => {
                    const { isLive, label } = liveClassSchedule(item);
                    return (
                      <TouchableOpacity
                        key={item.id}
                        style={styles.liveCard}
                        onPress={() => navigation.navigate('LiveClasses')}
                        activeOpacity={0.85}
                      >
                        <View style={[styles.liveDot, isLive && styles.liveDotActive]} />
                        <View style={styles.liveInfo}>
                          <Text style={styles.liveTitle} numberOfLines={1}>{item.title}</Text>
                          <Text style={styles.liveMeta}>{label} · {item.duration_minutes} min</Text>
                        </View>
                        {isLive && !!item.meeting_url ? (
                          <View style={styles.joinPill}>
                            <Text style={styles.joinPillText}>Join</Text>
                          </View>
                        ) : (
                          <Icon name="chevron-right" size={18} color={colors.textTertiary} />
                        )}
                      </TouchableOpacity>
                    );
                  })}
                </View>
              </>
            )}

            {exploreList.length > 0 && (
              <>
                <SectionHeader title="Explore More" onPressSeeAll={() => navigation.navigate('Explore')} />
                <FlatList
                  data={exploreList}
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  keyExtractor={(item) => item.id.toString()}
                  contentContainerStyle={styles.railList}
                  renderItem={({ item }) => (
                    <CourseCard
                      variant="rail"
                      title={item.title}
                      thumbnail={item.thumbnail}
                      moduleCount={item.modules?.length}
                      price={item.price}
                      onPress={() => navigation.navigate('CourseDetails', { courseId: item.id })}
                    />
                  )}
                />
              </>
            )}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { justifyContent: 'center', alignItems: 'center' },

  header: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md,
  },
  eyebrow: { ...typography.caption, marginBottom: 2 },
  headerTitle: { ...typography.title },
  headerActions: { flexDirection: 'row', gap: spacing.sm },
  iconButton: {
    width: 38, height: 38, borderRadius: 19, backgroundColor: colors.card,
    alignItems: 'center', justifyContent: 'center', marginLeft: spacing.sm,
  },

  scrollContent: { paddingBottom: spacing.xxxl },

  heroCard: {
    marginHorizontal: spacing.lg, marginBottom: spacing.xxl,
    borderRadius: radius.lg, overflow: 'hidden', height: 200,
    backgroundColor: colors.card,
  },
  heroImage: { ...StyleSheet.absoluteFill, resizeMode: 'cover' },
  heroScrim: { ...StyleSheet.absoluteFill, backgroundColor: colors.overlay },
  heroContent: { flex: 1, justifyContent: 'flex-end', padding: spacing.lg },
  heroEyebrow: { ...typography.caption, color: colors.accent, marginBottom: 6 },
  heroTitle: { color: colors.text, fontSize: 20, fontWeight: '700', marginBottom: 2 },
  heroLesson: { ...typography.bodyRegular, marginBottom: spacing.sm },
  heroProgressRow: { flexDirection: 'row', alignItems: 'center', marginBottom: spacing.md },
  heroProgressBar: { flex: 1, marginRight: spacing.sm },
  heroProgressText: { ...typography.meta, color: colors.accent, fontWeight: '600' },
  heroButton: {
    flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-start', gap: 6,
    backgroundColor: colors.accent, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm,
    borderRadius: radius.md,
  },
  heroButtonText: { color: colors.textInverse, fontSize: 14, fontWeight: '700', marginLeft: 6 },

  railList: { paddingHorizontal: spacing.lg, paddingBottom: spacing.xxl },

  liveList: { paddingHorizontal: spacing.lg, marginBottom: spacing.xxl, gap: spacing.sm },
  liveCard: {
    flexDirection: 'row', alignItems: 'center', backgroundColor: colors.card,
    borderRadius: radius.md, padding: spacing.md, borderWidth: 1, borderColor: colors.border,
    marginBottom: spacing.sm,
  },
  liveDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.textTertiary, marginRight: spacing.md },
  liveDotActive: { backgroundColor: colors.danger },
  liveInfo: { flex: 1 },
  liveTitle: { ...typography.body },
  liveMeta: { ...typography.meta, marginTop: 2 },
  joinPill: { backgroundColor: colors.accent, borderRadius: radius.pill, paddingHorizontal: spacing.md, paddingVertical: 6 },
  joinPillText: { color: colors.textInverse, fontSize: 12, fontWeight: '700' },
});
