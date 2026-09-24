import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import Icon, { FeatherIconName } from './Icon';
import { colors, spacing, typography, radius } from '../theme';

interface EmptyStateProps {
  icon: FeatherIconName;
  title: string;
  message?: string;
  ctaLabel?: string;
  onPressCta?: () => void;
}

// Replaces every prior "no cartoon illustration exists yet, so nothing
// shows" or ad-hoc text-only empty state with one consistent, professional
// pattern: icon + title + short message + optional single CTA.
export default function EmptyState({ icon, title, message, ctaLabel, onPressCta }: EmptyStateProps) {
  return (
    <View style={styles.container} accessibilityRole="text">
      <View style={styles.iconCircle}>
        <Icon name={icon} size={26} color={colors.textSecondary} />
      </View>
      <Text style={styles.title}>{title}</Text>
      {!!message && <Text style={styles.message}>{message}</Text>}
      {!!ctaLabel && !!onPressCta && (
        <TouchableOpacity style={styles.cta} onPress={onPressCta} accessibilityRole="button" accessibilityLabel={ctaLabel}>
          <Text style={styles.ctaText}>{ctaLabel}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.xxl, paddingVertical: spacing.xxxl },
  iconCircle: {
    width: 56, height: 56, borderRadius: 28, backgroundColor: colors.card,
    borderWidth: 1, borderColor: colors.border,
    alignItems: 'center', justifyContent: 'center', marginBottom: spacing.lg,
  },
  title: { ...typography.title, textAlign: 'center', marginBottom: spacing.sm },
  message: { ...typography.bodyRegular, textAlign: 'center', marginBottom: spacing.lg },
  cta: { backgroundColor: colors.accent, paddingHorizontal: spacing.xl, paddingVertical: spacing.md, borderRadius: radius.md, marginTop: spacing.sm },
  ctaText: { color: colors.textInverse, fontSize: 14, fontWeight: '600' },
});
