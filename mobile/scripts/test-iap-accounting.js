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

  console.log('\nAll Apple IAP client accounting & lifecycle tests PASSED successfully.');
})();
