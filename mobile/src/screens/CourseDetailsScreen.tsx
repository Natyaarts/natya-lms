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

import {
  fetchAppleProduct,
  requestApplePurchase,
  restorePurchases,
  showRestoreFeedback,
  subscribeIap,
  recoverUnfinishedTransactions,
  isUserCancellation,
} from '../api/appleIap';

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
  const [iapFetching, setIapFetching] = useState(Platform.OS === 'ios');

  const awaitingPaymentRef = useRef(awaitingPayment);
  awaitingPaymentRef.current = awaitingPayment;
  const pendingPurchaseIdRef = useRef(pendingPurchaseId);
  pendingPurchaseIdRef.current = pendingPurchaseId;

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const fetchCourseDetails = async () => {
    try {
      const res = await client.get(`courses/${courseId}/`);
      if (isMountedRef.current) {
        setCourse(res.data);
      }
      return res.data;
    } catch (err) {
      console.error(err);
      if (isMountedRef.current) {
        Alert.alert('Error', 'Could not load course details');
        navigation.goBack();
      }
      return null;
    } finally {
      if (isMountedRef.current) {
        setLoading(false);
      }
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
        if (isMountedRef.current) {
          setAwaitingPayment(false);
          setPendingPurchaseId(null);
        }
        await fetchCourseDetails();
        if (isMountedRef.current) {
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
        }
      } else if (isManual && isMountedRef.current) {
        Alert.alert(
          'Payment Pending',
          'Payment has not been confirmed yet. If you have already completed the transaction, please wait a moment and tap "Verify Payment" again. The course will remain locked until confirmation is received from Razorpay.'
        );
      }
    } catch (err: any) {
      console.error('Error verifying payment:', err);
      if (isManual && isMountedRef.current) {
        Alert.alert('Verification Check', 'Unable to verify payment status right now. Please try again.');
      }
    } finally {
      if (isMountedRef.current) {
        setCheckingPayment(false);
      }
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
    if (Platform.OS === 'ios') {
      (async () => {
        try {
          if (isMounted && isMountedRef.current) setIapFetching(true);
          const product = await fetchAppleProduct(appleProductId);
          if (isMounted && isMountedRef.current) {
            setIapProduct(product);
          }
        } catch (err) {
          console.warn('Failed to fetch Apple StoreKit products:', err);
          if (isMounted && isMountedRef.current) setIapProduct(null);
        } finally {
          if (isMounted && isMountedRef.current) setIapFetching(false);
        }
      })();
    }
    return () => {
      isMounted = false;
    };
  }, [appleProductId]);

  useEffect(() => {
    if (Platform.OS !== 'ios') return;
    let isMounted = true;

    // 1. Register subscriber FIRST so any event emitted during recovery or startup is captured
    const unsubscribe = subscribeIap({
      onSuccess: async (_purchase, _resData) => {
        if (!isMounted || !isMountedRef.current) return;
        setIapLoading(false);
        await fetchCourseDetails();
        if (!isMounted || !isMountedRef.current) return;
        Alert.alert(
          'Course Unlocked! 🎉',
          'Your Apple purchase was verified successfully. Enjoy your learning!',
          [
            {
              text: 'Start Learning',
              onPress: () => {
                if (isMountedRef.current) {
                  navigation.navigate('Learn', { courseId, isEnrolled: true });
                }
              },
            },
            { text: 'OK' },
          ]
        );
      },
      onError: (err: any) => {
        if (!isMounted || !isMountedRef.current) return;
        setIapLoading(false);
        if (isUserCancellation(err)) {
          return;
        }
        console.error('Apple purchase event error:', err);
        const errMsg = err?.response?.data?.error || err?.message || 'Could not complete Apple In-App Purchase.';
        if (isMountedRef.current) {
          Alert.alert('Purchase Error', errMsg);
        }
      },
    });

    // 2. Start recovery AFTER subscriber is registered, and catch promise errors
    recoverUnfinishedTransactions()
      .then(({ recovered }) => {
        if (isMounted && isMountedRef.current && recovered > 0) {
          fetchCourseDetails();
        }
      })
      .catch((err) => {
        console.warn('[CourseDetailsScreen] recoverUnfinishedTransactions error:', err);
      });

    return () => {
      isMounted = false;
      unsubscribe();
    };
  }, [courseId]);

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
    if (buying || iapLoading || iapFetching) return;
    if (!iapProduct) {
      Alert.alert(
        'Product Unavailable',
        'This course is currently not configured or available for purchase on the App Store.'
      );
      return;
    }
    setIapLoading(true);
    try {
      await requestApplePurchase(appleProductId);
      // Asynchronous transaction fulfillment and UI alerts are handled by subscribeIap above.
    } catch (err: any) {
      if (isMountedRef.current) {
        setIapLoading(false);
      }
      if (isUserCancellation(err)) {
        return;
      }
      console.error('Apple purchase initiation error:', err);
      const errMsg = err.response?.data?.error || err.message || 'Could not initiate Apple In-App Purchase.';
      if (isMountedRef.current) {
        Alert.alert('Purchase Error', errMsg);
      }
    }
  };

  // iOS Restore Purchases Flow
  const handleRestorePurchases = async () => {
    if (iapLoading) return;
    setIapLoading(true);
    try {
      const result = await restorePurchases();
      if (result.newlyRestoredCount > 0 || result.previouslyVerifiedFinishedCount > 0) {
        await fetchCourseDetails();
      }
      if (isMountedRef.current) {
        showRestoreFeedback(result, () => {
          if (isMountedRef.current) {
            fetchCourseDetails();
          }
        });
      }
    } catch (err: any) {
      console.error('Failed to restore purchases:', err);
      if (isMountedRef.current) {
        Alert.alert('Restore Error', err.message || 'Could not restore purchases from Apple Store. Please try again.');
      }
    } finally {
      if (isMountedRef.current) {
        setIapLoading(false);
      }
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

      const { payment_url, purchase_id } = res.data;

      if (payment_url) {
        // 2. Redirect directly to Razorpay's official checkout URL
        if (isMountedRef.current) {
          setPendingPurchaseId(purchase_id);
          setAwaitingPayment(true);
        }
        await Linking.openURL(payment_url);
      } else {
        if (isMountedRef.current) {
          Alert.alert(
            'Payment Notice',
            'Razorpay checkout link is currently unavailable. Please check your network or try again in a moment.'
          );
        }
      }
    } catch (err: any) {
      console.error('Failed to create Razorpay order:', err);
      const errMsg = err.response?.data?.error || 'Could not initiate Razorpay checkout. Please try again.';
      if (isMountedRef.current) {
        Alert.alert('Checkout Error', errMsg);
      }
    } finally {
      if (isMountedRef.current) {
        setBuying(false);
      }
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
            {!owned && (
              Platform.OS === 'ios' ? (
                iapProduct?.localizedPrice ? (
                  <Text style={styles.price}>{iapProduct.localizedPrice}</Text>
                ) : null
              ) : (
                <Text style={styles.price}>₹{course.price}</Text>
              )
            )}
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
              style={[
                styles.primaryButton,
                (iapLoading || buying || iapFetching || !iapProduct) && styles.disabledButton,
              ]}
              onPress={handleApplePurchase}
              disabled={iapLoading || buying || iapFetching || !iapProduct}
              accessibilityRole="button"
              accessibilityLabel={
                iapFetching
                  ? "Checking App Store availability"
                  : iapProduct
                  ? `Unlock course for ${iapProduct.localizedPrice}`
                  : "Course unavailable for purchase on App Store"
              }
            >
              {iapLoading ? (
                <ActivityIndicator color={colors.textInverse} size="small" />
              ) : iapFetching ? (
                <>
                  <ActivityIndicator color={colors.textInverse} size="small" />
                  <Text style={styles.primaryButtonText}>Checking App Store...</Text>
                </>
              ) : iapProduct ? (
                <>
                  <Icon name="credit-card" size={16} color={colors.textInverse} />
                  <Text style={styles.primaryButtonText}>
                    Unlock Course ({iapProduct.localizedPrice})
                  </Text>
                </>
              ) : (
                <>
                  <Icon name="alert-circle" size={16} color={colors.textInverse} />
                  <Text style={styles.primaryButtonText}>Unavailable on App Store</Text>
                </>
              )}
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.restoreButton}
              onPress={handleRestorePurchases}
              disabled={iapLoading || iapFetching}
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
