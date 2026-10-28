import React, { useEffect, useState, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Image,
  ActivityIndicator,
  ScrollView,
  TouchableOpacity,
  Linking,
  Alert,
  Platform,
  Modal,
  AppState,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import client, { resolveMediaUrl } from '../api/client';
import Icon from '../components/Icon';
import { colors, spacing, radius, typography, headerLayout } from '../theme';

const FALLBACK_THUMB = 'https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop';

let RNIap: any = null;
if (Platform.OS === 'ios') {
  try {
    RNIap = require('react-native-iap');
  } catch (e) {
    console.warn('react-native-iap unavailable in current runtime:', e);
  }
}

// A module's completion fields are only non-null when its content is
// actually unlocked for this user (see courses/serializers.py
// ModuleSerializer.to_representation -- locked modules get
// LOCKED_COMPLETION, whose is_completed is always None). This is a real
// per-request access signal from the API, not a guess -- used to decide
// whether to offer "Go to Course" instead of "Buy Course".
function hasCourseAccess(course: any) {
  return !!course?.modules?.some((m: any) => m.is_completed !== null);
}

export default function CourseDetailsScreen({ route, navigation }: any) {
  const { courseId } = route.params;
  const [course, setCourse] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [buying, setBuying] = useState(false);
  const [checkingPayment, setCheckingPayment] = useState(false);
  const [awaitingPayment, setAwaitingPayment] = useState(false);
  const [pendingPurchaseId, setPendingPurchaseId] = useState<number | null>(null);

  // StoreKit (iOS IAP) states
  const [iapProduct, setIapProduct] = useState<any>(null);
  const [iapLoading, setIapLoading] = useState(false);

  const awaitingPaymentRef = useRef(awaitingPayment);
  awaitingPaymentRef.current = awaitingPayment;
  const pendingPurchaseIdRef = useRef(pendingPurchaseId);
  pendingPurchaseIdRef.current = pendingPurchaseId;

  const fetchCourseDetails = async () => {
    try {
      const res = await client.get(`courses/${courseId}/`);
      setCourse(res.data);
      return res.data;
    } catch (err) {
      console.error(err);
      Alert.alert('Error', 'Could not load course details');
      navigation.goBack();
      return null;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCourseDetails();
  }, [courseId]);

  // Check payment status with backend (which queries Razorpay verification)
  const verifyPaymentAndUnlock = async (isManual: boolean = false) => {
    if (checkingPayment) return;
    setCheckingPayment(true);
    try {
      const res = await client.get('orders/check-status/', {
        params: {
          course_id: courseId,
          purchase_id: pendingPurchaseIdRef.current || undefined,
        },
      });

      if (res.data?.is_enrolled) {
        // Payment verified! Refresh course to unlock curriculum and player
        setAwaitingPayment(false);
        setPendingPurchaseId(null);
        const updatedCourse = await fetchCourseDetails();
        Alert.alert(
          'Payment Successful! 🎉',
          'Your payment has been confirmed by Razorpay and the course is now unlocked. Happy learning!',
          [
            {
              text: 'Start Learning',
              onPress: () => {
                navigation.navigate('Learn', { courseId, isEnrolled: true });
              },
            },
            { text: 'OK' },
          ]
        );
      } else if (isManual) {
        Alert.alert(
          'Payment Pending',
          'Payment has not been confirmed yet. If you have already completed the transaction, please wait a moment and tap "Verify Payment" again. The course will remain locked until confirmation is received from Razorpay.'
        );
      }
    } catch (err: any) {
      console.error('Error verifying payment:', err);
      if (isManual) {
        Alert.alert('Verification Check', 'Unable to verify payment status right now. Please try again.');
      }
    } finally {
      setCheckingPayment(false);
    }
  };

  // When student switches back to the app from Razorpay checkout, automatically check status
  useEffect(() => {
    const subscription = AppState.addEventListener('change', (nextAppState) => {
      if (nextAppState === 'active' && awaitingPaymentRef.current) {
        verifyPaymentAndUnlock(false);
      }
    });

    return () => {
      subscription.remove();
    };
  }, [courseId]);

  const appleProductId = course?.apple_product_id || `com.natyaarts.academy.course.${courseId}`;

  useEffect(() => {
    let isMounted = true;
    if (Platform.OS === 'ios' && RNIap) {
      (async () => {
        try {
          await RNIap.initConnection();
          const products = await RNIap.getProducts({ skus: [appleProductId] });
          if (isMounted && products && products.length > 0) {
            setIapProduct(products[0]);
          }
        } catch (err) {
          console.warn('Failed to fetch Apple StoreKit products:', err);
        }
      })();
    }
    return () => {
      isMounted = false;
      if (Platform.OS === 'ios' && RNIap) {
        RNIap.endConnection?.().catch(() => {});
      }
    };
  }, [appleProductId]);

  if (loading || !course) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  const thumbUrl = resolveMediaUrl(course.thumbnail) || FALLBACK_THUMB;
  const owned = hasCourseAccess(course);

  // iOS Apple StoreKit In-App Purchase Flow
  const handleApplePurchase = async () => {
    if (buying || iapLoading) return;
    setIapLoading(true);
    try {
      if (!RNIap) {
        Alert.alert(
          'In-App Purchase Notice',
          'Apple In-App Purchases require a physical iOS test device or TestFlight build.'
        );
        return;
      }
      await RNIap.initConnection();
      const purchase: any = await RNIap.requestPurchase({
        sku: appleProductId,
        andDangerouslyFinishTransactionAutomaticallyIOS: false,
      });

      const signedTx =
        purchase.signedTransactionInfo ||
        purchase.transactionReceipt ||
        purchase.jwsRepresentation ||
        purchase.transactionId;

      if (!signedTx) {
        throw new Error('No transaction receipt received from Apple.');
      }

      // Verify transaction cryptographically on backend
      const res = await client.post('orders/apple/verify-purchase/', {
        signed_transaction_info: signedTx,
        product_id: appleProductId,
      });

      if (res.status === 200) {
        await RNIap.finishTransaction({ purchase, isConsumable: false });
        await fetchCourseDetails();
        Alert.alert(
          'Course Unlocked! 🎉',
          'Your Apple purchase was verified successfully. Enjoy your learning!',
          [
            {
              text: 'Start Learning',
              onPress: () => navigation.navigate('Learn', { courseId, isEnrolled: true }),
            },
            { text: 'OK' },
          ]
        );
      } else {
        throw new Error(res.data?.error || 'Server verification failed.');
      }
    } catch (err: any) {
      if (err.code === 'E_USER_CANCELLED') {
        return;
      }
      console.error('Apple purchase error:', err);
      const errMsg = err.response?.data?.error || err.message || 'Could not complete Apple In-App Purchase.';
      Alert.alert('Purchase Error', errMsg);
    } finally {
      setIapLoading(false);
    }
  };

  // iOS Restore Purchases Flow
  const handleRestorePurchases = async () => {
    if (iapLoading) return;
    setIapLoading(true);
    try {
      if (!RNIap) {
        Alert.alert('Notice', 'Restore Purchases requires an iOS device with active App Store.');
        return;
      }
      await RNIap.initConnection();
      const purchases = await RNIap.getAvailablePurchases();
      let restoredCount = 0;

      for (const p of purchases) {
        const signedTx =
          p.signedTransactionInfo ||
          p.transactionReceipt ||
          p.jwsRepresentation ||
          p.transactionId;
        if (signedTx) {
          try {
            const res = await client.post('orders/apple/verify-purchase/', {
              signed_transaction_info: signedTx,
              product_id: p.productId,
            });
            if (res.status === 200) {
              await RNIap.finishTransaction({ purchase: p, isConsumable: false });
              restoredCount++;
            }
          } catch (e) {
            console.warn('Failed restoring transaction:', e);
          }
        }
      }

      await fetchCourseDetails();
      if (restoredCount > 0) {
        Alert.alert('Purchases Restored', `Successfully restored ${restoredCount} course purchase(s).`);
      } else {
        Alert.alert('Restore Purchases', 'No previous purchases were found for this Apple ID.');
      }
    } catch (err: any) {
      console.error('Failed to restore purchases:', err);
      Alert.alert('Restore Error', 'Could not restore purchases from Apple Store. Please try again.');
    } finally {
      setIapLoading(false);
    }
  };

  // Android & Web Razorpay payment flow (preserved untouched)
  const handleBuyCourse = async () => {
    if (buying) return;
    setBuying(true);
    try {
      // 1. Create order and obtain Razorpay checkout link from backend
      const res = await client.post('orders/create-order/', {
        course_id: courseId,
      });

      const { payment_url, purchase_id, order_id } = res.data;

      if (payment_url) {
        // 2. Redirect directly to Razorpay's official checkout URL
        setPendingPurchaseId(purchase_id);
        setAwaitingPayment(true);
        await Linking.openURL(payment_url);
      } else {
        Alert.alert(
          'Payment Notice',
          'Razorpay checkout link is currently unavailable. Please check your network or try again in a moment.'
        );
      }
    } catch (err: any) {
      console.error('Failed to create Razorpay order:', err);
      const errMsg = err.response?.data?.error || 'Could not initiate Razorpay checkout. Please try again.';
      Alert.alert('Checkout Error', errMsg);
    } finally {
      setBuying(false);
    }
  };

  const handleGoToCourse = () => {
    navigation.navigate('Learn', { courseId, isEnrolled: true });
  };

  return (
    <SafeAreaView style={styles.container} edges={['top', 'left', 'right', 'bottom']}>
      <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
        <View style={styles.artworkWrap}>
          <Image source={{ uri: thumbUrl }} style={styles.artwork} />
          <TouchableOpacity
            onPress={() => navigation.goBack()}
            style={styles.backButton}
            accessibilityRole="button"
            accessibilityLabel="Go back"
          >
            <Icon name="arrow-left" size={18} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.typeBadge}>
            <Text style={styles.typeBadgeText}>{course.course_type === 'LIVE' ? 'Live' : 'Recorded'}</Text>
          </View>
        </View>

        <View style={styles.content}>
          <Text style={styles.title}>{course.title}</Text>

          <View style={styles.metaRow}>
            {!owned && <Text style={styles.price}>₹{course.price}</Text>}
            {typeof course.total_module_count === 'number' && course.total_module_count > 0 && (
              <Text style={typography.meta}>
                {course.total_module_count} {course.total_module_count === 1 ? 'module' : 'modules'}
              </Text>
            )}
          </View>

          {typeof course.completion_percentage === 'number' && (
            <Text style={styles.progressNote}>
              {course.is_completed ? 'Course completed' : `${course.completion_percentage}% complete`}
            </Text>
          )}

          <Text style={styles.sectionTitle}>About this course</Text>
          <Text style={styles.description}>{course.description}</Text>

          <Text style={styles.sectionTitle}>Curriculum</Text>
          {course.modules?.map((mod: any, index: number) => (
            <View key={mod.id} style={styles.moduleCard}>
              <View style={styles.moduleHeaderRow}>
                <Text style={styles.moduleNumber}>{(index + 1).toString().padStart(2, '0')}</Text>
                <View style={styles.moduleInfo}>
                  <Text style={styles.moduleTitle}>{mod.title}</Text>
                  {!!mod.description && (
                    <Text style={styles.moduleDescription} numberOfLines={2}>
                      {mod.description}
                    </Text>
                  )}
                </View>
              </View>
              {mod.lessons?.map((lesson: any) => (
                <View key={lesson.id} style={styles.lessonRow}>
                  <Icon
                    name={lesson.is_completed ? 'check-circle' : lesson.is_locked ? 'lock' : 'play'}
                    size={15}
                    color={lesson.is_completed ? colors.success : lesson.is_locked ? colors.textTertiary : colors.accent}
                  />
                  <Text style={[styles.lessonText, lesson.is_locked && styles.lessonTextLocked]} numberOfLines={1}>
                    {lesson.title}
                  </Text>
                </View>
              ))}
            </View>
          ))}
        </View>
      </ScrollView>

      {/* Razorpay Verification Modal (Android only) */}
      {Platform.OS !== 'ios' && (
        <Modal
          visible={awaitingPayment}
          transparent
          animationType="fade"
          onRequestClose={() => setAwaitingPayment(false)}
        >
          <View style={styles.modalBackdrop}>
            <View style={styles.modalCard}>
              <View style={styles.modalIconWrap}>
                <Icon name="credit-card" size={28} color={colors.accent} />
              </View>
              <Text style={styles.modalTitle}>Payment in Progress</Text>
              <Text style={styles.modalDescription}>
                Complete your payment on the Razorpay screen. Your course will unlock automatically once payment is confirmed.
              </Text>

              <TouchableOpacity
                style={[styles.primaryButton, styles.modalButton]}
                onPress={() => verifyPaymentAndUnlock(true)}
                disabled={checkingPayment}
              >
                {checkingPayment ? (
                  <ActivityIndicator color={colors.textInverse} size="small" />
                ) : (
                  <>
                    <Icon name="refresh-cw" size={16} color={colors.textInverse} />
                    <Text style={styles.primaryButtonText}>Verify Payment & Unlock</Text>
                  </>
                )}
              </TouchableOpacity>

              <TouchableOpacity
                style={styles.cancelModalButton}
                onPress={() => setAwaitingPayment(false)}
                disabled={checkingPayment}
              >
                <Text style={styles.cancelModalButtonText}>Close / Check Later</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>
      )}

      <View style={styles.footer}>
        {owned ? (
          <TouchableOpacity style={styles.primaryButton} onPress={handleGoToCourse}>
            <Icon name="play" size={16} color={colors.textInverse} />
            <Text style={styles.primaryButtonText}>Go to Course</Text>
          </TouchableOpacity>
        ) : Platform.OS === 'ios' ? (
          <View style={styles.iosPurchaseContainer}>
            <TouchableOpacity
              style={[styles.primaryButton, (iapLoading || buying) && styles.disabledButton]}
              onPress={handleApplePurchase}
              disabled={iapLoading || buying}
              accessibilityRole="button"
              accessibilityLabel="Unlock course with In-App Purchase"
            >
              {iapLoading ? (
                <ActivityIndicator color={colors.textInverse} size="small" />
              ) : (
                <>
                  <Icon name="credit-card" size={16} color={colors.textInverse} />
                  <Text style={styles.primaryButtonText}>
                    {iapProduct?.localizedPrice
                      ? `Unlock Course (${iapProduct.localizedPrice})`
                      : `Unlock Course (₹${course.price})`}
                  </Text>
                </>
              )}
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.restoreButton}
              onPress={handleRestorePurchases}
              disabled={iapLoading}
              accessibilityRole="button"
              accessibilityLabel="Restore Purchases"
            >
              <Text style={styles.restoreButtonText}>Restore Purchases</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <TouchableOpacity
            style={[styles.primaryButton, buying && styles.disabledButton]}
            onPress={handleBuyCourse}
            disabled={buying}
          >
            {buying ? (
              <ActivityIndicator color={colors.textInverse} size="small" />
            ) : (
              <>
                <Icon name="credit-card" size={16} color={colors.textInverse} />
                <Text style={styles.primaryButtonText}>Buy Course for ₹{course.price}</Text>
              </>
            )}
          </TouchableOpacity>
        )}
      </View>
    </SafeAreaView>
  );
}

