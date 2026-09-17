import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, ActivityIndicator, SafeAreaView, RefreshControl } from 'react-native';
import client from '../api/client';

// Mobile purchase-history gap fix ("Mobile has no one-time purchase/
// payment history"). Combines the two backend flows that both represent a
// one-time (non-subscription) purchase:
//  - Order/OrderItem (the multi-item/bundle checkout) via the EXISTING
//    GET orders/orders/ -- unchanged, already used by the web /orders page.
//  - Purchase (the legacy single-course "Buy This Course" checkout) via
//    the NEW GET orders/my-purchases/ -- the first student-facing
//    endpoint for this model; it previously had none at all.
// Subscription payment history is deliberately NOT duplicated here --
// SubscriptionScreen already owns that, unchanged.
type CombinedRow =
  | { kind: 'ORDER'; id: number; order_number: string; status: string; total_amount: string; currency: string; items: any[]; has_invoice: boolean; created_at: string }
  | { kind: 'PURCHASE'; id: number; course_title: string; status: string; amount: string; has_invoice: boolean; created_at: string };

const STATUS_STYLE: Record<string, { bg: string; text: string }> = {
  PAID: { bg: 'rgba(74,222,128,0.1)', text: '#4ade80' },
  SUCCESS: { bg: 'rgba(74,222,128,0.1)', text: '#4ade80' },
  PENDING: { bg: 'rgba(250,204,21,0.1)', text: '#facc15' },
  FAILED: { bg: 'rgba(248,113,113,0.1)', text: '#f87171' },
  CANCELLED: { bg: 'rgba(113,113,122,0.15)', text: '#a1a1aa' },
  REFUNDED: { bg: 'rgba(96,165,250,0.1)', text: '#60a5fa' },
};

export default function PurchaseHistoryScreen({ navigation }: any) {
  const [rows, setRows] = useState<CombinedRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);

  const fetchHistory = async () => {
    try {
      setError(false);
      // Both endpoints return a plain (unpaginated) array -- fetched in
      // parallel and merged client-side purely for display; neither
      // amount/status/total is ever computed here, only read as-is.
      const [ordersRes, purchasesRes] = await Promise.all([
        client.get('orders/orders/'),
        client.get('orders/my-purchases/'),
      ]);

      const orderRows: CombinedRow[] = (ordersRes.data || []).map((o: any) => ({
        kind: 'ORDER', id: o.id, order_number: o.order_number, status: o.status,
        total_amount: o.total_amount, currency: o.currency, items: o.items || [],
        has_invoice: !!o.has_invoice, created_at: o.created_at,
      }));
      const purchaseRows: CombinedRow[] = (purchasesRes.data || []).map((p: any) => ({
        kind: 'PURCHASE', id: p.id, course_title: p.course_title, status: p.status,
        amount: p.amount, has_invoice: !!p.has_invoice, created_at: p.created_at,
      }));

      const combined = [...orderRows, ...purchaseRows].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      setRows(combined);
    } catch (err) {
      console.error(err);
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { fetchHistory(); }, []);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchHistory(); }, []);

  const renderItem = ({ item }: { item: CombinedRow }) => {
    const statusStyle = STATUS_STYLE[item.status] || STATUS_STYLE.PENDING;
    const title = item.kind === 'PURCHASE'
      ? item.course_title
      : item.items.length === 1
        ? (item.items[0].title_snapshot)
        : `${item.items.length} items`;
    const subtitle = item.kind === 'ORDER'
      ? item.order_number
      : 'Course Purchase';
    const amount = item.kind === 'ORDER' ? item.total_amount : item.amount;
    const currencySymbol = item.kind === 'ORDER' && item.currency !== 'INR' ? `${item.currency} ` : '₹';

    return (
      <View style={styles.card}>
        <View style={styles.cardRow}>
          <Text style={styles.subtitle}>{subtitle}</Text>
          <View style={[styles.statusBadge, { backgroundColor: statusStyle.bg }]}>
            <Text style={[styles.statusText, { color: statusStyle.text }]}>{item.status}</Text>
          </View>
        </View>
        <Text style={styles.title} numberOfLines={2}>{title}</Text>
        <View style={styles.cardFooter}>
          <View>
            <Text style={styles.amount}>{currencySymbol}{parseFloat(amount).toLocaleString()}</Text>
            <Text style={styles.date}>{new Date(item.created_at).toLocaleDateString()}</Text>
          </View>
          {item.has_invoice && (
            <TouchableOpacity style={styles.invoiceButton} onPress={() => navigation.navigate('Invoices')}>
              <Text style={styles.invoiceButtonText}>View Invoice</Text>
            </TouchableOpacity>
          )}
        </View>
      </View>
    );
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
        <Text style={styles.headerTitle}>Purchase History</Text>
        <View style={{ width: 50 }} />
      </View>
      {error ? (
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyText}>Couldn't load your purchase history.</Text>
          <TouchableOpacity style={styles.retryButton} onPress={fetchHistory}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <FlatList
          data={rows}
          keyExtractor={(item) => `${item.kind}-${item.id}`}
          renderItem={renderItem}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#facc15" />}
          contentContainerStyle={styles.listContainer}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>No purchases yet.</Text>
              <Text style={styles.emptySubtext}>Courses and bundles you buy will show up here.</Text>
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
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold' },
  listContainer: { padding: 16 },
  card: { backgroundColor: '#0a0a0a', borderRadius: 12, borderWidth: 1, borderColor: '#27272a', padding: 14, marginBottom: 10 },
  cardRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 },
  subtitle: { color: '#71717a', fontSize: 11, fontFamily: 'monospace' },
  statusBadge: { borderRadius: 20, paddingHorizontal: 8, paddingVertical: 2 },
  statusText: { fontSize: 9, fontWeight: 'bold' },
  title: { color: '#fff', fontSize: 15, fontWeight: 'bold', marginBottom: 10 },
  cardFooter: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end', paddingTop: 10, borderTopWidth: 1, borderTopColor: '#18181b' },
  amount: { color: '#facc15', fontSize: 16, fontWeight: 'bold' },
  date: { color: '#a1a1aa', fontSize: 12, marginTop: 2 },
  invoiceButton: { borderWidth: 1, borderColor: 'rgba(250,204,21,0.3)', borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6 },
  invoiceButtonText: { color: '#facc15', fontSize: 11, fontWeight: '600' },
  emptyContainer: { flex: 1, padding: 40, alignItems: 'center', justifyContent: 'center' },
  emptyText: { color: '#e4e4e7', fontSize: 15, fontWeight: '600', textAlign: 'center', marginBottom: 6 },
  emptySubtext: { color: '#71717a', fontSize: 13, textAlign: 'center' },
  retryButton: { marginTop: 16, borderWidth: 1, borderColor: '#facc15', borderRadius: 10, paddingHorizontal: 20, paddingVertical: 10 },
  retryText: { color: '#facc15', fontSize: 13, fontWeight: '700' },
});
