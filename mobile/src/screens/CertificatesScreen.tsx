import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, ActivityIndicator, SafeAreaView, RefreshControl, Linking } from 'react-native';
import client from '../api/client';

// Phase 4.9 -- read-only certificate list. Reuses GET courses/certificates/
// (owner-scoped, list+retrieve only -- see CertificateViewSet) as-is.
// Viewing/downloading a certificate opens the existing browser-print web
// page (academy.natyaarts.com/certificates/<courseId>) rather than
// reimplementing PDF rendering natively -- the exact same "defer to web"
// pattern course purchase already uses, and the certificate itself was a
// deliberate browser-print design on web (see the Phase 4.6 report), not a
// generated file this app could just download and hand off.
export default function CertificatesScreen({ navigation }: any) {
  const [certificates, setCertificates] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchCertificates = async () => {
    try {
      const res = await client.get('courses/certificates/');
      setCertificates(res.data.results || res.data || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { fetchCertificates(); }, []);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchCertificates(); }, []);

  const renderItem = ({ item }: { item: any }) => (
    <View style={styles.card}>
      <Text style={styles.courseTitle}>{item.course_title_snapshot}</Text>
      <Text style={styles.issuedDate}>Issued {new Date(item.issued_at).toLocaleDateString()}</Text>
      <Text style={styles.verificationId}>ID: {item.verification_id}</Text>
      <TouchableOpacity
        style={styles.viewButton}
        onPress={() => Linking.openURL(`https://academy.natyaarts.com/certificates/${item.course_id}`)}
      >
        <Text style={styles.viewButtonText}>View / Download</Text>
      </TouchableOpacity>
    </View>
  );

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
        <Text style={styles.headerTitle}>Certificates</Text>
      </View>
      <FlatList
        data={certificates}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderItem}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#facc15" />}
        contentContainerStyle={styles.listContainer}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyTitle}>No certificates yet</Text>
            <Text style={styles.emptyText}>Complete a course to earn your certificate.</Text>
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
  card: { backgroundColor: '#0a0a0a', borderRadius: 14, borderWidth: 1, borderColor: '#27272a', padding: 18, marginBottom: 12 },
  courseTitle: { color: '#fff', fontSize: 17, fontWeight: 'bold', marginBottom: 6 },
  issuedDate: { color: '#a1a1aa', fontSize: 13, marginBottom: 4 },
  verificationId: { color: '#52525b', fontSize: 11, marginBottom: 14 },
  viewButton: { backgroundColor: '#facc15', paddingVertical: 10, borderRadius: 10, alignItems: 'center' },
  viewButtonText: { color: '#000', fontSize: 14, fontWeight: 'bold' },
  emptyContainer: { padding: 40, alignItems: 'center' },
  emptyTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold', marginBottom: 8 },
  emptyText: { color: '#a1a1aa', fontSize: 14, textAlign: 'center' },
});
