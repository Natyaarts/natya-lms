/**
 * Focused Unit Tests for Apple IAP Client Logic:
 * 1. JWS cryptographic extraction (rejection of raw transaction IDs)
 * 2. Multi-account session isolation & unauthenticated rejection
 * 3. Separation of backend verification vs StoreKit finish retry
 * 4. Restore accounting 5-category tallying and user feedback classification
 */

const assert = require('assert');

// -------------------------------------------------------------
// Test 1: Strict JWS extraction
// -------------------------------------------------------------
function extractSignedTransaction(purchase) {
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
      if (trimmed.split('.').length === 3) {
        return trimmed;
      }
    }
  }
  return null;
}

// 1.1 Valid JWS in verificationResultIOS
assert.strictEqual(
  extractSignedTransaction({ verificationResultIOS: 'eyJhbGciOiJFUzI1NiJ9.eyJ0eCI6IjEyMyJ9.sig' }),
  'eyJhbGciOiJFUzI1NiJ9.eyJ0eCI6IjEyMyJ9.sig'
);

// 1.2 Raw transactionId fallback is strictly rejected (Requirement 6)
assert.strictEqual(extractSignedTransaction({ transactionId: '2000000858172635' }), null);
assert.strictEqual(extractSignedTransaction({ transactionId: 'c4d12345-6789-abcd-ef01-23456789abcd' }), null);
assert.strictEqual(extractSignedTransaction({ transactionReceipt: 'not-a-jws-receipt' }), null);
assert.strictEqual(extractSignedTransaction({}), null);
console.log('✔ Test 1: JWS extraction and raw transaction ID rejection passed.');

// -------------------------------------------------------------
// Test 2: Multi-account session isolation & auth check
// -------------------------------------------------------------
class MockIapSession {
  constructor() {
    this.activeSessionToken = null;
    this.sessionBackendVerified = new Map();
    this.sessionFinishedStoreKit = new Set();
    this.inFlight = new Set();
  }

  syncSessionToken(token) {
    if (token !== this.activeSessionToken) {
      this.activeSessionToken = token;
      this.sessionBackendVerified.clear();
      this.sessionFinishedStoreKit.clear();
      this.inFlight.clear();
    }
    return this.activeSessionToken;
  }
}

const session = new MockIapSession();

// User A logs in
session.syncSessionToken('token_user_A');
session.sessionBackendVerified.set('tx_1001', { isNewEnrollment: true, data: { purchase_id: 10 } });
session.sessionFinishedStoreKit.add('tx_1001');
assert.strictEqual(session.sessionFinishedStoreKit.has('tx_1001'), true);
assert.strictEqual(session.sessionBackendVerified.has('tx_1001'), true);

// User B logs in on same device -> cache must be completely wiped (Requirement 3)
session.syncSessionToken('token_user_B');
assert.strictEqual(session.sessionFinishedStoreKit.has('tx_1001'), false);
assert.strictEqual(session.sessionBackendVerified.has('tx_1001'), false);

// User logs out (token = null) -> cache must be wiped
session.syncSessionToken(null);
assert.strictEqual(session.syncSessionToken(null), null);
console.log('✔ Test 2: Multi-account session cache isolation passed.');

