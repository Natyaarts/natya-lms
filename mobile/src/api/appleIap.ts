import { Platform, Alert } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { Purchase, Product, PurchaseError } from 'react-native-iap';
import client, { onSessionExpired } from './client';

export type { Product };

/**
 * Shared Apple In-App Purchase Service for Natya LMS.
 *
 * Implements authoritative StoreKit 2 transaction verification, accurate restore feedback,
 * explicit verification/finishing states, session-isolated caching, concurrency guards,
 * and single-source listener lifecycles.
 */

let RNIapModule: typeof import('react-native-iap') | null = null;
let isStoreKit2Configured = false;

export function isIapSupported(): boolean {
  return Platform.OS === 'ios';
}

/**
 * Explicitly activates StoreKit 2 mode in react-native-iap before any
 * NativeEventEmitter listener registration or StoreKit connection call.
 *
 * react-native-iap v12.16.4 defaults to STOREKIT1_MODE (RNIapIos), which only
 * emits legacy PKCS#7 ASN.1 transactionReceipt blobs and omits StoreKit 2 JWS
 * tokens (verificationResultIOS). Activating STOREKIT2_MODE binds RNIapIosSk2
 * so purchases and restored entitlements emit Apple-signed StoreKit 2 JWS data.
 */
export function ensureStoreKit2Configured(
  iapModule: typeof import('react-native-iap') | null = RNIapModule
): boolean {
  if (!isIapSupported() || !iapModule) return false;
  if (!isStoreKit2Configured) {
    try {
      if (typeof iapModule.setup === 'function') {
        iapModule.setup({ storekitMode: 'STOREKIT2_MODE' });
      }
      isStoreKit2Configured = true;
    } catch (err) {
      console.warn('[AppleIAP] Failed to configure STOREKIT2_MODE:', err);
      return false;
    }
  }
  return true;
}

export function getRNIap() {
  if (!isIapSupported()) return null;
  if (!RNIapModule) {
    try {
      RNIapModule = require('react-native-iap');
      ensureStoreKit2Configured(RNIapModule);
    } catch (e) {
      console.warn('[AppleIAP] react-native-iap unavailable in current runtime:', e);
      return null;
    }
  } else {
    ensureStoreKit2Configured(RNIapModule);
  }
  return RNIapModule;
}

// Global connection state to avoid redundant calls or closing a shared connection
let connectionPromise: Promise<boolean> | null = null;
let isConnected = false;

