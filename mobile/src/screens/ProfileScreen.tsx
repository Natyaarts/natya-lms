import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator, ScrollView, Alert, Linking } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import client from '../api/client';
import { unregisterForPushNotificationsAsync } from '../api/pushNotifications';
import Icon, { FeatherIconName } from '../components/Icon';
import AccountDeletionModal from '../components/AccountDeletionModal';
import { colors, spacing, radius, typography } from '../theme';

// Phase 4.9 -- profile screen: the missing navigation hub for every
// previously-unreachable P0 surface (live classes, subscription/payment
// status, notifications, certificates) plus the account's own basic info.
// Logout stays on DashboardScreen too (unchanged) -- this is an additional
// entry point, not a relocation.
export default function ProfileScreen({ navigation }: any) {
  const [user, setUser] = useState<any>(null);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [showDeleteModal, setShowDeleteModal] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const [userRes, countRes] = await Promise.allSettled([
          client.get('users/me/'),
          client.get('notifications/unread-count/'),
        ]);
        if (userRes.status === 'fulfilled') setUser(userRes.value.data);
        if (countRes.status === 'fulfilled') setUnreadCount(countRes.value.data.count || 0);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const handleLogout = async () => {
    try {
      await unregisterForPushNotificationsAsync();
    } catch (err) {
      console.warn('Push notification unregistration failed:', err);
    }
    await AsyncStorage.removeItem('access_token');
    await AsyncStorage.removeItem('refresh_token');
    // Same call DashboardScreen's own (unchanged) logout already uses --
    // React Navigation bubbles this up to the root Stack automatically
    // since 'Login' isn't one of MainTabs' own screens.
    navigation.replace('Login');
  };

  const handleAccountDeleted = async () => {
    setShowDeleteModal(false);
    try {
      await unregisterForPushNotificationsAsync();
    } catch (err) {
      console.warn('Push unregistration failed during account deletion:', err);
    }
    await AsyncStorage.removeItem('access_token');
    await AsyncStorage.removeItem('refresh_token');
    Alert.alert(
      'Account Deleted',
      'Your account has been permanently deleted and your session has ended.',
      [{ text: 'OK', onPress: () => navigation.replace('Login') }]
    );
    navigation.replace('Login');
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color={colors.accent} size="large" /></View>;

  const menuItems: { label: string; icon: FeatherIconName; screen?: string; url?: string; badge?: number }[] = [
    { label: 'Edit Profile', icon: 'edit-2', screen: 'EditProfile' },
    { label: 'Live Classes', icon: 'video', screen: 'LiveClasses' },
    { label: 'Subscription & Payments', icon: 'credit-card', screen: 'Subscription' },
    { label: 'Purchase History', icon: 'file-text', screen: 'PurchaseHistory' },
    { label: 'Notifications', icon: 'bell', screen: 'Notifications', badge: unreadCount },
    { label: 'Certificates', icon: 'award', screen: 'Certificates' },
    { label: 'Invoices', icon: 'file-text', screen: 'Invoices' },
    { label: 'Privacy Policy', icon: 'shield', url: 'https://academy.natyaarts.com/privacy' },
  ];

  const displayName = user?.first_name || user?.username || 'Student';

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView style={styles.scroll} contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <View style={styles.avatarSection}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{(user?.username || 'U')[0].toUpperCase()}</Text>
          </View>
          <Text style={styles.username}>{displayName}</Text>
          {!!user?.email && <Text style={styles.detail}>{user.email}</Text>}
          {!!user?.phone_number && <Text style={styles.detail}>{user.phone_number}</Text>}
        </View>

        <View style={styles.menu}>
          {menuItems.map((item, idx) => (
            <TouchableOpacity
              key={item.screen || item.label}
              style={[styles.menuRow, idx === menuItems.length - 1 && styles.menuRowLast]}
              onPress={() => {
                if (item.url) {
                  Linking.openURL(item.url).catch((err) => console.error("Couldn't open URL", err));
                } else if (item.screen) {
                  navigation.navigate(item.screen);
                }
              }}
              accessibilityRole="button"
              accessibilityLabel={item.label}
            >
              <View style={styles.menuIconWrap}>
                <Icon name={item.icon} size={17} color={colors.textSecondary} />
              </View>
              <Text style={styles.menuLabel}>{item.label}</Text>
              {!!item.badge && (
                <View style={styles.badge}><Text style={styles.badgeText}>{item.badge}</Text></View>
              )}
              <Icon name="chevron-right" size={18} color={colors.textTertiary} />
            </TouchableOpacity>
          ))}
        </View>

        <TouchableOpacity style={styles.logoutButton} onPress={handleLogout} accessibilityRole="button" accessibilityLabel="Log out">
          <Icon name="log-out" size={16} color={colors.danger} />
          <Text style={styles.logoutText}>Log Out</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.deleteButton}
          onPress={() => setShowDeleteModal(true)}
          accessibilityRole="button"
          accessibilityLabel="Delete Account"
        >
          <Icon name="trash-2" size={14} color={colors.textTertiary} />
          <Text style={styles.deleteText}>Delete Account</Text>
        </TouchableOpacity>

        <AccountDeletionModal
          visible={showDeleteModal}
          user={user}
          onClose={() => setShowDeleteModal(false)}
          onDeleted={handleAccountDeleted}
        />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.bg },
  // The ScrollView itself needs an explicit flex (not just contentContainerStyle)
  // to be bounded to the screen's available height -- without it, it sizes to
  // its own content instead of the viewport, so on a real device the bottom of
  // a full 7-item menu (Certificates/Invoices/Logout) can render past the
  // screen edge with nothing to actually scroll against to reach it.
  scroll: { flex: 1 },
  content: { padding: spacing.xl, paddingTop: spacing.xxl, paddingBottom: spacing.xxxl * 2 },
  avatarSection: { alignItems: 'center', marginBottom: spacing.xxxl },
  avatar: {
    width: 72, height: 72, borderRadius: 36, backgroundColor: colors.accent,
    justifyContent: 'center', alignItems: 'center', marginBottom: spacing.md,
  },
  avatarText: { color: colors.textInverse, fontSize: 26, fontWeight: '700' },
  username: { ...typography.title },
  detail: { ...typography.bodyRegular, marginTop: 2 },

  menu: {
    marginBottom: spacing.xxxl, backgroundColor: colors.card, borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.border, overflow: 'hidden',
  },
  menuRow: {
    flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.lg,
    paddingHorizontal: spacing.lg, borderBottomWidth: 1, borderBottomColor: colors.divider,
  },
  menuRowLast: { borderBottomWidth: 0 },
  menuIconWrap: {
    width: 32, height: 32, borderRadius: 16, backgroundColor: colors.cardAlt,
    alignItems: 'center', justifyContent: 'center', marginRight: spacing.md,
  },
  menuLabel: { ...typography.body, flex: 1 },
  badge: { backgroundColor: colors.accent, borderRadius: radius.pill, paddingHorizontal: 7, paddingVertical: 1, marginRight: spacing.sm },
  badgeText: { color: colors.textInverse, fontSize: 11, fontWeight: '700' },

  logoutButton: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm,
    borderWidth: 1, borderColor: colors.danger, borderRadius: radius.md, paddingVertical: spacing.md,
  },
  logoutText: { color: colors.danger, fontSize: 15, fontWeight: '700', marginLeft: spacing.sm },

  deleteButton: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs,
    paddingVertical: spacing.md, marginTop: spacing.md,
  },
  deleteText: { ...typography.meta, color: colors.textTertiary },
});

