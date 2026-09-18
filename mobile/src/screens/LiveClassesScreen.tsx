import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, ActivityIndicator, SafeAreaView, RefreshControl, Linking } from 'react-native';
import client from '../api/client';

// Phase 4.9 -- read-only live class list + join link. Reuses
// GET courses/live-classes/ exactly as-is (already scoped server-side to
// this student's own enrolled batches -- see LiveClassViewSet.get_queryset).
function statusMeta(status: string) {
  switch (status) {
    case 'LIVE': return { label: 'Live Now', color: '#22c55e' };
    case 'SCHEDULED': return { label: 'Scheduled', color: '#60a5fa' };
    case 'COMPLETED': return { label: 'Completed', color: '#71717a' };
    case 'CANCELLED': return { label: 'Cancelled', color: '#f87171' };
    default: return { label: status, color: '#71717a' };
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

  const renderItem = ({ item }: { item: any }) => {
    const meta = statusMeta(item.status);
    const scheduled = new Date(item.scheduled_start);
    return (
      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
          <Text style={[styles.badge, { color: meta.color }]}>{meta.label}</Text>
        </View>
        <Text style={styles.datetime}>{scheduled.toLocaleString()} · {item.duration_minutes} min</Text>
        {item.status === 'LIVE' && !!item.meeting_url && (
          <TouchableOpacity style={styles.joinButton} onPress={() => Linking.openURL(item.meeting_url)}>
            <Text style={styles.joinButtonText}>Join Now</Text>
          </TouchableOpacity>
        )}
        {item.status === 'SCHEDULED' && !!item.meeting_url && (
          <TouchableOpacity style={styles.joinButtonSecondary} onPress={() => Linking.openURL(item.meeting_url)}>
            <Text style={styles.joinButtonSecondaryText}>Open Meeting Link</Text>
          </TouchableOpacity>
        )}
        {item.status === 'COMPLETED' && !!item.recording_url && (
          <TouchableOpacity style={styles.joinButtonSecondary} onPress={() => Linking.openURL(item.recording_url)}>
            <Text style={styles.joinButtonSecondaryText}>Watch Recording</Text>
          </TouchableOpacity>
        )}
      </View>
    );
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
        <Text style={styles.headerTitle}>Live Classes</Text>
      </View>
      <FlatList
        data={classes}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderItem}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#facc15" />}
        contentContainerStyle={styles.listContainer}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>No live classes scheduled yet.</Text>
          </View>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505' },
  header: { flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  backText: { color: '#facc15', fontSize: 16, marginRight: 16 },
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold' },
  listContainer: { padding: 16 },
  card: { backgroundColor: '#0a0a0a', borderRadius: 14, borderWidth: 1, borderColor: '#27272a', padding: 16, marginBottom: 12 },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  title: { color: '#fff', fontSize: 16, fontWeight: 'bold', flex: 1, marginRight: 8 },
  badge: { fontSize: 11, fontWeight: 'bold' },
  datetime: { color: '#a1a1aa', fontSize: 13, marginBottom: 12 },
  joinButton: { backgroundColor: '#facc15', paddingVertical: 10, borderRadius: 10, alignItems: 'center' },
  joinButtonText: { color: '#000', fontSize: 14, fontWeight: 'bold' },
  joinButtonSecondary: { backgroundColor: '#18181b', paddingVertical: 10, borderRadius: 10, alignItems: 'center', borderWidth: 1, borderColor: '#27272a' },
  joinButtonSecondaryText: { color: '#e4e4e7', fontSize: 14, fontWeight: '600' },
  emptyContainer: { padding: 40, alignItems: 'center' },
  emptyText: { color: '#a1a1aa', fontSize: 15, textAlign: 'center' },
});
