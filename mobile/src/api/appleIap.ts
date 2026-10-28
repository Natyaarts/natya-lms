import { Platform, Alert } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { Purchase, Product, PurchaseError } from 'react-native-iap';
import client, { onSessionExpired } from './client';

/**
 * Shared Apple In-App Purchase Service for Natya LMS.
 *
 * Implements authoritative StoreKit 2 transaction verification, accurate restore feedback,
 * explicit verification/finishing states, session-isolated caching, concurrency guards,
 * and single-source listener lifecycles.
 */

let RNIapModule: typeof import('react-native-iap') | null = null;

export function isIapSupported(): boolean {
  return Platform.OS === 'ios';
}

export function getRNIap() {
  if (!isIapSupported()) return null;
  if (!RNIapModule) {
    try {
      RNIapModule = require('react-native-iap');
    } catch (e) {
      console.warn('[AppleIAP] react-native-iap unavailable in current runtime:', e);
      return null;
    }
  }
  return RNIapModule;
}

// Global connection state to avoid redundant calls or closing a shared connection
let connectionPromise: Promise<boolean> | null = null;
let isConnected = false;

export async function initIapConnection(): Promise<boolean> {
  const RNIap = getRNIap();
  if (!RNIap) return false;
  if (isConnected) return true;
  if (connectionPromise) return connectionPromise;

  connectionPromise = (async () => {
    try {
      const ok = await RNIap.initConnection();
      isConnected = ok ?? true;
      return isConnected;
    } catch (err) {
      console.warn('[AppleIAP] initConnection error:', err);
      isConnected = false;
      return false;
    } finally {
      connectionPromise = null;
    }
  })();

  return connectionPromise;
}

/**
 * Extracts a cryptographically verifiable JWS token from a StoreKit 2 transaction.
 *
 * A raw transactionId (e.g. integer or UUID string) is NOT a cryptographically
 * signed payload and will fail backend JWS validation. This helper explicitly
 * requires a valid 3-part dot-separated JWS string.
 */
export function extractSignedTransaction(purchase: any): string | null {
  if (!purchase) return null;

  const candidates = [
    purchase.signedTransactionInfo,
    purchase.verificationResultIOS,
    purchase.jwsRepresentation,
    purchase.verificationResult,
    purchase.transactionReceipt,
  ];

  for (const candidate of candidates) {
    if (typeof candidate === 'string') {
      const trimmed = candidate.trim();
      // JWS tokens have exactly 3 parts separated by periods (header.payload.signature)
      if (trimmed.split('.').length === 3) {
        return trimmed;
      }
    }
  }

  // Explicitly do NOT fall back to purchase.transactionId!
  return null;
}

export function getTransactionKey(purchase: any): string {
  return (
    purchase?.transactionId ||
    purchase?.originalTransactionIdentifierIOS ||
    extractSignedTransaction(purchase) ||
    'unknown'
  );
}

// In-flight transaction keys currently being processed (prevents concurrent duplicate execution)
const inFlightTransactions = new Set<string>();

// Current session token associated with cached transaction states
let activeSessionToken: string | null = null;

// Cached backend verification records for the CURRENT signed-in user session
interface BackendVerifiedCacheEntry {
  isNewEnrollment: boolean;
  data: any;
}
const sessionBackendVerifiedTransactions = new Map<string, BackendVerifiedCacheEntry>();

// Transactions that have been successfully acknowledged/finished with StoreKit in this session
const sessionFinishedStoreKitTransactions = new Set<string>();

/**
 * Synchronizes the in-memory cache with the active user session.
 * If the user logs out or switches accounts, all session caches are wiped immediately.
 */
export async function syncSessionToken(): Promise<string | null> {
  try {
    const token = await AsyncStorage.getItem('access_token');
    if (token !== activeSessionToken) {
      activeSessionToken = token;
      sessionBackendVerifiedTransactions.clear();
      sessionFinishedStoreKitTransactions.clear();
      inFlightTransactions.clear();
    }
    return token;
  } catch {
    return activeSessionToken;
  }
}