// -------------------------------------------------------------
// Test 3: Separation of backend verification vs StoreKit finish retry
// -------------------------------------------------------------
async function simulateVerifyAndFinish(purchase, sessionState, mockBackend, mockStoreKitFinish) {
  if (!sessionState.activeSessionToken) {
    return { success: false, backendVerified: false, finished: false, error: 'Authentication required' };
  }

  const txKey = purchase.transactionId;
  if (sessionState.sessionFinishedStoreKit.has(txKey)) {
    return { success: true, backendVerified: true, finished: true, alreadyFinished: true };
  }

  let backendData = null;
  let isNewEnrollment = false;

  // Step 1: Backend verification
  if (sessionState.sessionBackendVerified.has(txKey)) {
    const cached = sessionState.sessionBackendVerified.get(txKey);
    backendData = cached.data;
    // On retry, enrollment is already granted on backend
    isNewEnrollment = false;
  } else {
    const backendRes = await mockBackend(txKey);
    if (backendRes.status !== 200) {
      return { success: false, backendVerified: false, finished: false, error: 'Backend failed' };
    }
    backendData = backendRes.data;
    isNewEnrollment = (backendRes.data.status === 'SUCCESS');
    sessionState.sessionBackendVerified.set(txKey, {
      isNewEnrollment,
      data: backendData,
    });
  }

  // Step 2: StoreKit Finish
  try {
    await mockStoreKitFinish(txKey);
    sessionState.sessionFinishedStoreKit.add(txKey);
    return {
      success: true,
      backendVerified: true,
      finished: true,
      isNewEnrollment,
      previouslyVerifiedFinished: !isNewEnrollment,
      data: backendData,
    };
  } catch (err) {
    // Finish failed; do NOT mark finished in StoreKit; allow retry
    return {
      success: false,
      backendVerified: true,
      finished: false,
      isNewEnrollment,
      data: backendData,
      error: 'StoreKit finish failed',
    };
  }
}