const ARTWORK_HEIGHT = 260;

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { justifyContent: 'center', alignItems: 'center' },

  scrollContent: { paddingBottom: 110 },

  artworkWrap: { width: '100%', height: ARTWORK_HEIGHT, position: 'relative', backgroundColor: colors.card },
  artwork: { width: '100%', height: '100%', resizeMode: 'cover' },
  backButton: {
    position: 'absolute',
    top: spacing.lg + headerLayout.topGap,
    left: spacing.lg,
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: colors.scrim,
    alignItems: 'center',
    justifyContent: 'center',
  },
  typeBadge: {
    position: 'absolute',
    top: spacing.lg + headerLayout.topGap,
    right: spacing.lg,
    backgroundColor: colors.scrim,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
  },
  typeBadgeText: { color: colors.text, fontSize: 11, fontWeight: '700', letterSpacing: 0.4 },

  content: { padding: spacing.lg },
  title: { ...typography.hero, marginBottom: spacing.sm },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, marginBottom: spacing.sm },
  price: { color: colors.accent, fontSize: 20, fontWeight: '700', marginRight: spacing.md },
  progressNote: { ...typography.meta, color: colors.accent, marginBottom: spacing.lg },

  sectionTitle: { ...typography.section, marginTop: spacing.xl, marginBottom: spacing.md },
  description: { ...typography.bodyRegular, lineHeight: 22 },

  moduleCard: {
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.lg,
    marginBottom: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  moduleHeaderRow: { flexDirection: 'row', marginBottom: spacing.sm },
  moduleNumber: { color: colors.accent, fontSize: 16, fontWeight: '700', width: 34 },
  moduleInfo: { flex: 1 },
  moduleTitle: { ...typography.body },
  moduleDescription: { ...typography.meta, marginTop: 2 },

  lessonRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.sm, gap: spacing.sm },
  lessonText: { ...typography.bodyRegular, color: colors.text, flex: 1, marginLeft: spacing.sm },
  lessonTextLocked: { color: colors.textTertiary },

  footer: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    padding: spacing.lg,
    backgroundColor: colors.bg,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  primaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    backgroundColor: colors.accent,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
  },
  disabledButton: {
    opacity: 0.7,
  },
  primaryButtonText: { color: colors.textInverse, fontSize: 16, fontWeight: '700', marginLeft: spacing.sm },

  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.75)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.xl,
  },
  modalCard: {
    width: '100%',
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.xl,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: colors.border,
  },
  modalIconWrap: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: 'rgba(250, 204, 21, 0.1)',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.md,
  },
  modalTitle: {
    ...typography.section,
    color: colors.text,
    textAlign: 'center',
    marginBottom: spacing.sm,
  },
  modalDescription: {
    ...typography.bodyRegular,
    color: colors.textSecondary,
    textAlign: 'center',
    lineHeight: 20,
    marginBottom: spacing.xl,
  },
  modalButton: {
    width: '100%',
    marginBottom: spacing.md,
  },
  cancelModalButton: {
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
  },
  cancelModalButtonText: {
    ...typography.meta,
    color: colors.textSecondary,
  },
  iosPurchaseContainer: {
    width: '100%',
    alignItems: 'center',
  },
  restoreButton: {
    marginTop: spacing.sm,
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.md,
  },
  restoreButtonText: {
    ...typography.meta,
    color: colors.accent,
    fontWeight: '600',
    textAlign: 'center',
  },
});
