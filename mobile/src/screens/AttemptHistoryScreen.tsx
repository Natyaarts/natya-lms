import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, ActivityIndicator, SafeAreaView, RefreshControl } from 'react-native';
import client from '../api/client';

// Mobile assessment attempt-history gap fix. Backend already fully
// supports this -- GET courses/assessment-attempts/my/?assessment_id=<id>
// (owner-scoped, paginated, AssessmentAttemptListSerializer) is the exact
// same endpoint the web attempt page already uses for its "View Previous
// Attempts" table. Reused here completely unchanged -- no new backend
// endpoint, no new business logic. Tapping a row navigates to the
// EXISTING AssessmentAttempt screen with that historical attempt's id --
// that screen already renders full review mode (frozen Phase 4.4 snapshot
// data: is_correct_answer/marks_awarded from AssessmentAnswerOptionSnapshot,
// never recalculated from live question/option data) for any
// SUBMITTED/TIMED_OUT attempt it's given, so no changes were needed there.
const STATUS_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  SUBMITTED: { bg: 'rgba(250,204,21,0.1)', text: '#facc15', label: 'Submitted' },
  IN_PROGRESS: { bg: 'rgba(96,165,250,0.1)', text: '#60a5fa', label: 'In Progress' },
  TIMED_OUT: { bg: 'rgba(113,113,122,0.15)', text: '#a1a1aa', label: 'Timed Out' },
};

export default function AttemptHistoryScreen({ route, navigation }: any) {
  const { assessmentId, assessmentTitle } = route.params;
  const [attempts, setAttempts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const [nextPage, setNextPage] = useState<string | null>(null);

  const fetchHistory = async () => {
    try {
      setError(false);
      const res = await client.get(`courses/assessment-attempts/my/?assessment_id=${assessmentId}`);
      setAttempts(res.data.results || []);
      setNextPage(res.data.next || null);
    } catch (err) {
      console.error(err);
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { fetchHistory(); }, [assessmentId]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchHistory(); }, [assessmentId]);

  const loadMore = async () => {
    if (!nextPage) return;
    try {
      // Absolute URL from DRF pagination -- axios uses it as-is, ignoring
      // baseURL, same pattern already established in InvoicesScreen.tsx.
      const res = await client.get(nextPage);
      setAttempts((prev) => [...prev, ...(res.data.results || [])]);
      setNextPage(res.data.next || null);
    } catch (err) {
      console.error(err);
    }
  };

  const renderItem = ({ item, index }: { item: any; index: number }) => {
    const statusStyle = STATUS_STYLE[item.status] || STATUS_STYLE.SUBMITTED;
    const dateStr = item.submitted_at || item.started_at;
    const isLatest = index === 0;

    return (
      <TouchableOpacity
        style={styles.card}
        onPress={() => navigation.navigate('AssessmentAttempt', { attemptId: item.id })}
      >
        <View style={styles.cardRow}>
          <View style={styles.attemptLabelRow}>
            <Text style={styles.attemptLabel}>Attempt {item.attempt_number}</Text>
            {isLatest && (
              <View style={styles.latestBadge}><Text style={styles.latestBadgeText}>LATEST</Text></View>
            )}
          </View>
          <View style={[styles.statusBadge, { backgroundColor: statusStyle.bg }]}>
            <Text style={[styles.statusText, { color: statusStyle.text }]}>{statusStyle.label}</Text>
          </View>
        </View>

        <Text style={styles.date}>{dateStr ? new Date(dateStr).toLocaleString() : '—'}</Text>

        <View style={styles.cardFooter}>
          <View style={styles.scoreGroup}>
            {item.score !== null && item.score !== undefined && (
              <Text style={styles.scoreText}>{item.score} pts</Text>
            )}
            {item.percentage !== null && item.percentage !== undefined && (
              <Text style={styles.percentageText}>{item.percentage}%</Text>
            )}
            {item.passed !== null && item.passed !== undefined && (
              <View style={[styles.passBadge, item.passed ? styles.passBadgeSuccess : styles.passBadgeFail]}>
                <Text style={[styles.passBadgeText, { color: item.passed ? '#4ade80' : '#f87171' }]}>
                  {item.passed ? 'PASSED' : 'FAILED'}
                </Text>
              </View>
            )}
          </View>
          {item.can_review && <Text style={styles.reviewArrow}>Review ›</Text>}
        </View>
      </TouchableOpacity>
    );
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
        <Text style={styles.headerTitle} numberOfLines={1}>Attempt History</Text>
        <View style={{ width: 50 }} />
      </View>
      {!!assessmentTitle && <Text style={styles.subtitle} numberOfLines={1}>{assessmentTitle}</Text>}

      {error ? (
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyText}>Couldn't load your attempt history.</Text>
          <TouchableOpacity style={styles.retryButton} onPress={fetchHistory}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <FlatList
          data={attempts}
          keyExtractor={(item) => item.id.toString()}
          renderItem={renderItem}
          onEndReached={loadMore}
          onEndReachedThreshold={0.4}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#facc15" />}
          contentContainerStyle={styles.listContainer}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>No previous attempts yet.</Text>
            </View>
          }
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 16, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  backText: { color: '#facc15', fontSize: 16 },
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold', flex: 1, textAlign: 'center' },
  subtitle: { color: '#71717a', fontSize: 13, textAlign: 'center', paddingTop: 10, paddingHorizontal: 20 },
  listContainer: { padding: 16 },
  card: { backgroundColor: '#0a0a0a', borderRadius: 12, borderWidth: 1, borderColor: '#27272a', padding: 14, marginBottom: 10 },
  cardRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 },
  attemptLabelRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  attemptLabel: { color: '#fff', fontSize: 15, fontWeight: 'bold' },
  latestBadge: { backgroundColor: '#facc15', borderRadius: 20, paddingHorizontal: 6, paddingVertical: 2 },
  latestBadgeText: { color: '#000', fontSize: 9, fontWeight: 'bold' },
  statusBadge: { borderRadius: 20, paddingHorizontal: 8, paddingVertical: 2 },
  statusText: { fontSize: 9, fontWeight: 'bold' },
  date: { color: '#a1a1aa', fontSize: 12, marginBottom: 10 },
  cardFooter: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingTop: 10, borderTopWidth: 1, borderTopColor: '#18181b' },
  scoreGroup: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  scoreText: { color: '#e4e4e7', fontSize: 13, fontWeight: '600' },
  percentageText: { color: '#facc15', fontSize: 15, fontWeight: 'bold' },
  passBadge: { borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2, borderWidth: 1 },
  passBadgeSuccess: { backgroundColor: 'rgba(74,222,128,0.1)', borderColor: 'rgba(74,222,128,0.2)' },
  passBadgeFail: { backgroundColor: 'rgba(248,113,113,0.1)', borderColor: 'rgba(248,113,113,0.2)' },
  passBadgeText: { fontSize: 9, fontWeight: 'bold' },
  reviewArrow: { color: '#facc15', fontSize: 12, fontWeight: '600' },
  emptyContainer: { flex: 1, padding: 40, alignItems: 'center', justifyContent: 'center' },
  emptyText: { color: '#e4e4e7', fontSize: 15, fontWeight: '600', textAlign: 'center' },
  retryButton: { marginTop: 16, borderWidth: 1, borderColor: '#facc15', borderRadius: 10, paddingHorizontal: 20, paddingVertical: 10 },
  retryText: { color: '#facc15', fontSize: 13, fontWeight: '700' },
});
