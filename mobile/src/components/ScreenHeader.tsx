import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, StyleProp, ViewStyle, TextStyle } from 'react-native';
import { colors, spacing, typography, headerLayout } from '../theme';

interface ScreenHeaderProps {
  title: string;
  onBack?: () => void;
  backLabel?: string;
  rightAction?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  titleStyle?: StyleProp<TextStyle>;
  bordered?: boolean;
}

export default function ScreenHeader({
  title,
  onBack,
  backLabel = '← Back',
  rightAction,
  style,
  titleStyle,
  bordered = true,
}: ScreenHeaderProps) {
  return (
    <View style={[styles.header, !bordered && styles.noBorder, style]}>
      {onBack ? (
        <TouchableOpacity
          onPress={onBack}
          style={styles.backButton}
          accessibilityRole="button"
          accessibilityLabel={backLabel}
        >
          <Text style={styles.backText}>{backLabel}</Text>
        </TouchableOpacity>
      ) : null}
      <Text style={[styles.headerTitle, titleStyle]} numberOfLines={1}>
        {title}
      </Text>
      {rightAction ? (
        <View style={styles.rightAction}>{rightAction}</View>
      ) : onBack ? (
        <View style={styles.spacer} />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingTop: headerLayout.stackHeaderPaddingTop,
    paddingBottom: spacing.lg,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    backgroundColor: colors.bg,
  },
  noBorder: {
    borderBottomWidth: 0,
  },
  backButton: {
    paddingVertical: spacing.xs,
    paddingRight: spacing.md,
  },
  backText: {
    color: colors.accent,
    fontSize: 16,
    fontWeight: '600',
  },
  headerTitle: {
    ...typography.title,
    color: colors.text,
    fontSize: 18,
    fontWeight: '700',
    flex: 1,
    textAlign: 'left',
  },
  rightAction: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  spacer: {
    width: 20,
  },
});
