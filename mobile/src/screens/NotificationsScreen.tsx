import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, ActivityIndicator, SafeAreaView, RefreshControl } from 'react-native';
import client from '../api/client';

// Phase 4.9 -- read-only notification list + mark-as-read, reusing the
// existing NotificationViewSet exactly (GET api/notifications/,
// POST api/notifications/<id>/read/, POST api/notifications/read-all/).
export default function NotificationsScreen({ navigation }: any) {
  const [notifications, setNotifications] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchNotifications = async () => {
    try {
      const res = await client.get('notifications/');
      setNotifications(res.data.results || res.data || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { fetchNotifications(); }, []);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchNotifications(); }, []);

  const handleMarkRead = async (item: any) => {
    if (item.is_read) return;
    setNotifications((prev) => prev.map((n) => (n.id === item.id ? { ...n, is_read: true } : n)));
    try {
      await client.post(`notifications/${item.id}/read/`);
    } catch (err) {
      console.error(err);
    }
  };

  const handleMarkAllRead = async () => {
    setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
    try {
      await client.post('notifications/read-all/');
    } catch (err) {
      console.error(err);
    }
  };

  const renderItem = ({ item }: { item: any }) => (
    <TouchableOpacity style={[styles.card, !item.is_read && styles.cardUnread]} onPress={() => handleMarkRead(item)}>
      <View style={styles.cardRow}>
        {!item.is_read && <View style={styles.unreadDot} />}
        <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
      </View>
      <Text style={styles.body}>{item.body}</Text>
      <Text style={styles.time}>{new Date(item.created_at).toLocaleString()}</Text>
    </TouchableOpacity>
  );

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
        <Text style={styles.headerTitle}>Notifications</Text>
        <TouchableOpacity onPress={handleMarkAllRead}><Text style={styles.markAllText}>Mark all read</Text></TouchableOpacity>
      </View>
      <FlatList
        data={notifications}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderItem}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#facc15" />}
        contentContainerStyle={styles.listContainer}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>You're all caught up.</Text>
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
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold', flex: 1 },
  markAllText: { color: '#facc15', fontSize: 12, fontWeight: '600' },
  listContainer: { padding: 16 },
  card: { backgroundColor: '#0a0a0a', borderRadius: 12, borderWidth: 1, borderColor: '#27272a', padding: 14, marginBottom: 10 },
  cardUnread: { borderColor: 'rgba(250,204,21,0.4)' },
  cardRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 4 },
  unreadDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: '#facc15', marginRight: 8 },
  title: { color: '#fff', fontSize: 14, fontWeight: 'bold', flex: 1 },
  body: { color: '#a1a1aa', fontSize: 13, lineHeight: 18, marginBottom: 6 },
  time: { color: '#52525b', fontSize: 11 },
  emptyContainer: { padding: 40, alignItems: 'center' },
  emptyText: { color: '#a1a1aa', fontSize: 15, textAlign: 'center' },
});
