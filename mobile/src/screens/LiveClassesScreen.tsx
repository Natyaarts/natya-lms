import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, ActivityIndicator, RefreshControl, Linking } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import client from '../api/client';
import ScreenHeader from '../components/ScreenHeader';
import EmptyState from '../components/EmptyState';
import { colors, spacing, radius, typography } from '../theme';

// Phase 4.9 -- read-only live class list + join link. Reuses
// GET courses/live-classes/ exactly as-is (already scoped server-side to
// this student's own enrolled batches -- see LiveClassViewSet.get_queryset).
function statusMeta(status: string) {
  switch (status) {
    case 'LIVE': return { label: 'Live Now', color: colors.success };
    case 'SCHEDULED': return { label: 'Scheduled', color: colors.info };
    case 'COMPLETED': return { label: 'Completed', color: colors.textTertiary };
    case 'CANCELLED': return { label: 'Cancelled', color: colors.danger };
    default: return { label: status, color: colors.textTertiary };
  }
}

export default function LiveClassesScreen({ navigation }: any) {
  const [classes, setClasses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchClasses = async () => {
    try {
      const res = await client.get('courses/live-classes/');
      setClasses(res.data.results || res.data || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { fetchClasses(); }, []);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchClasses(); }, []);

  const handleJoin = async (item: any) => {
    try {
      const res = await client.post(`courses/live-classes/${item.id}/join/`);
      const targetUrl = res.data?.meeting_url || item.meeting_url;
      if (targetUrl) {
        Linking.openURL(targetUrl);
        return;
      }
    } catch (err) {
      // fallback to regular meeting link
    }
    if (item.meeting_url) {
      Linking.openURL(item.meeting_url);
    }
  };

  const renderItem = ({ item }: { item: any }) => {
    const meta = statusMeta(item.status);
    const scheduled = item.scheduled_start ? new Date(item.scheduled_start) : null;
    const dateText = scheduled && !isNaN(scheduled.getTime())
      ? `${scheduled.toLocaleString()} · ${item.duration_minutes || 0} min`
      : `${item.duration_minutes || 0} min`;
    const subtitle = [item.course_title || item.batch_name, item.instructor_name ? `By ${item.instructor_name}` : null]
      .filter(Boolean)
      .join(' · ');

    return (
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
          <View style={[styles.badgeContainer, { backgroundColor: `${meta.color}1A`, borderColor: `${meta.color}4D` }]}>
            <Text style={[styles.badge, { color: meta.color }]}>{meta.label}</Text>
          </View>
        </View>
        {!!subtitle && (
          <Text style={styles.subtitle} numberOfLines={1}>{subtitle}</Text>
        )}
        <Text style={styles.datetime}>{dateText}</Text>
        {item.status === 'LIVE' && !!item.meeting_url && (
          <TouchableOpacity
            style={styles.joinButton}
            onPress={() => handleJoin(item)}
            accessibilityRole="button"
            accessibilityLabel="Join Live Class Now"
          >
            <Text style={styles.joinButtonText}>Join Now</Text>
          </TouchableOpacity>
        )}
        {item.status === 'SCHEDULED' && !!item.meeting_url && (
          <TouchableOpacity
            style={styles.joinButtonSecondary}
            onPress={() => handleJoin(item)}
            accessibilityRole="button"
            accessibilityLabel="Open Meeting Link"
          >
            <Text style={styles.joinButtonSecondaryText}>Open Meeting Link</Text>
          </TouchableOpacity>
        )}
        {item.status === 'COMPLETED' && !!item.recording_url && (
          <TouchableOpacity
            style={styles.joinButtonSecondary}
            onPress={() => Linking.openURL(item.recording_url)}
            accessibilityRole="button"
            accessibilityLabel="Watch Class Recording"
          >
            <Text style={styles.joinButtonSecondaryText}>Watch Recording</Text>
          </TouchableOpacity>
        )}
      </View>
    );
  };

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top', 'left', 'right', 'bottom']}>
      <ScreenHeader title="Live Classes" onBack={() => navigation.goBack()} />
      <FlatList
        data={classes}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderItem}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
        contentContainerStyle={styles.listContainer}
        showsVerticalScrollIndicator={false}
        ListEmptyComponent={
          <EmptyState
            icon="video"
            title="No live classes scheduled"
            message="When an instructor schedules a live workshop or interactive session for your batches, it will appear here."
          />
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.bg },
  listContainer: { padding: spacing.lg, flexGrow: 1 },
  card: {
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    marginBottom: spacing.md,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.xs,
  },
  title: {
    ...typography.section,
    color: colors.text,
    fontSize: 16,
    fontWeight: '600',
    flex: 1,
    marginRight: spacing.sm,
  },
  badgeContainer: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.pill,
    borderWidth: 1,
  },
  badge: {
    fontSize: 11,
    fontWeight: '700',
  },
  subtitle: {
    ...typography.meta,
    color: colors.textSecondary,
    marginBottom: spacing.xs,
  },
  datetime: {
    ...typography.caption,
    color: colors.textTertiary,
    marginBottom: spacing.md,
  },
  joinButton: {
    backgroundColor: colors.accent,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    alignItems: 'center',
  },
  joinButtonText: {
    color: colors.textInverse,
    fontSize: 14,
    fontWeight: '700',
  },
  joinButtonSecondary: {
    backgroundColor: colors.cardAlt,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: colors.border,
  },
  joinButtonSecondaryText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: '600',
  },
});
