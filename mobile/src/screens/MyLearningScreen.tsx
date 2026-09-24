import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, StyleSheet, ActivityIndicator, SafeAreaView, RefreshControl } from 'react-native';
import client from '../api/client';
import CourseCard from '../components/CourseCard';
import EmptyState from '../components/EmptyState';
import { colors, spacing, typography } from '../theme';

// The definitive, complete list of everything the student is enrolled in --
// distinct from Home's compact "Continue Learning" hero + rail summary.
// Reuses the exact same real endpoint Home already calls; no new API,
// no mock data.
export default function MyLearningScreen({ navigation }: any) {
  const [courses, setCourses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchCourses = async () => {
    try {
      const res = await client.get('courses/my_courses/');
      setCourses(res.data || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchCourses();
  }, []);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchCourses();
  }, []);

  const goToCourse = (course: any) =>
    navigation.navigate('Learn', { courseId: course.id || course.course_id, isEnrolled: true });

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
        <Text style={styles.headerTitle}>My Learning</Text>
        <Text style={styles.headerSubtitle}>Every course you're enrolled in, in one place.</Text>
      </View>

      <FlatList
        data={courses}
        keyExtractor={(item) => (item.id || item.course_id).toString()}
        renderItem={({ item }) => (
          <CourseCard
            variant="list"
            title={item.title}
            thumbnail={item.thumbnail}
            moduleCount={item.modules?.length}
            completionPercent={typeof item.completion_percentage === 'number' ? item.completion_percentage : null}
            isCompleted={!!item.is_completed}
            onPress={() => goToCourse(item)}
          />
        )}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
        contentContainerStyle={styles.listContainer}
        showsVerticalScrollIndicator={false}
        ListEmptyComponent={
          <EmptyState
            icon="book-open"
            title="No courses yet"
            message="You haven't enrolled in any masterclasses yet. Explore the catalog to start learning."
            ctaLabel="Explore catalog"
            onPressCta={() => navigation.navigate('Explore')}
          />
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { justifyContent: 'center', alignItems: 'center' },
  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg },
  headerTitle: { ...typography.title },
  headerSubtitle: { ...typography.bodyRegular, marginTop: 4 },
  listContainer: { paddingHorizontal: spacing.lg, paddingBottom: spacing.xxl, flexGrow: 1 },
});
