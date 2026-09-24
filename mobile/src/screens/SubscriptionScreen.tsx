import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, SafeAreaView, ActivityIndicator, ScrollView, Linking, Alert, Platform } from 'react-native';
import client from '../api/client';

// Phase 4.9 -- subscription status/AutoPay/payment-history screen. Reuses
// orders/subscriptions/me, orders/subscription-plans, orders/subscriptions/
// payments, and orders/subscriptions/cancel exactly as they already exist
// (Phase 3.4/4.8). Subscribing to a NEW plan opens the web checkout, same
// "bypass Play Billing's 30% fee" pattern CourseDetailsScreen/LearnScreen
// already use for course purchase -- no native Razorpay SDK is added here.
const STATUS_LABEL: Record<string, string> = {
  ACTIVE: 'Active', AUTHENTICATED: 'Authenticated', CREATED: 'Created',
  PENDING: 'Payment Issue -- Retrying', HALTED: 'Payment Issue',
  CANCELLED: 'Cancelled', EXPIRED: 'Expired', COMPLETED: 'Completed',
};
const GRACE_STATUSES = new Set(['PENDING', 'HALTED']);

export default function SubscriptionScreen({ navigation }: any) {
  const [subscription, setSubscription] = useState<any>(null);
  const [hasSubscription, setHasSubscription] = useState(true);
  const [plans, setPlans] = useState<any[]>([]);
  const [payments, setPayments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);

  const load = async () => {
    try {
      const [meRes, paymentsRes] = await Promise.allSettled([
        client.get('orders/subscriptions/me/'),
        client.get('orders/subscriptions/payments/'),
      ]);
      if (meRes.status === 'fulfilled') {
        setSubscription(meRes.value.data);
        setHasSubscription(true);
      } else {
        setHasSubscription(false);
        // Not paginated (SubscriptionPlanViewSet has no pagination_class
        // and no global DRF default is configured) -- a plain array.
        const plansRes = await client.get('orders/subscription-plans/');
        setPlans(plansRes.data || []);
      }
      if (paymentsRes.status === 'fulfilled') {
        setPayments(paymentsRes.value.data.results || []);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleCancel = () => {
    Alert.alert('Cancel Subscription', 'Your access will continue until the end of the current billing period. Continue?', [
      { text: 'Keep Subscription', style: 'cancel' },
      {
        text: 'Cancel Subscription', style: 'destructive', onPress: async () => {
          setCancelling(true);
          try {
            const res = await client.post('orders/subscriptions/cancel/');
            setSubscription(res.data);
          } catch (err: any) {
            Alert.alert('Error', err.response?.data?.error || 'Could not cancel your subscription.');
          } finally {
            setCancelling(false);
          }
        },
      },
    ]);
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
        <Text style={styles.headerTitle}>Subscription</Text>
      </View>
      <ScrollView contentContainerStyle={styles.content}>
        {hasSubscription && subscription ? (
          <View style={styles.statusCard}>
            <View style={styles.statusRow}>
              <Text style={styles.planName}>{subscription.plan?.name}</Text>
              <Text style={[styles.statusBadge, GRACE_STATUSES.has(subscription.status) && styles.statusBadgeWarn]}>
                {STATUS_LABEL[subscription.status] || subscription.status}
              </Text>
            </View>
            <Text style={styles.planPrice}>₹{subscription.plan?.price} / {subscription.plan?.billing_interval === 'MONTHLY' ? 'month' : 'year'}</Text>

            {GRACE_STATUSES.has(subscription.status) && subscription.effective_access_until && (
              <View style={styles.graceBox}>
                <Text style={styles.graceText}>
                  We couldn't process your last payment. Your access continues until{' '}
                  {new Date(subscription.effective_access_until).toLocaleDateString()} while we retry.
                </Text>
              </View>
            )}

            {subscription.cancel_at_period_end ? (
              <Text style={styles.cancelledNote}>
                Cancelled -- access continues until {subscription.current_period_end ? new Date(subscription.current_period_end).toLocaleDateString() : 'the end of this period'}.
              </Text>
            ) : (
              <TouchableOpacity style={styles.cancelButton} onPress={handleCancel} disabled={cancelling}>
                {cancelling ? <ActivityIndicator color="#f87171" /> : <Text style={styles.cancelButtonText}>Cancel Subscription</Text>}
              </TouchableOpacity>
            )}
          </View>
        ) : (
          <View style={styles.plansSection}>
            <Text style={styles.sectionTitle}>Subscription Plans</Text>
            {plans.length === 0 ? (
              <Text style={styles.emptyText}>No subscription plans available right now.</Text>
            ) : plans.map((plan: any) => (
              <View key={plan.id} style={styles.planCard}>
                <Text style={styles.planName}>{plan.name}</Text>
                <Text style={styles.planPrice}>₹{plan.price} / {plan.billing_interval === 'MONTHLY' ? 'month' : 'year'}</Text>
                {!!plan.description && <Text style={styles.planDescription}>{plan.description}</Text>}
                {Platform.OS === 'android' ? (
                  <TouchableOpacity
                    style={styles.subscribeButton}
                    onPress={() => Linking.openURL('https://academy.natyaarts.com/subscriptions')}
                  >
                    <Text style={styles.subscribeButtonText}>Subscribe via Web</Text>
                  </TouchableOpacity>
                ) : (
                  <View style={styles.iosInfoBox}>
                    <Text style={styles.iosInfoText}>
                      Subscriptions can be managed on our website. Active plans will appear here automatically.
                    </Text>
                  </View>
                )}
              </View>
            ))}
          </View>
        )}

        {payments.length > 0 && (
          <View style={styles.historySection}>
            <Text style={styles.sectionTitle}>Payment History</Text>
            {payments.map((p: any) => (
              <View key={p.id} style={styles.paymentRow}>
                <Text style={styles.paymentDate}>{p.paid_at ? new Date(p.paid_at).toLocaleDateString() : new Date(p.created_at).toLocaleDateString()}</Text>
                <Text style={styles.paymentAmount}>₹{p.amount}</Text>
                <Text style={[styles.paymentStatus, p.status === 'SUCCESS' && styles.paymentStatusSuccess]}>{p.status}</Text>
              </View>
            ))}
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505' },
  header: { flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  backText: { color: '#facc15', fontSize: 16, marginRight: 16 },
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold' },
  content: { padding: 20 },

  statusCard: { backgroundColor: '#0a0a0a', borderRadius: 14, borderWidth: 1, borderColor: '#27272a', padding: 18, marginBottom: 24 },
  statusRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  planName: { color: '#fff', fontSize: 18, fontWeight: 'bold' },
  statusBadge: { color: '#22c55e', fontSize: 11, fontWeight: 'bold' },
  statusBadgeWarn: { color: '#fb923c' },
  planPrice: { color: '#facc15', fontSize: 15, fontWeight: '600', marginBottom: 12 },
  planDescription: { color: '#a1a1aa', fontSize: 13, marginBottom: 12, lineHeight: 18 },
  graceBox: { backgroundColor: 'rgba(251,146,60,0.1)', borderRadius: 10, padding: 12, marginBottom: 12 },
  graceText: { color: '#fed7aa', fontSize: 12, lineHeight: 17 },
  cancelledNote: { color: '#71717a', fontSize: 12, marginTop: 4 },
  cancelButton: { borderWidth: 1, borderColor: '#f87171', borderRadius: 10, paddingVertical: 10, alignItems: 'center', marginTop: 4 },
  cancelButtonText: { color: '#f87171', fontSize: 13, fontWeight: '600' },

  plansSection: { marginBottom: 24 },
  sectionTitle: { color: '#fff', fontSize: 16, fontWeight: 'bold', marginBottom: 12 },
  planCard: { backgroundColor: '#0a0a0a', borderRadius: 14, borderWidth: 1, borderColor: '#27272a', padding: 16, marginBottom: 12 },
  subscribeButton: { backgroundColor: '#facc15', paddingVertical: 10, borderRadius: 10, alignItems: 'center' },
  subscribeButtonText: { color: '#000', fontSize: 13, fontWeight: 'bold' },
  emptyText: { color: '#a1a1aa', fontSize: 14 },
  iosInfoBox: {
    backgroundColor: '#141414',
    borderRadius: 10,
    padding: 12,
    borderWidth: 1,
    borderColor: '#27272a',
    marginTop: 4,
  },
  iosInfoText: {
    color: '#a1a1aa',
    fontSize: 13,
    lineHeight: 18,
    textAlign: 'center',
  },

  historySection: {},
  paymentRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: '#0a0a0a', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 6 },
  paymentDate: { color: '#a1a1aa', fontSize: 12, flex: 1 },
  paymentAmount: { color: '#e4e4e7', fontSize: 13, fontWeight: '600', marginHorizontal: 8 },
  paymentStatus: { color: '#f87171', fontSize: 11, fontWeight: 'bold' },
  paymentStatusSuccess: { color: '#22c55e' },
});
