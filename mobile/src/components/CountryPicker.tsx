import React, { useMemo, useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, Modal, FlatList } from 'react-native';
import Icon from './Icon';
import { colors, spacing, radius, typography } from '../theme';
import { COUNTRIES, Country, flagEmoji } from '../data/countries';

interface CountryPickerProps {
  visible: boolean;
  selected: Country;
  onSelect: (country: Country) => void;
  onClose: () => void;
}

export default function CountryPicker({ visible, selected, onSelect, onClose }: CountryPickerProps) {
  const [query, setQuery] = useState('');

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return COUNTRIES;
    return COUNTRIES.filter(
      (c) => c.name.toLowerCase().includes(q) || c.dialCode.includes(q) || c.iso2.toLowerCase() === q
    );
  }, [query]);

  const handleSelect = (country: Country) => {
    setQuery('');
    onSelect(country);
  };

  const handleClose = () => {
    setQuery('');
    onClose();
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={handleClose}>
      <View style={styles.overlay}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>Select country</Text>
            <TouchableOpacity onPress={handleClose} accessibilityRole="button" accessibilityLabel="Close">
              <Icon name="x" size={20} color={colors.textSecondary} />
            </TouchableOpacity>
          </View>

          <View style={styles.searchBox}>
            <Icon name="search" size={16} color={colors.textTertiary} />
            <TextInput
              style={styles.searchInput}
              placeholder="Search country or dial code"
              placeholderTextColor={colors.textTertiary}
              value={query}
              onChangeText={setQuery}
              autoCapitalize="none"
              autoCorrect={false}
            />
          </View>

          <FlatList
            data={results}
            keyExtractor={(item) => item.iso2}
            keyboardShouldPersistTaps="handled"
            renderItem={({ item }) => {
              const isActive = item.iso2 === selected.iso2;
              return (
                <TouchableOpacity
                  style={[styles.row, isActive && styles.rowActive]}
                  onPress={() => handleSelect(item)}
                  accessibilityRole="button"
                  accessibilityLabel={`${item.name} ${item.dialCode}`}
                >
                  <Text style={styles.flag}>{flagEmoji(item.iso2)}</Text>
                  <Text style={styles.countryName} numberOfLines={1}>{item.name}</Text>
                  <Text style={styles.dialCode}>{item.dialCode}</Text>
                  {isActive && <Icon name="check" size={16} color={colors.accent} style={{ marginLeft: spacing.sm }} />}
                </TouchableOpacity>
              );
            }}
            ListEmptyComponent={<Text style={styles.emptyText}>No matching countries</Text>}
            style={styles.list}
          />
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'flex-end' },
  sheet: {
    backgroundColor: colors.card,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    paddingTop: spacing.lg,
    maxHeight: '75%',
  },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: spacing.xl, marginBottom: spacing.md,
  },
  title: { ...typography.title, fontSize: 17 },
  searchBox: {
    flexDirection: 'row', alignItems: 'center', backgroundColor: colors.cardAlt,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    marginHorizontal: spacing.xl, paddingHorizontal: spacing.md, marginBottom: spacing.md,
  },
  searchInput: { flex: 1, paddingVertical: 10, paddingHorizontal: spacing.sm, fontSize: 15, color: colors.text },
  list: { paddingHorizontal: spacing.xl },
  row: {
    flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.md,
    borderBottomWidth: 1, borderBottomColor: colors.divider,
  },
  rowActive: { backgroundColor: colors.accentMuted },
  flag: { fontSize: 22, marginRight: spacing.md },
  countryName: { flex: 1, color: colors.text, fontSize: 15 },
  dialCode: { color: colors.textSecondary, fontSize: 14, fontVariant: ['tabular-nums'] },
  emptyText: { color: colors.textTertiary, textAlign: 'center', paddingVertical: spacing.xl },
});
