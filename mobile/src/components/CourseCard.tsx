import React from 'react';
import { View, Text, Image, TouchableOpacity, StyleSheet } from 'react-native';
import Icon from './Icon';
import ProgressBar from './ProgressBar';
import { resolveMediaUrl } from '../api/client';
import { colors, spacing, radius, typography } from '../theme';

const FALLBACK_THUMB = 'https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop';

interface CourseCardProps {
  title: string;
  thumbnail?: string | null;
  moduleCount?: number;
  completionPercent?: number | null;
  isCompleted?: boolean;
  price?: number | string;
  locked?: boolean;
  onPress: () => void;
  // 'rail': fixed-width card for a horizontal scroller (Dashboard).
  // 'list': full-width image-first card for a vertical list (Catalog).
  variant?: 'rail' | 'list';
}

export default function CourseCard({
  title,
  thumbnail,
  moduleCount,
  completionPercent,
  isCompleted,
  price,
  locked,
  onPress,
  variant = 'list',
}: CourseCardProps) {
  const thumbUrl = resolveMediaUrl(thumbnail) || FALLBACK_THUMB;
  const isRail = variant === 'rail';

  return (
    <TouchableOpacity
      style={isRail ? styles.railCard : styles.listCard}
      onPress={onPress}
      activeOpacity={0.85}
      accessibilityRole="button"
      accessibilityLabel={title}
    >
      <View style={isRail ? styles.railImageWrap : styles.listImageWrap}>
        <Image source={{ uri: thumbUrl }} style={styles.image} />
        {locked && (
          <View style={styles.lockBadge}>
            <Icon name="lock" size={12} color={colors.text} />
          </View>
        )}
        {isCompleted && (
          <View style={styles.completeBadge}>
            <Icon name="check-circle" size={12} color={colors.accent} />
            <Text style={styles.completeBadgeText}>Completed</Text>
          </View>
        )}
      </View>

      <View style={isRail ? styles.railContent : styles.listContent}>
        <Text style={typography.body} numberOfLines={isRail ? 2 : 1}>{title}</Text>

        <View style={styles.metaRow}>
          {typeof moduleCount === 'number' && (
            <Text style={typography.meta}>{moduleCount} {moduleCount === 1 ? 'module' : 'modules'}</Text>
          )}
          {price !== undefined && (
            <Text style={styles.price}>₹{price}</Text>
          )}
        </View>

        {typeof completionPercent === 'number' && (
          <View style={styles.progressWrap}>
            <ProgressBar percent={completionPercent} />
          </View>
        )}
      </View>
    </TouchableOpacity>
  );
}

const CARD_RADIUS = radius.md;

const styles = StyleSheet.create({
  railCard: { width: 168, marginRight: spacing.md },
  railImageWrap: { width: '100%', aspectRatio: 16 / 9, borderRadius: CARD_RADIUS, overflow: 'hidden', backgroundColor: colors.card },
  railContent: { paddingTop: spacing.sm },

  listCard: {
    backgroundColor: colors.card, borderRadius: radius.lg, overflow: 'hidden',
    marginBottom: spacing.lg, borderWidth: 1, borderColor: colors.border,
  },
  listImageWrap: { width: '100%', aspectRatio: 16 / 9, backgroundColor: colors.cardAlt },
  listContent: { padding: spacing.lg },

  image: { width: '100%', height: '100%', resizeMode: 'cover' },

  lockBadge: {
    position: 'absolute', top: spacing.sm, right: spacing.sm,
    width: 26, height: 26, borderRadius: 13, backgroundColor: colors.scrim,
    alignItems: 'center', justifyContent: 'center',
  },
  completeBadge: {
    position: 'absolute', bottom: spacing.sm, left: spacing.sm,
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: colors.scrim, borderRadius: radius.pill,
    paddingHorizontal: spacing.sm, paddingVertical: 3,
  },
  completeBadgeText: { color: colors.accent, fontSize: 11, fontWeight: '600', marginLeft: 4 },

  metaRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 },
  price: { color: colors.accent, fontSize: 14, fontWeight: '600' },
  progressWrap: { marginTop: spacing.sm },
});