/**
 * Explicitly clears session-level IAP caches on user logout or session expiration.
 */
export function resetIapSessionState(): void {
  activeSessionToken = null;
  sessionBackendVerifiedTransactions.clear();
  sessionFinishedStoreKitTransactions.clear();
  inFlightTransactions.clear();
}

// Automatically reset session state when auth session expires
onSessionExpired(() => {
  resetIapSessionState();
});

export interface VerifyResult {
  success: boolean;
  backendVerified?: boolean;
  finished?: boolean;
  isNewEnrollment?: boolean; // created = True on backend (status: 'SUCCESS')
  previouslyVerifiedFinished?: boolean; // existed in DB (status: 'ALREADY_PROCESSED') and now finished in StoreKit
  alreadyFinished?: boolean; // was already finished in StoreKit earlier in this session
  alreadyInFlight?: boolean;
  data?: any;
  error?: string;
}

/**
 * Verifies a transaction with the backend and finishes it in StoreKit.
 *
 * Explicitly separates:
 * 1. Backend verification and entitlement granting (authoritative server check).
 * 2. StoreKit finishing (client-side acknowledgment to Apple).
 *
 * If finishing fails, the transaction is retained for retry without duplicate enrollment.
 * If the user switched accounts, the session cache is automatically invalidated.
 */
export async function verifyAndFinishAppleTransaction(
  purchase: any,
  fallbackProductId?: string
): Promise<VerifyResult> {
  const token = await syncSessionToken();
  if (!token) {
    return {
      success: false,
      backendVerified: false,
      finished: false,
      error: 'Authentication required. Please sign in to verify course purchases.',
    };
  }

  const txKey = getTransactionKey(purchase);

  if (inFlightTransactions.has(txKey)) {
    return {
      success: false,
      alreadyInFlight: true,
      error: 'Transaction is currently being processed.',
    };
  }

  if (sessionFinishedStoreKitTransactions.has(txKey)) {
    return {
      success: true,
      backendVerified: true,
      finished: true,
      alreadyFinished: true,
    };
  }

  inFlightTransactions.add(txKey);

  try {
    let backendData: any = null;
    let isNewEnrollment = false;

    // Step 1: Authoritative Backend Verification
    if (sessionBackendVerifiedTransactions.has(txKey)) {
      const cached = sessionBackendVerifiedTransactions.get(txKey)!;
      backendData = cached.data;
      // On retry of StoreKit finish, backend enrollment was already completed earlier
      isNewEnrollment = false;
    } else {
      const signedTx = extractSignedTransaction(purchase);
      if (!signedTx) {
        return {
          success: false,
          backendVerified: false,
          finished: false,
          error: 'Transaction is missing cryptographic JWS signature data from Apple StoreKit.',
        };
      }

      const productId = purchase.productId || fallbackProductId;
      const res = await client.post('orders/apple/verify-purchase/', {
        signed_transaction_info: signedTx,
        product_id: productId,
      });

      if (res.status === 200) {
        backendData = res.data;
        isNewEnrollment = res.data?.status === 'SUCCESS';
        sessionBackendVerifiedTransactions.set(txKey, {
          isNewEnrollment,
          data: backendData,
        });
      } else {
        return {
          success: false,
          backendVerified: false,
          finished: false,
          error: res.data?.error || `Server returned status ${res.status}`,
        };
      }
    }

    // Step 2: StoreKit Transaction Finishing
    const connected = await initIapConnection();
    const RNIap = getRNIap();

    if (RNIap && connected) {
      try {
        await RNIap.finishTransaction({ purchase, isConsumable: false });
        sessionFinishedStoreKitTransactions.add(txKey);
      } catch (finishErr: any) {
        console.warn(`[AppleIAP] finishTransaction failed for ${txKey}:`, finishErr);
        // Backend verification succeeded, but StoreKit finish failed.
        // Do NOT add to sessionFinishedStoreKitTransactions, allowing safe retry.
        return {
          success: false,
          backendVerified: true,
          finished: false,
          isNewEnrollment,
          data: backendData,
          error: `Course enrollment confirmed on server, but StoreKit finishTransaction failed (${finishErr?.message || 'StoreKit error'}). Transaction retained for retry.`,
        };
      }
    } else {
      return {
        success: false,
        backendVerified: true,
        finished: false,
        isNewEnrollment,
        data: backendData,
        error: 'Course enrollment confirmed on server, but could not connect to StoreKit to finish transaction.',
      };
    }

    return {
      success: true,
      backendVerified: true,
      finished: true,
      isNewEnrollment,
      previouslyVerifiedFinished: !isNewEnrollment,
      data: backendData,
    };
  } catch (err: any) {
    const errorMsg =
      err.response?.data?.error ||
      err.message ||
      'Network or server verification error occurred.';
    return {
      success: false,
      backendVerified: sessionBackendVerifiedTransactions.has(txKey),
      finished: false,
      error: errorMsg,
    };
  } finally {
    inFlightTransactions.delete(txKey);
  }
}