(async () => {
  const simSession = new MockIapSession();

  // Test 3.1: Unauthenticated request rejected
  const unauthRes = await simulateVerifyAndFinish({ transactionId: 'tx_unauth' }, simSession, null, null);
  assert.strictEqual(unauthRes.success, false);
  assert.strictEqual(unauthRes.error, 'Authentication required');

  // Test 3.2: First run - Backend succeeds, StoreKit finish throws
  simSession.syncSessionToken('token_A');
  let backendCallCount = 0;
  const mockBackend = async () => {
    backendCallCount++;
    return { status: 200, data: { status: 'SUCCESS', purchase_id: 99 } };
  };

  let finishFails = true;
  const mockFinish = async () => {
    if (finishFails) throw new Error('StoreKit network timeout');
  };

  const res1 = await simulateVerifyAndFinish({ transactionId: 'tx_fail_finish' }, simSession, mockBackend, mockFinish);
  assert.strictEqual(res1.success, false);
  assert.strictEqual(res1.backendVerified, true);
  assert.strictEqual(res1.finished, false);
  assert.strictEqual(backendCallCount, 1);
  assert.strictEqual(simSession.sessionFinishedStoreKit.has('tx_fail_finish'), false);
  assert.strictEqual(simSession.sessionBackendVerified.has('tx_fail_finish'), true);

  // Test 3.3: Second run - Retry finish succeeds without duplicate backend call
  finishFails = false;
  const res2 = await simulateVerifyAndFinish({ transactionId: 'tx_fail_finish' }, simSession, mockBackend, mockFinish);
  assert.strictEqual(res2.success, true);
  assert.strictEqual(res2.backendVerified, true);
  assert.strictEqual(res2.finished, true);
  assert.strictEqual(res2.previouslyVerifiedFinished, true);
  assert.strictEqual(res2.isNewEnrollment, false);
  assert.strictEqual(res2.data.purchase_id, 99);
  assert.strictEqual(backendCallCount, 1); // No duplicate backend verification!
  assert.strictEqual(simSession.sessionFinishedStoreKit.has('tx_fail_finish'), true);
  console.log('✔ Test 3: StoreKit finish failure retry without duplicate backend verification passed.');

  // -------------------------------------------------------------
  // Test 4: Restore accounting categories & feedback classification
  // -------------------------------------------------------------
  function classifyRestore(result) {
    if (result.totalFound === 0) return 'NO_PURCHASES';
    if (result.newlyRestoredCount > 0 && result.failedCount === 0) return 'FULL_RESTORE_NEW';
    if (result.newlyRestoredCount > 0 && result.failedCount > 0) return 'PARTIAL_RESTORE';
    if (result.previouslyVerifiedFinishedCount > 0 && result.failedCount === 0) return 'PREVIOUSLY_VERIFIED_CONFIRMED';
    if (result.alreadyFinishedCount > 0 && result.failedCount === 0) return 'ALREADY_ACTIVE';
    if (result.inFlightCount > 0 && result.failedCount === 0) return 'PROCESSING';
    return 'FAILED';
  }

  assert.strictEqual(classifyRestore({ totalFound: 0, newlyRestoredCount: 0, previouslyVerifiedFinishedCount: 0, alreadyFinishedCount: 0, inFlightCount: 0, failedCount: 0 }), 'NO_PURCHASES');
  assert.strictEqual(classifyRestore({ totalFound: 1, newlyRestoredCount: 1, previouslyVerifiedFinishedCount: 0, alreadyFinishedCount: 0, inFlightCount: 0, failedCount: 0 }), 'FULL_RESTORE_NEW');
  assert.strictEqual(classifyRestore({ totalFound: 2, newlyRestoredCount: 1, previouslyVerifiedFinishedCount: 0, alreadyFinishedCount: 0, inFlightCount: 0, failedCount: 1 }), 'PARTIAL_RESTORE');
  assert.strictEqual(classifyRestore({ totalFound: 1, newlyRestoredCount: 0, previouslyVerifiedFinishedCount: 1, alreadyFinishedCount: 0, inFlightCount: 0, failedCount: 0 }), 'PREVIOUSLY_VERIFIED_CONFIRMED');
  assert.strictEqual(classifyRestore({ totalFound: 1, newlyRestoredCount: 0, previouslyVerifiedFinishedCount: 0, alreadyFinishedCount: 1, inFlightCount: 0, failedCount: 0 }), 'ALREADY_ACTIVE');
  assert.strictEqual(classifyRestore({ totalFound: 1, newlyRestoredCount: 0, previouslyVerifiedFinishedCount: 0, alreadyFinishedCount: 0, inFlightCount: 1, failedCount: 0 }), 'PROCESSING');
  assert.strictEqual(classifyRestore({ totalFound: 1, newlyRestoredCount: 0, previouslyVerifiedFinishedCount: 0, alreadyFinishedCount: 0, inFlightCount: 0, failedCount: 1 }), 'FAILED');
  console.log('✔ Test 4: Restore accounting 5-category tallying and classification passed.');

  // -------------------------------------------------------------
  // Test 5: iOS checkout wording & StoreKit localized currency rules
  // -------------------------------------------------------------
  const fs = require('fs');
  const path = require('path');

  function getCourseAppleSku(course, fallbackCourseId) {
    const explicit = course && course.apple_product_id ? String(course.apple_product_id).trim() : '';
    if (explicit) return explicit;
    const id = (course && course.id !== undefined && course.id !== null) ? course.id : fallbackCourseId;
    return id !== undefined && id !== null ? `com.natyaarts.academy.course.${id}` : '';
  }

  function resolveCourseCardDisplayPrice(platformOS, price, localizedPrice) {
    return platformOS === 'ios'
      ? (localizedPrice || null)
      : (price !== undefined ? `₹${price}` : null);
  }

  // 5.1 SKU resolution prefers explicit apple_product_id, falls back to bundle convention
  assert.strictEqual(
    getCourseAppleSku({ id: 7, apple_product_id: 'com.natyaarts.academy.course.custom7' }, 7),
    'com.natyaarts.academy.course.custom7'
  );
  assert.strictEqual(
    getCourseAppleSku({ id: 7, apple_product_id: null }),
    'com.natyaarts.academy.course.7'
  );
  assert.strictEqual(getCourseAppleSku(null, 12), 'com.natyaarts.academy.course.12');

  // 5.2 Platform price display: iOS uses StoreKit localizedPrice only (never INR fallback), Android preserves ₹
  assert.strictEqual(resolveCourseCardDisplayPrice('ios', '1499.00', '$19.99'), '$19.99');
  assert.strictEqual(resolveCourseCardDisplayPrice('ios', '1499.00', undefined), null);
  assert.strictEqual(resolveCourseCardDisplayPrice('android', '1499.00', '$19.99'), '₹1499.00');
  assert.strictEqual(resolveCourseCardDisplayPrice('android', undefined, '$19.99'), null);

  // 5.3 Source assertions across mobile checkout & course-card files
  const mobileSrc = path.join(__dirname, '..', 'src');
  const learnSrc = fs.readFileSync(path.join(mobileSrc, 'screens', 'LearnScreen.tsx'), 'utf8');
  const detailsSrc = fs.readFileSync(path.join(mobileSrc, 'screens', 'CourseDetailsScreen.tsx'), 'utf8');
  const cardSrc = fs.readFileSync(path.join(mobileSrc, 'components', 'CourseCard.tsx'), 'utf8');
  const catalogSrc = fs.readFileSync(path.join(mobileSrc, 'screens', 'CatalogScreen.tsx'), 'utf8');
  const dashboardSrc = fs.readFileSync(path.join(mobileSrc, 'screens', 'DashboardScreen.tsx'), 'utf8');

  // LearnScreen gates web checkout text off iOS and uses StoreKit localizedPrice on iOS while keeping Android Razorpay text
  assert.ok(learnSrc.includes("Platform.OS === 'ios'"), 'LearnScreen must branch on Platform.OS === ios');
  assert.ok(learnSrc.includes('In-App Purchase via App Store'), 'LearnScreen must show App Store IAP label on iOS');
  assert.ok(learnSrc.includes('iapProduct?.localizedPrice'), 'LearnScreen must use StoreKit localizedPrice on iOS');
  assert.ok(learnSrc.includes('Secure checkout via web'), 'LearnScreen must preserve Android/non-iOS web checkout wording');

  // CourseDetailsScreen gates ₹{course.price} off iOS and uses iapProduct.localizedPrice on iOS
  assert.ok(detailsSrc.includes('iapProduct?.localizedPrice'), 'CourseDetailsScreen must use StoreKit localizedPrice in header on iOS');
  assert.ok(detailsSrc.includes('Buy Course for ₹{course.price}'), 'CourseDetailsScreen must preserve Android Razorpay purchase button');

  // CourseCard, CatalogScreen, and DashboardScreen wire StoreKit localizedPrice on iOS
  assert.ok(cardSrc.includes('localizedPrice'), 'CourseCard must accept StoreKit localizedPrice');
  assert.ok(catalogSrc.includes('fetchAppleProducts'), 'CatalogScreen must batch-fetch StoreKit prices on iOS');
  assert.ok(dashboardSrc.includes('fetchAppleProducts'), 'DashboardScreen must batch-fetch StoreKit prices on iOS');
  console.log('✔ Test 5: iOS checkout wording & StoreKit localized currency rules passed.');

  // -------------------------------------------------------------
  // Test 6: Real appleIap.ts StoreKit 2 runtime integration tests
  // -------------------------------------------------------------
  const ts = require(path.join(__dirname, '..', 'node_modules', 'typescript'));
  const vm = require('vm');

  function makeJws(payloadObj) {
    const header = Buffer.from(JSON.stringify({ alg: 'ES256' })).toString('base64url');
    const payload = Buffer.from(JSON.stringify(payloadObj)).toString('base64url');
    return `${header}.${payload}.c2lnbmF0dXJl`;
  }

  function loadTranspiledAppleIap(envMocks) {
    const appleIapPath = path.join(mobileSrc, 'api', 'appleIap.ts');
    const source = fs.readFileSync(appleIapPath, 'utf8');
    const transpiled = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
        esModuleInterop: true,
      },
    }).outputText;

    const mod = { exports: {} };
    const customRequire = (id) => {
      if (id in envMocks) return envMocks[id];
      return require(id);
    };
    const fn = vm.runInNewContext(
      `(function(exports, require, module, __filename, __dirname) { ${transpiled}\n})`,
      { console, setTimeout, clearTimeout, TextDecoder }
    );
    fn(mod.exports, customRequire, mod, appleIapPath, path.dirname(appleIapPath));
    return mod.exports;
  }

  // Simulate react-native-iap v12.16.4 exact SK1 default + RNIapIosSk2 native quirks:
  // - Defaults to STOREKIT1_MODE (no JWS, ASN.1 base64 receipt only).
  // - Only switches to STOREKIT2_MODE when setup({ storekitMode: 'STOREKIT2_MODE' }) is called BEFORE listeners/initConnection.
  // - In SK2 mode, getAvailablePurchases({ onlyIncludeActiveItems: true }) filters against loaded productStore SKUs!
  // - In SK2 mode, getAvailablePurchases() does NOT add historical transactions to nativePendingTransactions,
  //   and finishTransaction() rejects with E_DEVELOPER_ERROR ("Invalid transaction Id") if transactionId is not in nativePendingTransactions!
  let currentToken = 'jwt_student_1';
  const callOrder = [];
  let storekitMode = 'STOREKIT1_MODE';
  let listenerBoundMode = null;
  let purchaseListener = null;
  const loadedProductSkus = new Set();
  const nativePendingTransactions = new Set();
  let finishCalls = [];
  let backendCalls = [];
  let sk2AllTransactions = [];
  let sk2SyncBuffer = [];
  let syncCalls = 0;

  const mockRNIap = {
    setup: ({ storekitMode: mode } = {}) => {
      callOrder.push(`setup:${mode}`);
      storekitMode = mode || 'STOREKIT1_MODE';
    },
    isIosStorekit2: () => storekitMode === 'STOREKIT2_MODE',
    initConnection: async () => {
      callOrder.push(`initConnection:${storekitMode}`);
      return true;
    },
    purchaseUpdatedListener: (cb) => {
      listenerBoundMode = storekitMode;
      callOrder.push(`purchaseUpdatedListener:${storekitMode}`);
      purchaseListener = cb;
      return { remove: () => { purchaseListener = null; } };
    },
    purchaseErrorListener: () => ({ remove: () => {} }),
    getProducts: async ({ skus }) => {
      skus.forEach((s) => loadedProductSkus.add(s));
      return skus.map((s) => ({
        productId: s,
        title: 'Course',
        description: 'Desc',
        price: '19.99',
        localizedPrice: '$19.99',
        currency: 'USD',
      }));
    },
    requestPurchase: async ({ sku }) => {
      const txId = '4000000111222333';
      if (storekitMode !== 'STOREKIT2_MODE') {
        // StoreKit 1 mode: single ASN.1 base64 receipt (NO dots, NO JWS)
        const sk1Purchase = {
          productId: sku,
          transactionId: txId,
          transactionReceipt: 'MIAGCSqGSIb3DQEHAqCAMIACAQExDzANBglghkgBZQMEAgEFAA==',
        };
        if (purchaseListener) await purchaseListener(sk1Purchase);
        return sk1Purchase;
      }
      // StoreKit 2 mode: adds to nativePendingTransactions and emits 3-part JWS in verificationResultIOS
      nativePendingTransactions.add(txId);
      const sk2Purchase = {
        productId: sku,
        transactionId: txId,
        transactionReceipt: '',
        verificationResultIOS: makeJws({
          transactionId: txId,
          originalTransactionId: txId,
          productId: sku,
          bundleId: 'com.natyaarts.academy',
          environment: 'Sandbox',
        }),
      };
      if (purchaseListener) await purchaseListener(sk2Purchase);
      return sk2Purchase;
    },
    getAvailablePurchases: async ({ onlyIncludeActiveItems = true } = {}) => {
      if (storekitMode !== 'STOREKIT2_MODE') {
        return [];
      }
      // Exact RNIapIosSk2.swift lines 628-641 behavior:
      if (onlyIncludeActiveItems) {
        return sk2AllTransactions.filter((t) => loadedProductSkus.has(t.productId));
      }
      return [...sk2AllTransactions];
    },
    finishTransaction: async ({ purchase }) => {
      const txId = purchase && purchase.transactionId;
      finishCalls.push(txId);
      // Exact RNIapIosSk2.swift lines 929-937 behavior:
      if (!nativePendingTransactions.has(txId)) {
        const err = new Error('Invalid transaction Id');
        err.code = 'E_DEVELOPER_ERROR';
        throw err;
      }
      nativePendingTransactions.delete(txId);
      return true;
    },
    IapIosSk2: {
      sync: async () => {
        syncCalls++;
        sk2AllTransactions = [...sk2SyncBuffer];
        return null;
      },
    },
  };

  const alerts = [];
  const appleIap = loadTranspiledAppleIap({
    'react-native': {
      Platform: { OS: 'ios' },
      Alert: { alert: (title, msg) => alerts.push({ title, msg }) },
    },
    '@react-native-async-storage/async-storage': {
      getItem: async (k) => (k === 'access_token' ? currentToken : null),
    },
    './client': {
      __esModule: true,
      default: {
        post: async (url, body) => {
          backendCalls.push({ url, body, token: currentToken });
          return {
            status: 200,
            data: { status: 'SUCCESS', purchase_id: 501, is_enrolled: true },
          };
        },
      },
      onSessionExpired: () => () => {},
    },
    'react-native-iap': mockRNIap,
  });

  // 6.1 subscribeIap() called BEFORE initIapConnection() (exact CourseDetailsScreen order)
  // must configure STOREKIT2_MODE synchronously BEFORE attaching purchaseUpdatedListener!
  let successEvents = 0;
  let errorEvents = 0;
  const unsub = appleIap.subscribeIap({
    onSuccess: () => { successEvents++; },
    onError: () => { errorEvents++; },
  });
  assert.strictEqual(storekitMode, 'STOREKIT2_MODE', 'StoreKit 2 mode must be activated synchronously');
  assert.strictEqual(listenerBoundMode, 'STOREKIT2_MODE', 'purchaseUpdatedListener must bind AFTER STOREKIT2_MODE is active');
  assert.strictEqual(callOrder[0], 'setup:STOREKIT2_MODE');
  assert.strictEqual(callOrder[1], 'purchaseUpdatedListener:STOREKIT2_MODE');

  // 6.2 Live purchase emits StoreKit 2 JWS, verifies once on backend, and finishes native pending transaction
  await appleIap.requestApplePurchase('com.natyaarts.academy.course.2');
  assert.strictEqual(errorEvents, 0);
  assert.strictEqual(successEvents, 1);
  assert.strictEqual(backendCalls.length, 1, 'Live purchase must verify with backend exactly once (no race duplicate)');
  assert.strictEqual(backendCalls[0].body.signed_transaction_info.split('.').length, 3);
  assert.deepStrictEqual(finishCalls, ['4000000111222333']);
  assert.strictEqual(nativePendingTransactions.has('4000000111222333'), false);

  // 6.3 Startup recovery & Restore Purchases when productStore is EMPTY (no getProducts called!)
  // Includes 1 revoked historical transaction (should be skipped) + 1 active non-consumable course purchase
  // NOT present in nativePendingTransactions (historical entitlement).
  loadedProductSkus.clear();
  backendCalls = [];
  finishCalls = [];
  currentToken = 'jwt_student_reinstall';

  const revokedTx = {
    productId: 'com.natyaarts.academy.course.3',
    transactionId: '4000000444555666',
    verificationResultIOS: makeJws({
      transactionId: '4000000444555666',
      productId: 'com.natyaarts.academy.course.3',
      bundleId: 'com.natyaarts.academy',
      revocationDate: 1730000000000,
    }),
  };
  const historicalActiveTx = {
    productId: 'com.natyaarts.academy.course.5',
    transactionId: '4000000777888999',
    verificationResultIOS: makeJws({
      transactionId: '4000000777888999',
      productId: 'com.natyaarts.academy.course.5',
      bundleId: 'com.natyaarts.academy',
      environment: 'Production',
    }),
  };
  sk2AllTransactions = [revokedTx, historicalActiveTx];

  const recoveryRes = await appleIap.recoverUnfinishedTransactions();
  assert.strictEqual(recoveryRes.recovered, 1);
  assert.strictEqual(recoveryRes.failed, 0);
  assert.strictEqual(backendCalls.length, 1, 'Active historical course recovered even with empty productStore; revoked tx skipped');
  assert.strictEqual(backendCalls[0].body.product_id, 'com.natyaarts.academy.course.5');
  assert.deepStrictEqual(finishCalls, [], 'Must NOT call finishTransaction on historical transaction absent from nativePendingTransactions');
  assert.strictEqual(syncCalls, 0, 'Startup recovery must NEVER trigger interactive AppStore.sync()');

  // 6.4 Explicit Restore Purchases on a fresh device (empty local cache -> triggers IapIosSk2.sync() -> restores cleanly without invalid finishTransaction)
  currentToken = 'jwt_student_new_device';
  backendCalls = [];
  finishCalls = [];
  sk2AllTransactions = [];
  sk2SyncBuffer = [historicalActiveTx];

  const restoreRes = await appleIap.restorePurchases();
  assert.strictEqual(syncCalls, 1, 'Explicit restorePurchases should sync with AppStore when local cache is empty');
  assert.strictEqual(restoreRes.totalFound, 1);
  assert.strictEqual(restoreRes.newlyRestoredCount, 1);
  assert.strictEqual(restoreRes.failedCount, 0);
  assert.strictEqual(backendCalls.length, 1);
  assert.deepStrictEqual(finishCalls, [], 'Restored historical transaction must not call finishTransaction on unknown native tx');

  // 6.5 Unfinished transaction that WAS emitted via Transaction.updates (present in nativePendingTransactions)
  // while user was logged out MUST be finished in StoreKit when recovered after login!
  currentToken = null;
  const unfinishedStartupTxId = '4000000999000111';
  nativePendingTransactions.add(unfinishedStartupTxId);
  const unfinishedStartupTx = {
    productId: 'com.natyaarts.academy.course.8',
    transactionId: unfinishedStartupTxId,
    verificationResultIOS: makeJws({
      transactionId: unfinishedStartupTxId,
      productId: 'com.natyaarts.academy.course.8',
      bundleId: 'com.natyaarts.academy',
      environment: 'Sandbox',
    }),
  };
  await purchaseListener(unfinishedStartupTx); // fires while unauthenticated -> retained in nativePendingTransactions
  assert.strictEqual(nativePendingTransactions.has(unfinishedStartupTxId), true);

  // Now user signs in and recoverUnfinishedTransactions runs:
  currentToken = 'jwt_student_after_login';
  backendCalls = [];
  finishCalls = [];
  sk2AllTransactions = [unfinishedStartupTx];
  const postLoginRecovery = await appleIap.recoverUnfinishedTransactions();
  assert.strictEqual(postLoginRecovery.recovered, 1);
  assert.strictEqual(postLoginRecovery.failed, 0);
  assert.strictEqual(backendCalls.length, 1);
  assert.deepStrictEqual(finishCalls, [unfinishedStartupTxId], 'Genuinely pending native transaction MUST call finishTransaction');
  assert.strictEqual(nativePendingTransactions.has(unfinishedStartupTxId), false);

  unsub();
  console.log('✔ Test 6: Real appleIap.ts StoreKit 2 activation, JWS flow, empty-productStore recovery & safe restore passed.');

  console.log('\nAll Apple IAP client accounting & lifecycle tests PASSED successfully.');
})();
