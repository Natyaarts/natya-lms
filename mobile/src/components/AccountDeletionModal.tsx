import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Modal,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
} from 'react-native';
import client from '../api/client';
import Icon from './Icon';
import { colors, spacing, radius, typography } from '../theme';

interface AccountDeletionModalProps {
  visible: boolean;
  user: any;
  onClose: () => void;
  onDeleted: () => void;
}

type ModalStep = 'CONFIRM' | 'OTP';

function maskIdentifier(user: any): string {
  if (user?.phone_number) {
    const raw = String(user.phone_number).trim();
    if (raw.length > 4) {
      const visible = raw.slice(-4);
      const prefix = raw.startsWith('+') ? raw.slice(0, 3) + ' ' : '';
      return `${prefix}••••• •${visible}`;
    }
    return raw;
  }
  if (user?.email) {
    const email = String(user.email).trim();
    const parts = email.split('@');
    if (parts.length === 2 && parts[0].length > 2) {
      return `${parts[0][0]}••••${parts[0].slice(-1)}@${parts[1]}`;
    }
    return email;
  }
  return 'your registered contact';
}

export default function AccountDeletionModal({
  visible,
  user,
  onClose,
  onDeleted,
}: AccountDeletionModalProps) {
  const [step, setStep] = useState<ModalStep>('CONFIRM');
  const [reason, setReason] = useState('');
  const [otp, setOtp] = useState('');
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [countdown, setCountdown] = useState(0);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    if (countdown > 0) {
      timer = setTimeout(() => setCountdown(countdown - 1), 1000);
    }
    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [countdown]);

  const resetState = () => {
    setStep('CONFIRM');
    setReason('');
    setOtp('');
    setErrorMessage('');
    setLoading(false);
    setCountdown(0);
  };

  const handleClose = () => {
    if (loading) return;
    resetState();
    onClose();
  };

  const handleRequestOtp = async (isResend = false) => {
    if (loading) return;
    setLoading(true);
    setErrorMessage('');

    try {
      await client.post('users/delete-account/request-otp/', {});
      setStep('OTP');
      setCountdown(60);
    } catch (err: any) {
      const status = err.response?.status;
      const serverError = err.response?.data?.error;

      if (status === 429) {
        setErrorMessage(serverError || 'Too many OTP requests. Please wait a few minutes and try again.');
      } else if (status === 409) {
        setErrorMessage(serverError || 'An account deletion is already being processed.');
      } else if (serverError) {
        setErrorMessage(serverError);
      } else if (!err.response) {
        setErrorMessage('Network error. Please check your internet connection.');
      } else {
        setErrorMessage('Failed to send verification code. Please try again.');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async () => {
    const trimmedOtp = otp.trim();
    if (trimmedOtp.length !== 4 && trimmedOtp.length !== 6) {
      setErrorMessage('Please enter a valid 4 or 6-digit code.');
      return;
    }
    if (loading) return;

    setLoading(true);
    setErrorMessage('');

    try {
      const res = await client.post('users/delete-account/verify-otp/', {
        otp: trimmedOtp,
        reason: reason.trim(),
      });

      if (res.status === 200 || res.status === 201) {
        resetState();
        onDeleted();
      } else {
        setErrorMessage('Unexpected response. Please try again.');
      }
    } catch (err: any) {
      const serverError = err.response?.data?.error;
      if (serverError) {
        setErrorMessage(serverError);
      } else if (!err.response) {
        setErrorMessage('Network error. Please check your internet connection.');
      } else {
        setErrorMessage('Verification failed. Please check the code and try again.');
      }
    } finally {
      setLoading(false);
    }
  };

  const maskedContact = maskIdentifier(user);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={handleClose}>
      <KeyboardAvoidingView
        style={styles.overlay}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={styles.sheet}>
          <ScrollView
            contentContainerStyle={styles.scrollContent}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            <View style={styles.header}>
              <View style={styles.headerTitleWrap}>
                <View style={[styles.iconWrap, { backgroundColor: colors.danger + '22' }]}>
                  <Icon name="alert-triangle" size={18} color={colors.danger} />
                </View>
                <Text style={styles.title}>
                  {step === 'CONFIRM' ? 'Delete Account' : 'Verify Deletion'}
                </Text>
              </View>
              <TouchableOpacity
                onPress={handleClose}
                disabled={loading}
                accessibilityRole="button"
                accessibilityLabel="Close"
              >
                <Icon name="x" size={20} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            {errorMessage ? (
              <View style={styles.errorBox}>
                <Icon name="alert-circle" size={15} color={colors.danger} />
                <Text style={styles.errorText}>{errorMessage}</Text>
              </View>
            ) : null}

            {step === 'CONFIRM' ? (
              <View style={styles.body}>
                <Text style={styles.warningHeading}>
                  Are you sure you want to permanently delete your account?
                </Text>

                <View style={styles.infoList}>
                  <View style={styles.infoItem}>
                    <Icon name="user-x" size={15} color={colors.danger} style={styles.infoIcon} />
                    <Text style={styles.infoText}>
                      Your profile will be deactivated and your active session ended immediately.
                    </Text>
                  </View>
                  <View style={styles.infoItem}>
                    <Icon name="trash-2" size={15} color={colors.danger} style={styles.infoIcon} />
                    <Text style={styles.infoText}>
                      Personal details, saved preferences, and notification tokens will be removed.
                    </Text>
                  </View>
                  <View style={styles.infoItem}>
                    <Icon name="shield" size={15} color={colors.textSecondary} style={styles.infoIcon} />
                    <Text style={styles.infoText}>
                      In accordance with tax and legal regulations, payment invoices and earned certificates will be retained.
                    </Text>
                  </View>
                  <View style={styles.infoItem}>
                    <Icon name="credit-card" size={15} color={colors.textSecondary} style={styles.infoIcon} />
                    <Text style={styles.infoText}>
                      Any active recurring subscriptions will be cancelled.
                    </Text>
                  </View>
                </View>

                <Text style={styles.inputLabel}>Reason for leaving (optional)</Text>
                <TextInput
                  style={styles.reasonInput}
                  placeholder="Tell us why you are leaving..."
                  placeholderTextColor={colors.textTertiary}
                  value={reason}
                  onChangeText={setReason}
                  maxLength={250}
                  multiline
                  numberOfLines={3}
                  editable={!loading}
                />

                <TouchableOpacity
                  style={[styles.primaryDeleteButton, loading && styles.buttonDisabled]}
                  onPress={() => handleRequestOtp(false)}
                  disabled={loading}
                  accessibilityRole="button"
                  accessibilityLabel="Send Deletion OTP"
                >
                  {loading ? (
                    <ActivityIndicator color={colors.textInverse} size="small" />
                  ) : (
                    <Text style={styles.primaryDeleteButtonText}>Send Deletion OTP</Text>
                  )}
                </TouchableOpacity>

                <TouchableOpacity
                  style={styles.cancelButton}
                  onPress={handleClose}
                  disabled={loading}
                  accessibilityRole="button"
                  accessibilityLabel="Cancel"
                >
                  <Text style={styles.cancelButtonText}>Keep My Account</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <View style={styles.body}>
                <Text style={styles.otpDescription}>
                  A verification code has been sent to{' '}
                  <Text style={styles.contactHighlight}>{maskedContact}</Text>. Enter the code below to permanently delete your account.
                </Text>

                <TextInput
                  style={styles.otpInput}
                  placeholder="000000"
                  placeholderTextColor={colors.textTertiary}
                  keyboardType="number-pad"
                  value={otp}
                  onChangeText={(val) => {
                    setOtp(val);
                    if (errorMessage) setErrorMessage('');
                  }}
                  maxLength={6}
                  autoFocus
                  editable={!loading}
                />

                <TouchableOpacity
                  style={[styles.confirmDeleteButton, (loading || otp.trim().length < 4) && styles.buttonDisabled]}
                  onPress={handleVerifyOtp}
                  disabled={loading || otp.trim().length < 4}
                  accessibilityRole="button"
                  accessibilityLabel="Confirm and Delete Account"
                >
                  {loading ? (
                    <ActivityIndicator color="#fff" size="small" />
                  ) : (
                    <Text style={styles.confirmDeleteButtonText}>Permanently Delete Account</Text>
                  )}
                </TouchableOpacity>

                <View style={styles.resendRow}>
                  {countdown > 0 ? (
                    <Text style={styles.countdownText}>Resend code in {countdown}s</Text>
                  ) : (
                    <TouchableOpacity
                      onPress={() => handleRequestOtp(true)}
                      disabled={loading}
                      accessibilityRole="button"
                      accessibilityLabel="Resend Code"
                    >
                      <Text style={styles.resendText}>Resend Code</Text>
                    </TouchableOpacity>
                  )}
                </View>

                <TouchableOpacity
                  style={styles.backButton}
                  onPress={() => {
                    setStep('CONFIRM');
                    setOtp('');
                    setErrorMessage('');
                  }}
                  disabled={loading}
                  accessibilityRole="button"
                  accessibilityLabel="Back"
                >
                  <Text style={styles.backButtonText}>Back to options</Text>
                </TouchableOpacity>
              </View>
            )}
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.72)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: colors.card,
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
    borderWidth: 1,
    borderColor: colors.border,
    maxHeight: '90%',
  },
  scrollContent: {
    padding: spacing.xl,
    paddingBottom: spacing.xxxl,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.lg,
  },
  headerTitleWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  iconWrap: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: {
    ...typography.title,
    fontSize: 18,
  },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.danger + '18',
    borderColor: colors.danger + '44',
    borderWidth: 1,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.lg,
    gap: spacing.sm,
  },
  errorText: {
    ...typography.meta,
    color: colors.danger,
    flex: 1,
  },
  body: {
    gap: spacing.md,
  },
  warningHeading: {
    ...typography.body,
    fontWeight: '600',
    color: colors.text,
    marginBottom: spacing.xs,
  },
  infoList: {
    backgroundColor: colors.cardAlt,
    borderRadius: radius.md,
    padding: spacing.md,
    gap: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: spacing.sm,
  },
  infoItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
  },
  infoIcon: {
    marginTop: 2,
  },
  infoText: {
    ...typography.meta,
    color: colors.textSecondary,
    flex: 1,
    lineHeight: 18,
  },
  inputLabel: {
    ...typography.meta,
    color: colors.textSecondary,
  },
  reasonInput: {
    backgroundColor: colors.cardAlt,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    padding: spacing.md,
    fontSize: 14,
    minHeight: 70,
    textAlignVertical: 'top',
  },
  primaryDeleteButton: {
    backgroundColor: colors.danger,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.sm,
  },
  primaryDeleteButtonText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '700',
  },
  cancelButton: {
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelButtonText: {
    ...typography.bodyRegular,
    color: colors.textSecondary,
  },
  otpDescription: {
    ...typography.bodyRegular,
    color: colors.textSecondary,
    lineHeight: 20,
    marginBottom: spacing.sm,
  },
  contactHighlight: {
    color: colors.text,
    fontWeight: '600',
  },
  otpInput: {
    backgroundColor: colors.cardAlt,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    color: colors.text,
    fontSize: 24,
    fontWeight: '700',
    letterSpacing: 8,
    textAlign: 'center',
    paddingVertical: spacing.md,
    marginVertical: spacing.sm,
  },
  confirmDeleteButton: {
    backgroundColor: colors.danger,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.sm,
  },
  confirmDeleteButtonText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '700',
  },
  resendRow: {
    alignItems: 'center',
    paddingVertical: spacing.sm,
  },
  countdownText: {
    ...typography.meta,
    color: colors.textTertiary,
  },
  resendText: {
    ...typography.meta,
    color: colors.accent,
    fontWeight: '600',
  },
  backButton: {
    alignItems: 'center',
    paddingVertical: spacing.sm,
  },
  backButtonText: {
    ...typography.meta,
    color: colors.textSecondary,
  },
  buttonDisabled: {
    opacity: 0.5,
  },
});