export type PurchaseSuccessCallback = (purchase: any, resData: any) => void;
export type PurchaseErrorCallback = (error: any) => void;

export interface IapSubscriber {
  onSuccess?: PurchaseSuccessCallback;
  onError?: PurchaseErrorCallback;
}

const subscribers = new Set<IapSubscriber>();
let purchaseUpdateSubscription: any = null;
let purchaseErrorSubscription: any = null;

function ensureNativeListeners() {
  const RNIap = getRNIap();
  if (!RNIap) return;

  if (!purchaseUpdateSubscription) {
    purchaseUpdateSubscription = RNIap.purchaseUpdatedListener(
      async (purchase: Purchase) => {
        try {
          const txKey = getTransactionKey(purchase);
          if (inFlightTransactions.has(txKey) || sessionFinishedStoreKitTransactions.has(txKey)) {
            return;
          }

          const result = await verifyAndFinishAppleTransaction(purchase);
          if (result.success && !result.alreadyFinished) {
            subscribers.forEach((sub) => {
              try {
                sub.onSuccess?.(purchase, result.data);
              } catch (cbErr) {
                console.error('[AppleIAP] subscriber onSuccess callback error:', cbErr);
              }
            });
          } else if (!result.success && !result.alreadyInFlight) {
            subscribers.forEach((sub) => {
              try {
                sub.onError?.(new Error(result.error || 'Failed to verify Apple purchase.'));
              } catch (cbErr) {
                console.error('[AppleIAP] subscriber onError callback error:', cbErr);
              }
            });
          }
        } catch (listenerErr) {
          console.error('[AppleIAP] purchaseUpdatedListener caught error:', listenerErr);
          subscribers.forEach((sub) => {
            try {
              sub.onError?.(listenerErr);
            } catch (cbErr) {
              console.error('[AppleIAP] subscriber onError callback error:', cbErr);
            }
          });
        }
      },
      (nativeErr: unknown) => {
        console.warn('[AppleIAP] purchaseUpdatedListener native error:', nativeErr);
        subscribers.forEach((sub) => {
          try {
            sub.onError?.(nativeErr);
          } catch (cbErr) {
            console.error('[AppleIAP] subscriber onError callback error:', cbErr);
          }
        });
      }
    );
  }

  if (!purchaseErrorSubscription) {
    purchaseErrorSubscription = RNIap.purchaseErrorListener((error: PurchaseError) => {
      try {
        subscribers.forEach((sub) => {
          try {
            sub.onError?.(error);
          } catch (cbErr) {
            console.error('[AppleIAP] subscriber onError callback error:', cbErr);
          }
        });
      } catch (err) {
        console.error('[AppleIAP] purchaseErrorListener caught error:', err);
      }
    });
  }
}

/**
 * Subscribes a screen or component to purchase updates.
 * Returns an unsubscribe callback to be called during cleanup.
 */
export function subscribeIap(subscriber: IapSubscriber): () => void {
  if (!isIapSupported()) return () => {};
  ensureNativeListeners();
  subscribers.add(subscriber);

  return () => {
    subscribers.delete(subscriber);
  };
}