export async function initIapConnection(): Promise<boolean> {
  const RNIap = getRNIap();
  if (!RNIap) return false;
  ensureStoreKit2Configured(RNIap);
  if (isConnected) {
    ensureNativeListeners();
    return true;
  }
  if (connectionPromise) return connectionPromise;

  connectionPromise = (async () => {
    try {
      ensureStoreKit2Configured(RNIap);
      const ok = await RNIap.initConnection();
      isConnected = ok ?? true;
      if (isConnected) {
        ensureNativeListeners();
      }
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

/**
 * Decodes the unverified JSON payload segment of a 3-part JWS string solely for
 * client-side StoreKit inventory filtering (e.g. skipping historically refunded
 * entries in StoreKit 2 Transaction.all). Never used to grant entitlements;
 * authoritative cryptographic x5c/ES256 verification always occurs on the backend.
 */
function decodeJwsPayloadUnverified(jws: string): Record<string, any> | null {
  try {
    const parts = jws.split('.');
    if (parts.length !== 3 || !parts[1]) return null;
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
    const bytes: number[] = [];
    for (let i = 0; i < padded.length; i += 4) {
      const c1 = chars.indexOf(padded[i]);
      const c2 = chars.indexOf(padded[i + 1]);
      const c3 = padded[i + 2] === '=' ? -1 : chars.indexOf(padded[i + 2]);
      const c4 = padded[i + 3] === '=' ? -1 : chars.indexOf(padded[i + 3]);
      if (c1 < 0 || c2 < 0) return null;
      const triplet = (c1 << 18) | (c2 << 12) | ((c3 < 0 ? 0 : c3) << 6) | (c4 < 0 ? 0 : c4);
      bytes.push((triplet >> 16) & 0xff);
      if (c3 >= 0) bytes.push((triplet >> 8) & 0xff);
      if (c4 >= 0) bytes.push(triplet & 0xff);
    }
    let jsonStr = '';
    if (typeof TextDecoder !== 'undefined') {
      jsonStr = new TextDecoder('utf-8').decode(new Uint8Array(bytes));
    } else {
      jsonStr = decodeURIComponent(
        bytes.map((b) => '%' + ('00' + b.toString(16)).slice(-2)).join('')
      );
    }
    const parsed = JSON.parse(jsonStr);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * Checks whether a StoreKit 2 transaction from Transaction.all carries an explicit
 * Apple revocationDate (refunded/revoked historical transaction) so restore/recovery
 * scans skip inactive historical entries without failing active course restores.
 */
export function isRevokedStoreKitTransaction(purchase: any): boolean {
  if (!purchase) return false;
  if (purchase.revocationDate || purchase.revocationDateIOS) {
    return true;
  }
  const signedTx = extractSignedTransaction(purchase);
  if (!signedTx) return false;
  const payload = decodeJwsPayloadUnverified(signedTx);
  return Boolean(payload && payload.revocationDate);
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

// Device/process-level StoreKit 2 transaction IDs known to be held in RNIapIosSk2's
// native `transactions` map (populated only via buyProduct and Transaction.updates).
// RNIapIosSk2.getAvailableItems() does NOT populate native `transactions`, so calling
// RNIap.finishTransaction() on historical restored entitlements rejects with
// E_DEVELOPER_ERROR ("Invalid transaction Id").
const nativePendingTransactionIds = new Set<string>();

export function markNativePendingTransaction(purchase: any): void {
  const txId = purchase?.transactionId ? String(purchase.transactionId) : '';
  if (txId) {
    nativePendingTransactionIds.add(txId);
  }
}

export function isUnknownNativeTransactionError(err: any): boolean {
  if (!err) return false;
  const code = String(err.code || '');
  const msg = String(err.message || '');
  return code === 'E_DEVELOPER_ERROR' && /invalid transaction id/i.test(msg);
}

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

export interface VerifyTransactionOptions {
  isRestored?: boolean;
}

/**
 * Verifies a transaction with the backend and finishes it in StoreKit when required.
 *
 * Explicitly separates:
 * 1. Backend verification and entitlement granting (authoritative server check).
 * 2. StoreKit finishing (client-side acknowledgment to Apple for live/unfinished transactions).
 *
 * For historical restored transactions returned by StoreKit 2 getAvailablePurchases(),
 * RNIapIosSk2 does not register the transaction in its native `transactions` map unless
 * it was also delivered as an unfinished transaction via Transaction.updates / buyProduct.
 * Calling finishTransaction on an unknown native transaction ID is avoided while still
 * finishing any genuinely pending native transaction.
 */
export async function verifyAndFinishAppleTransaction(
  purchase: any,
  fallbackProductId?: string,
  options?: VerifyTransactionOptions
): Promise<VerifyResult> {
  const txKey = getTransactionKey(purchase);

  if (inFlightTransactions.has(txKey)) {
    return {
      success: false,
      alreadyInFlight: true,
      error: 'Transaction is currently being processed.',
    };
  }

  inFlightTransactions.add(txKey);

  try {
    const token = await syncSessionToken();
    if (!token) {
      return {
        success: false,
        backendVerified: false,
        finished: false,
        error: 'Authentication required. Please sign in to verify course purchases.',
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
    const txId = purchase?.transactionId ? String(purchase.transactionId) : '';
    const isRestored = Boolean(options?.isRestored);
    const shouldFinishInNativeStoreKit =
      !isRestored || (Boolean(txId) && nativePendingTransactionIds.has(txId));

    if (!shouldFinishInNativeStoreKit) {
      // Historical StoreKit 2 entitlement recovered via getAvailablePurchases() that
      // is not queued in RNIapIosSk2's native unfinished `transactions` dictionary.
      sessionFinishedStoreKitTransactions.add(txKey);
      return {
        success: true,
        backendVerified: true,
        finished: true,
        isNewEnrollment,
        previouslyVerifiedFinished: !isNewEnrollment,
        data: backendData,
      };
    }

    const connected = await initIapConnection();
    const RNIap = getRNIap();

    if (RNIap && connected) {
      try {
        await RNIap.finishTransaction({ purchase, isConsumable: false });
        if (txId) {
          nativePendingTransactionIds.delete(txId);
        }
        sessionFinishedStoreKitTransactions.add(txKey);
      } catch (finishErr: any) {
        if (isRestored && isUnknownNativeTransactionError(finishErr)) {
          // Native RNIapIosSk2 already finished/cleared this transaction ID.
          if (txId) {
            nativePendingTransactionIds.delete(txId);
          }
          sessionFinishedStoreKitTransactions.add(txKey);
        } else {
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

async function handleNativePurchaseEvent(purchase: Purchase, fallbackSku?: string): Promise<void> {
  if (!purchase) return;
  markNativePendingTransaction(purchase);

  const txKey = getTransactionKey(purchase);
  if (inFlightTransactions.has(txKey) || sessionFinishedStoreKitTransactions.has(txKey)) {
    return;
  }

  const result = await verifyAndFinishAppleTransaction(purchase, fallbackSku);
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
}

function ensureNativeListeners() {
  const RNIap = getRNIap();
  if (!RNIap) return;
  ensureStoreKit2Configured(RNIap);

  if (!purchaseUpdateSubscription) {
    purchaseUpdateSubscription = RNIap.purchaseUpdatedListener(
      async (purchase: Purchase) => {
        try {
          await handleNativePurchaseEvent(purchase);
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
 * Checks connection before requesting. Completion is delivered via purchaseUpdatedListener,
 * with fallback handling if StoreKit 2 resolves directly without an event callback.
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

  const purchaseResult = await RNIap.requestPurchase({
    sku,
    andDangerouslyFinishTransactionAutomaticallyIOS: false,
  });

  if (purchaseResult) {
    const items = Array.isArray(purchaseResult) ? purchaseResult : [purchaseResult];
    for (const item of items) {
      if (item) {
        await handleNativePurchaseEvent(item, sku);
      }
    }
  }
}

/**
 * Resolves the Apple App Store Product ID (SKU) for a course, preferring
 * an explicit `apple_product_id` from the API and falling back to the
 * canonical bundle convention.
 */
export function getCourseAppleSku(
  course: { id?: number | string; apple_product_id?: string | null } | null | undefined,
  fallbackCourseId?: number | string
): string {
  const explicit = course?.apple_product_id?.trim();
  if (explicit) return explicit;
  const id = course?.id ?? fallbackCourseId;
  return id !== undefined && id !== null ? `com.natyaarts.academy.course.${id}` : '';
}

/**
 * Fetches product metadata for a given Apple Product ID.
 */
export async function fetchAppleProduct(sku: string): Promise<Product | null> {
  if (!sku) return null;
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
 * Batch-fetches StoreKit product metadata for multiple Apple Product IDs.
 * Returns a map keyed by `productId` for O(1) localized price lookup in course cards.
 */
export async function fetchAppleProducts(skus: string[]): Promise<Record<string, Product>> {
  const uniqueSkus = Array.from(new Set(skus.map((s) => (s || '').trim()).filter(Boolean)));
  if (uniqueSkus.length === 0) return {};

  const RNIap = getRNIap();
  if (!RNIap) return {};

  const connected = await initIapConnection();
  if (!connected) {
    console.warn('[AppleIAP] Cannot batch-fetch products: StoreKit connection failed.');
    return {};
  }

  try {
    const products: Product[] = await RNIap.getProducts({ skus: uniqueSkus });
    const map: Record<string, Product> = {};
    for (const product of products || []) {
      if (product?.productId) {
        map[product.productId] = product;
      }
    }
    return map;
  } catch (err) {
    console.warn('[AppleIAP] Failed to batch-fetch product info:', err);
    return {};
  }
}

/**
 * Queries StoreKit 2 purchase inventory without requiring products to be pre-loaded
 * into RNIapIosSk2's in-memory `productStore`.
 *
 * In react-native-iap v12.16.4 (`RNIapIosSk2.swift`), `getAvailablePurchases()` defaults
 * `onlyIncludeActiveItems` to `true`, which filters `.nonConsumable` entitlements against
 * `productStore.getProduct(productID:) != nil` and returns `[]` whenever products have not
 * been fetched yet. Passing `onlyIncludeActiveItems: false` reads verified StoreKit 2
 * transactions (with full JWS signatures) directly, and client-side filtering skips any
 * historically refunded (`revocationDate`) entries while backend verification remains authoritative.
 */
async function fetchRecoverableStoreKitPurchases(
  RNIap: typeof import('react-native-iap'),
  options?: { allowAppStoreSync?: boolean }
): Promise<Purchase[]> {
  const filterAndDedupe = (rawList: Purchase[] | null | undefined): Purchase[] => {
    if (!Array.isArray(rawList) || rawList.length === 0) return [];
    const seen = new Set<string>();
    const active: Purchase[] = [];
    for (const item of rawList) {
      if (!item || isRevokedStoreKitTransaction(item)) {
        continue;
      }
      const key = getTransactionKey(item);
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      active.push(item);
    }
    return active;
  };

  let purchases = filterAndDedupe(
    await RNIap.getAvailablePurchases({
      automaticallyFinishRestoredTransactions: false,
      onlyIncludeActiveItems: false,
    })
  );

  if (
    purchases.length === 0 &&
    options?.allowAppStoreSync &&
    typeof RNIap.IapIosSk2?.sync === 'function'
  ) {
    try {
      await RNIap.IapIosSk2.sync();
      purchases = filterAndDedupe(
        await RNIap.getAvailablePurchases({
          automaticallyFinishRestoredTransactions: false,
          onlyIncludeActiveItems: false,
        })
      );
    } catch (syncErr: any) {
      if (!isUserCancellation(syncErr)) {
        console.warn('[AppleIAP] AppStore.sync() failed during restore:', syncErr);
      }
    }
  }

  return purchases;
}

/**
 * Recovers previously purchased or unfinished StoreKit 2 non-consumable transactions,
 * even when course products have not been loaded into StoreKit's productStore yet.
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
    const purchases = await fetchRecoverableStoreKitPurchases(RNIap, {
      allowAppStoreSync: false,
    });
    if (purchases.length === 0) {
      return { recovered: 0, failed: 0 };
    }

    let recovered = 0;
    let failed = 0;

    for (const purchase of purchases) {
      const txKey = getTransactionKey(purchase);
      if (sessionFinishedStoreKitTransactions.has(txKey)) {
        continue;
      }

      const result = await verifyAndFinishAppleTransaction(purchase, undefined, {
        isRestored: true,
      });
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
  previouslyVerifiedFinishedCount: number; // Existed in backend DB and confirmed
  alreadyFinishedCount: number; // Was already verified/finished earlier in session
  inFlightCount: number; // Currently processing
  failedCount: number; // Verification or finish failure
  errors: string[];
}

/**
 * Restores previous purchases for the current Apple ID and verifies each with the backend.
 *
 * Works even when course products have not been loaded first, and avoids calling
 * finishTransaction on historical transactions unknown to RNIapIosSk2's native pending map.
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

  const purchases = await fetchRecoverableStoreKitPurchases(RNIap, {
    allowAppStoreSync: true,
  });

  if (purchases.length === 0) {
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

    const result = await verifyAndFinishAppleTransaction(p, undefined, {
      isRestored: true,
    });

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
