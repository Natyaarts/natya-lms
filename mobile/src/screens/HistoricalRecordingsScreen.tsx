import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, ActivityIndicator, RefreshControl, TextInput } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import client from '../api/client';
import ScreenHeader from '../components/ScreenHeader';
import EmptyState from '../components/EmptyState';
import Icon from '../components/Icon';
import { colors, spacing, radius, typography } from '../theme';

// Recordings of classes from the previous platform. The list is scoped
// server-side (GET courses/historical-recordings/) to sessions of the
// student's own batches (confirmed class-to-batch mappings); this screen never
// filters for access itself.

const PAGE_SIZE = 20;

type RecordingFile = { id: number; file_type: 'MP4' | 'VTT'; filename: string; file_size_bytes: number | null };
type Recording = {
  id: number;
  title: string;
  course: { id: number; title: string };
  class_name: string;
  teacher_name: string;
  actual_start: string | null;
  duration: string;
  files: RecordingFile[];
};

function formatWhen(iso: string | null) {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? '' : d.toLocaleString();
}

export default function HistoricalRecordingsScreen({ navigation }: any) {
  const [items, setItems] = useState<Recording[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [mappingPending, setMappingPending] = useState(false);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const requestId = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), 350);
    return () => clearTimeout(t);
  }, [query]);

  const load = useCallback(async (nextPage: number, replace: boolean) => {
    const id = ++requestId.current;
    try {
      const params: Record<string, string> = { page: String(nextPage), page_size: String(PAGE_SIZE), has_video: '1' };
      if (debouncedQuery) params.q = debouncedQuery;
      const res = await client.get('courses/historical-recordings/', { params });
      if (id !== requestId.current) return; // a newer search replaced this one
      const results: Recording[] = res.data?.results || [];
      setItems(prev => (replace ? results : [...prev, ...results]));
      setHasMore(!!res.data?.next);
      setPage(nextPage);
      setMappingPending(!!res.data?.access?.mapping_pending);
      setError('');
    } catch (err: any) {
      if (id !== requestId.current) return;
      setError(err?.response?.status === 401 ? 'Please sign in again.' : 'Could not load recordings. Pull down to retry.');
    } finally {
      if (id === requestId.current) {
        setLoading(false);
        setLoadingMore(false);
        setRefreshing(false);
      }
    }
  }, [debouncedQuery]);

  useEffect(() => {
    setLoading(true);
    load(1, true);
  }, [load]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    load(1, true);
  }, [load]);

  const onEndReached = () => {
    if (!hasMore || loadingMore || loading) return;
    setLoadingMore(true);
    load(page + 1, false);
  };

  const openRecording = (item: Recording) => {
    const video = item.files.find(f => f.file_type === 'MP4');
    if (!video) return;
    navigation.navigate('HistoricalRecordingPlayer', {
      fileId: video.id,
      title: item.title || 'Class recording',
      courseTitle: item.course?.title || '',
      teacherName: item.teacher_name || '',
      actualStart: item.actual_start,
      duration: item.duration || '',
    });
  };

  const renderItem = ({ item }: { item: Recording }) => {
    const hasVideo = item.files.some(f => f.file_type === 'MP4');
    const hasSubtitles = item.files.some(f => f.file_type === 'VTT');
    const meta = [item.course?.title, item.teacher_name ? `By ${item.teacher_name}` : null].filter(Boolean).join(' · ');
    const when = [formatWhen(item.actual_start), item.duration].filter(Boolean).join(' · ');
    return (
      <TouchableOpacity
        style={styles.card}
        onPress={() => openRecording(item)}
        disabled={!hasVideo}
        accessibilityRole="button"
        accessibilityLabel={`Play recording ${item.title || ''}`}
      >
        <View style={styles.playCircle}>
          <Icon name="play" size={18} color={colors.textInverse} />
        </View>
        <View style={styles.cardBody}>
          <Text style={styles.title} numberOfLines={2}>{item.title || 'Class recording'}</Text>
          {!!meta && <Text style={styles.meta} numberOfLines={1}>{meta}</Text>}
          <View style={styles.row}>
            {!!when && <Text style={styles.when} numberOfLines={1}>{when}</Text>}
            {hasSubtitles && (
              <View style={styles.ccBadge}>
                <Text style={styles.ccText}>CC</Text>
              </View>
            )}
          </View>
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top', 'left', 'right', 'bottom']}>
      <ScreenHeader title="Earlier Recordings" onBack={() => navigation.goBack()} />

      <View style={styles.searchRow}>
        <Icon name="search" size={17} color={colors.textTertiary} style={styles.searchIcon} />
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder="Search recordings"
          placeholderTextColor={colors.textTertiary}
          style={styles.searchInput}
          accessibilityLabel="Search recordings"
          returnKeyType="search"
        />
      </View>

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={item => String(item.id)}
          renderItem={renderItem}
          onEndReached={onEndReached}
          onEndReachedThreshold={0.4}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
          contentContainerStyle={styles.listContainer}
          showsVerticalScrollIndicator={false}
          ListFooterComponent={loadingMore ? <ActivityIndicator color={colors.accent} style={{ marginVertical: spacing.lg }} /> : null}
          ListEmptyComponent={
            error ? (
              <EmptyState icon="alert-circle" title="Something went wrong" message={error} ctaLabel="Try again" onPressCta={onRefresh} />
            ) : mappingPending && !debouncedQuery ? (
              <EmptyState
                icon="film"
                title="Recordings are being set up"
                message="Your earlier class recordings are still being linked to your batch. They will appear here once that is done."
              />
            ) : (
              <EmptyState
                icon="film"
                title="No recordings found"
                message={debouncedQuery ? 'Try a different search.' : 'There are no earlier recordings for your batches yet.'}
              />
            )
          }
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  listContainer: { padding: spacing.lg, paddingTop: 0, flexGrow: 1 },
  searchRow: {
    flexDirection: 'row', alignItems: 'center', backgroundColor: colors.card,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    marginHorizontal: spacing.lg, paddingHorizontal: spacing.md, marginBottom: spacing.md,
  },
  searchIcon: { marginRight: spacing.sm },
  searchInput: { flex: 1, color: colors.text, fontSize: 14, paddingVertical: 11 },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  playCircle: {
    width: 40, height: 40, borderRadius: 20, backgroundColor: colors.accent,
    alignItems: 'center', justifyContent: 'center', marginRight: spacing.md,
  },
  cardBody: { flex: 1, minWidth: 0 },
  title: { ...typography.section, color: colors.text, fontSize: 15, fontWeight: '600' },
  meta: { ...typography.meta, color: colors.textSecondary, marginTop: 2 },
  row: { flexDirection: 'row', alignItems: 'center', marginTop: 4 },
  when: { ...typography.caption, color: colors.textTertiary, flexShrink: 1 },
  ccBadge: {
    marginLeft: spacing.sm, paddingHorizontal: 6, paddingVertical: 1, borderRadius: 4,
    borderWidth: 1, borderColor: colors.accentBorder, backgroundColor: colors.accentMuted,
  },
  ccText: { color: colors.accent, fontSize: 10, fontWeight: '700' },
});