export function isUserCancellation(err: any): boolean {
  if (!err) return false;
  return (
    err.code === 'E_USER_CANCELLED' ||
    err.code === '2' ||
    String(err.message || '').toLowerCase().includes('cancel')
  );
}

/**
 * Requests an Apple In-App Purchase for a given SKU.
 * Checks connection before requesting. Completion is delivered via purchaseUpdatedListener.
 */
export async function requestApplePurchase(sku: string): Promise<void> {
  const token = await syncSessionToken();
  if (!token) {
    throw new Error('Please sign in to your Natya account before purchasing courses.');
  }

  const RNIap = getRNIap();
  if (!RNIap) {
    throw new Error('Apple In-App Purchases require an iOS device with active App Store.');
  }

  const connected = await initIapConnection();
  if (!connected) {
    throw new Error('Could not establish connection to the Apple App Store. Please verify your internet connection and App Store settings.');
  }

  ensureNativeListeners();

  await RNIap.requestPurchase({
    sku,
    andDangerouslyFinishTransactionAutomaticallyIOS: false,
  });
}

/**
 * Fetches product metadata for a given Apple Product ID.
 */
export async function fetchAppleProduct(sku: string): Promise<Product | null> {
  const RNIap = getRNIap();
  if (!RNIap) return null;

  const connected = await initIapConnection();
  if (!connected) {
    console.warn(`[AppleIAP] Cannot fetch product ${sku}: StoreKit connection failed.`);
    return null;
  }

  try {
    const products = await RNIap.getProducts({ skus: [sku] });
    return products && products.length > 0 ? products[0] : null;
  } catch (err) {
    console.warn(`[AppleIAP] Failed to fetch product info for ${sku}:`, err);
    return null;
  }
}

/**
 * Recovers and finishes any unfinished transactions delivered by StoreKit.
 */
export async function recoverUnfinishedTransactions(): Promise<{
  recovered: number;
  failed: number;
}> {
  const token = await syncSessionToken();
  if (!token) {
    return { recovered: 0, failed: 0 };
  }

  const RNIap = getRNIap();
  if (!RNIap) return { recovered: 0, failed: 0 };

  const connected = await initIapConnection();
  if (!connected) {
    console.warn('[AppleIAP] Cannot recover unfinished transactions: StoreKit connection failed.');
    return { recovered: 0, failed: 0 };
  }

  try {
    const purchases = await RNIap.getAvailablePurchases({
      automaticallyFinishRestoredTransactions: false,
    });
    if (!purchases || purchases.length === 0) {
      return { recovered: 0, failed: 0 };
    }

    let recovered = 0;
    let failed = 0;

    for (const purchase of purchases) {
      const txKey = getTransactionKey(purchase);
      if (sessionFinishedStoreKitTransactions.has(txKey)) {
        continue;
      }

      const result = await verifyAndFinishAppleTransaction(purchase);
      if (result.success && !result.alreadyFinished) {
        recovered++;
      } else if (!result.success && !result.alreadyInFlight) {
        failed++;
      }
    }

    return { recovered, failed };
  } catch (err) {
    console.warn('[AppleIAP] Error recovering unfinished transactions:', err);
    return { recovered: 0, failed: 0 };
  }
}

export interface RestoreResult {
  totalFound: number;
  newlyRestoredCount: number; // Newly verified and enrolled on backend (status: SUCCESS)
  previouslyVerifiedFinishedCount: number; // Existed in backend DB and was now finished in StoreKit
  alreadyFinishedCount: number; // Was already finished earlier in session
  inFlightCount: number; // Currently processing
  failedCount: number; // Verification or finish failure
  errors: string[];
}

/**
 * Restores previous purchases for the current Apple ID and verifies each with the backend.
 *
 * Accurately tracks newly restored, previously verified finished, already active, in-flight, and failed transactions.
 */
