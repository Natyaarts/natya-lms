import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, ActivityIndicator, SafeAreaView, RefreshControl } from 'react-native';
import client from '../api/client';

// Invoice visibility frontend gap fix. A small, consistent extension --
// mirrors NotificationsScreen/CertificatesScreen's exact pattern -- reusing
// the EXISTING GET api/finance/my-invoices/ API unchanged (the same
// endpoint the web /invoices page uses). No PDF/print capability on
// mobile (none exists anywhere in this codebase to reuse -- see the web
// print page's own comment); this screen is read-only visibility only,
// matching the "small, consistent extension" instruction.
const SOURCE_TYPE_LABEL: Record<string, string> = {
  PURCHASE: 'Course Purchase',
  ORDER: 'Order Checkout',
  SUBSCRIPTION_PAYMENT: 'Subscription Payment',
};

export default function InvoicesScreen({ navigation }: any) {
  const [invoices, setInvoices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [nextPage, setNextPage] = useState<string | null>(null);

  const fetchInvoices = async () => {
    try {
      const res = await client.get('finance/my-invoices/');
      setInvoices(res.data.results || []);
      setNextPage(res.data.next || null);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { fetchInvoices(); }, []);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchInvoices(); }, []);

  const loadMore = async () => {
    if (!nextPage) return;
    try {
      // nextPage is the full absolute URL DRF pagination returns -- axios
      // uses an absolute URL as-is (ignoring baseURL) when one is passed,
      // so this works without any URL surgery.
      const res = await client.get(nextPage);
      setInvoices((prev) => [...prev, ...(res.data.results || [])]);
      setNextPage(res.data.next || null);
    } catch (err) {
      console.error(err);
    }
  };

  const renderItem = ({ item }: { item: any }) => (
    <View style={styles.card}>
      <View style={styles.cardRow}>
        <Text style={styles.invoiceNumber}>{item.invoice_number}</Text>
        <View style={[styles.statusBadge, item.status === 'CANCELLED' && styles.statusBadgeCancelled]}>
          <Text style={styles.statusText}>{item.status}</Text>
        </View>
      </View>
      <Text style={styles.forLabel}>{item.source_label || SOURCE_TYPE_LABEL[item.source_type] || '—'}</Text>
      <Text style={styles.sourceType}>{SOURCE_TYPE_LABEL[item.source_type] || item.source_type}</Text>
      <View style={styles.cardFooter}>
        <Text style={styles.amount}>{item.currency === 'INR' ? '₹' : `${item.currency} `}{parseFloat(item.amount).toLocaleString()}</Text>
        <Text style={styles.date}>{new Date(item.payment_date).toLocaleDateString()}</Text>
      </View>
    </View>
  );

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
        <Text style={styles.headerTitle}>Invoices</Text>
        <View style={{ width: 50 }} />
      </View>
      <FlatList
        data={invoices}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderItem}
        onEndReached={loadMore}
        onEndReachedThreshold={0.4}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#facc15" />}
        contentContainerStyle={styles.listContainer}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>No invoices yet.</Text>
            <Text style={styles.emptySubtext}>A receipt appears here automatically after a successful payment.</Text>
          </View>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 16, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  backText: { color: '#facc15', fontSize: 16 },
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold' },
  listContainer: { padding: 16 },
  card: { backgroundColor: '#0a0a0a', borderRadius: 12, borderWidth: 1, borderColor: '#27272a', padding: 14, marginBottom: 10 },
  cardRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 },
  invoiceNumber: { color: '#a1a1aa', fontSize: 11, fontFamily: 'monospace' },
  statusBadge: { backgroundColor: 'rgba(74,222,128,0.1)', borderWidth: 1, borderColor: 'rgba(74,222,128,0.2)', borderRadius: 20, paddingHorizontal: 8, paddingVertical: 2 },
  statusBadgeCancelled: { backgroundColor: '#18181b', borderColor: '#27272a' },
  statusText: { color: '#4ade80', fontSize: 9, fontWeight: 'bold' },
  forLabel: { color: '#fff', fontSize: 15, fontWeight: 'bold', marginBottom: 2 },
  sourceType: { color: '#71717a', fontSize: 11, marginBottom: 10 },
  cardFooter: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingTop: 10, borderTopWidth: 1, borderTopColor: '#18181b' },
  amount: { color: '#facc15', fontSize: 16, fontWeight: 'bold' },
  date: { color: '#a1a1aa', fontSize: 12 },
  emptyContainer: { padding: 40, alignItems: 'center' },
  emptyText: { color: '#e4e4e7', fontSize: 15, fontWeight: '600', textAlign: 'center', marginBottom: 6 },
  emptySubtext: { color: '#71717a', fontSize: 13, textAlign: 'center' },
});
