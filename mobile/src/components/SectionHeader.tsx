import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import Icon from './Icon';
import { colors, spacing, typography } from '../theme';

interface SectionHeaderProps {
  title: string;
  onPressSeeAll?: () => void;
}

export default function SectionHeader({ title, onPressSeeAll }: SectionHeaderProps) {
  return (
    <View style={styles.row}>
      <Text style={styles.title}>{title}</Text>
      {!!onPressSeeAll && (
        <TouchableOpacity
          onPress={onPressSeeAll}
          style={styles.seeAll}
          accessibilityRole="button"
          accessibilityLabel={`See all ${title}`}
        >
          <Text style={styles.seeAllText}>See all</Text>
          <Icon name="chevron-right" size={14} color={colors.textSecondary} />
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: spacing.lg, marginBottom: spacing.md,
  },
  title: { ...typography.section },
  seeAll: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  seeAllText: { ...typography.meta, marginRight: 2 },
});