export async function restorePurchases(): Promise<RestoreResult> {
  const token = await syncSessionToken();
  if (!token) {
    throw new Error('Please sign in to your Natya account before restoring purchases.');
  }

  const RNIap = getRNIap();
  if (!RNIap) {
    throw new Error('Restore Purchases requires an iOS device with active App Store.');
  }

  const connected = await initIapConnection();
  if (!connected) {
    throw new Error('Could not connect to the Apple App Store. Please verify your internet connection and try again.');
  }

  const purchases = await RNIap.getAvailablePurchases({
    automaticallyFinishRestoredTransactions: false,
  });

  if (!purchases || purchases.length === 0) {
    return {
      totalFound: 0,
      newlyRestoredCount: 0,
      previouslyVerifiedFinishedCount: 0,
      alreadyFinishedCount: 0,
      failedCount: 0,
      inFlightCount: 0,
      errors: [],
    };
  }

  let newlyRestoredCount = 0;
  let previouslyVerifiedFinishedCount = 0;
  let alreadyFinishedCount = 0;
  let failedCount = 0;
  let inFlightCount = 0;
  const errors: string[] = [];

  for (const p of purchases) {
    const txKey = getTransactionKey(p);
    if (inFlightTransactions.has(txKey)) {
      inFlightCount++;
      continue;
    }

    const result = await verifyAndFinishAppleTransaction(p);

    if (result.success) {
      if (result.alreadyFinished) {
        alreadyFinishedCount++;
      } else if (result.isNewEnrollment) {
        newlyRestoredCount++;
      } else {
        previouslyVerifiedFinishedCount++;
      }
    } else {
      if (result.alreadyInFlight) {
        inFlightCount++;
      } else {
        failedCount++;
        if (result.error) {
          errors.push(result.error);
        }
      }
    }
  }

  return {
    totalFound: purchases.length,
    newlyRestoredCount,
    previouslyVerifiedFinishedCount,
    alreadyFinishedCount,
    failedCount,
    inFlightCount,
    errors,
  };
}

/**
 * Presents accurate, contextual feedback to the user based on restore results.
 * Distinguishes:
 * 1. No purchases found on Apple ID.
 * 2. Newly verified and restored course purchases.
 * 3. Previously verified course purchases successfully finished/confirmed.
 * 4. Purchases already active and up to date.
 * 5. In-flight processing status.
 * 6. Verification/finishing failures.
 */
export function showRestoreFeedback(
  result: RestoreResult,
  onRestored?: () => void
) {
  if (result.totalFound === 0) {
    Alert.alert(
      'Restore Purchases',
      'No previous purchases were found for this Apple ID.'
    );
  } else if (result.newlyRestoredCount > 0) {
    if (result.failedCount === 0) {
      Alert.alert(
        'Purchases Restored 🎉',
        `Successfully restored ${result.newlyRestoredCount} course purchase(s).`,
        onRestored ? [{ text: 'OK', onPress: onRestored }] : [{ text: 'OK' }]
      );
    } else {
      Alert.alert(
        'Partial Restore',
        `Restored ${result.newlyRestoredCount} course(s), but ${result.failedCount} purchase(s) could not be verified by the server.`,
        onRestored ? [{ text: 'OK', onPress: onRestored }] : [{ text: 'OK' }]
      );
    }
  } else if (result.previouslyVerifiedFinishedCount > 0) {
    Alert.alert(
      'Purchases Confirmed',
      `Confirmed and restored ${result.previouslyVerifiedFinishedCount} existing course purchase(s).`,
      onRestored ? [{ text: 'OK', onPress: onRestored }] : [{ text: 'OK' }]
    );
  } else if (result.alreadyFinishedCount > 0 && result.failedCount === 0) {
    Alert.alert(
      'Purchases Active',
      'Your course purchases for this Apple ID are already active and up to date.'
    );
  } else if (result.inFlightCount > 0 && result.failedCount === 0) {
    Alert.alert(
      'Processing',
      'Your purchase is currently being processed by the server. Please check back in a moment.'
    );
  } else {
    Alert.alert(
      'Restore Failed',
      `Found ${result.totalFound} purchase(s) on your Apple ID, but server verification failed. Please check your internet connection and try again.`
    );
  }
}
