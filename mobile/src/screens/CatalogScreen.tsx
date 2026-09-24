import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, TextInput, ActivityIndicator, SafeAreaView, RefreshControl } from 'react-native';
import client from '../api/client';
import Icon from '../components/Icon';
import CourseCard from '../components/CourseCard';
import EmptyState from '../components/EmptyState';
import { colors, spacing, radius, typography } from '../theme';

// course_type is a real, small enum on the Course model (LIVE | RECORDED)
// -- these filter chips are derived from that actual field, never an
// invented taxonomy.
const FILTERS = [
  { key: 'ALL', label: 'All' },
  { key: 'RECORDED', label: 'Recorded' },
  { key: 'LIVE', label: 'Live' },
] as const;

export default function CatalogScreen({ navigation }: any) {
  const [courses, setCourses] = useState<any[]>([]);
  const [enrolledIds, setEnrolledIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<typeof FILTERS[number]['key']>('ALL');

  const fetchCatalogData = async () => {
    setError(false);
    const [catalogRes, myCoursesRes] = await Promise.allSettled([
      client.get('courses/'),
      client.get('courses/my_courses/'),
    ]);
    if (catalogRes.status === 'fulfilled') {
      setCourses(catalogRes.value.data || []);
    } else {
      setError(true);
    }
    if (myCoursesRes.status === 'fulfilled') {
      setEnrolledIds(new Set((myCoursesRes.value.data || []).map((c: any) => c.id)));
    }
    setLoading(false);
    setRefreshing(false);
  };

  useEffect(() => {
    fetchCatalogData();
  }, []);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchCatalogData();
  }, []);

  const filteredCourses = useMemo(() => {
    return courses.filter((c) => {
      const matchesFilter = filter === 'ALL' || c.course_type === filter;
      const matchesQuery = !query.trim() || c.title?.toLowerCase().includes(query.trim().toLowerCase());
      return matchesFilter && matchesQuery;
    });
  }, [courses, filter, query]);

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
        <Text style={styles.headerTitle}>Explore</Text>
      </View>

      <View style={styles.searchRow}>
        <Icon name="search" size={17} color={colors.textTertiary} style={styles.searchIcon} />
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder="Search courses"
          placeholderTextColor={colors.textTertiary}
          style={styles.searchInput}
          accessibilityLabel="Search courses"
          returnKeyType="search"
        />
      </View>

      <View style={styles.filterRow}>
        {FILTERS.map((f) => {
          const active = filter === f.key;
          return (
            <TouchableOpacity
              key={f.key}
              onPress={() => setFilter(f.key)}
              style={[styles.chip, active && styles.chipActive]}
              accessibilityRole="button"
              accessibilityLabel={`Filter: ${f.label}`}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>{f.label}</Text>
            </TouchableOpacity>
          );
        })}
      </View>

      <FlatList
        data={filteredCourses}
        keyExtractor={(item) => item.id.toString()}
        renderItem={({ item }) => (
          <CourseCard
            variant="list"
            title={item.title}
            thumbnail={item.thumbnail}
            moduleCount={item.modules?.length}
            price={enrolledIds.has(item.id) ? undefined : item.price}
            isCompleted={false}
            onPress={() => navigation.navigate('CourseDetails', { courseId: item.id })}
          />
        )}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
        contentContainerStyle={styles.listContainer}
        showsVerticalScrollIndicator={false}
        ListEmptyComponent={
          error ? (
            <EmptyState
              icon="alert-circle"
              title="Couldn't load courses"
              message="Something went wrong reaching the catalog. Pull down to try again."
            />
          ) : query || filter !== 'ALL' ? (
            <EmptyState icon="search" title="No matches" message="Try a different search term or filter." />
          ) : (
            <EmptyState icon="book-open" title="No courses found" message="Check back later for new masterclasses." />
          )
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { justifyContent: 'center', alignItems: 'center' },

  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md },
  headerTitle: { ...typography.title },

  searchRow: {
    flexDirection: 'row', alignItems: 'center', backgroundColor: colors.card,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    marginHorizontal: spacing.lg, paddingHorizontal: spacing.md, marginBottom: spacing.md,
  },
  searchIcon: { marginRight: spacing.sm },
  searchInput: { flex: 1, color: colors.text, fontSize: 14, paddingVertical: 11 },

  filterRow: { flexDirection: 'row', paddingHorizontal: spacing.lg, marginBottom: spacing.lg, gap: spacing.sm },
  chip: {
    paddingHorizontal: spacing.lg, paddingVertical: 8, borderRadius: radius.pill,
    backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, marginRight: spacing.sm,
  },
  chipActive: { backgroundColor: colors.accentMuted, borderColor: colors.accentBorder },
  chipText: { color: colors.textSecondary, fontSize: 13, fontWeight: '600' },
  chipTextActive: { color: colors.accent },

  listContainer: { paddingHorizontal: spacing.lg, paddingBottom: spacing.xxl, flexGrow: 1 },
});
