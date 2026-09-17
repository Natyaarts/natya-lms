import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, SafeAreaView, ActivityIndicator, ScrollView } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import client from '../api/client';
import { unregisterForPushNotificationsAsync } from '../api/pushNotifications';

// Phase 4.9 -- profile screen: the missing navigation hub for every
// previously-unreachable P0 surface (live classes, subscription/payment
// status, notifications, certificates) plus the account's own basic info.
// Logout stays on DashboardScreen too (unchanged) -- this is an additional
// entry point, not a relocation.
export default function ProfileScreen({ navigation }: any) {
  const [user, setUser] = useState<any>(null);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(true);

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
    await unregisterForPushNotificationsAsync();
    await AsyncStorage.removeItem('access_token');
    await AsyncStorage.removeItem('refresh_token');
    // Same call DashboardScreen's own (unchanged) logout already uses --
    // React Navigation bubbles this up to the root Stack automatically
    // since 'Login' isn't one of MainTabs' own screens.
    navigation.replace('Login');
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  const menuItems = [
    { label: 'Edit Profile', icon: '✏️', screen: 'EditProfile' },
    { label: 'Live Classes', icon: '🎥', screen: 'LiveClasses' },
    { label: 'Subscription & Payments', icon: '💳', screen: 'Subscription' },
    { label: 'Purchase History', icon: '🛍️', screen: 'PurchaseHistory' },
    { label: 'Notifications', icon: '🔔', screen: 'Notifications', badge: unreadCount },
    { label: 'Certificates', icon: '🎓', screen: 'Certificates' },
    { label: 'Invoices', icon: '🧾', screen: 'Invoices' },
  ];

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.avatarSection}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{(user?.username || 'U')[0].toUpperCase()}</Text>
          </View>
          <Text style={styles.username}>{user?.username}</Text>
          {!!user?.email && <Text style={styles.detail}>{user.email}</Text>}
          {!!user?.phone_number && <Text style={styles.detail}>{user.phone_number}</Text>}
        </View>

        <View style={styles.menu}>
          {menuItems.map((item) => (
            <TouchableOpacity key={item.screen} style={styles.menuRow} onPress={() => navigation.navigate(item.screen)}>
              <Text style={styles.menuIcon}>{item.icon}</Text>
              <Text style={styles.menuLabel}>{item.label}</Text>
              {!!item.badge && (
                <View style={styles.badge}><Text style={styles.badgeText}>{item.badge}</Text></View>
              )}
              <Text style={styles.menuArrow}>›</Text>
            </TouchableOpacity>
          ))}
        </View>

        <TouchableOpacity style={styles.logoutButton} onPress={handleLogout}>
          <Text style={styles.logoutText}>Log Out</Text>
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505' },
  content: { padding: 20, paddingTop: 40 },
  avatarSection: { alignItems: 'center', marginBottom: 32 },
  avatar: { width: 72, height: 72, borderRadius: 36, backgroundColor: '#facc15', justifyContent: 'center', alignItems: 'center', marginBottom: 12 },
  avatarText: { color: '#000', fontSize: 28, fontWeight: 'bold' },
  username: { color: '#fff', fontSize: 20, fontWeight: 'bold' },
  detail: { color: '#a1a1aa', fontSize: 14, marginTop: 2 },

  menu: { marginBottom: 32 },
  menuRow: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#0a0a0a', borderWidth: 1, borderColor: '#27272a', borderRadius: 12, padding: 16, marginBottom: 10 },
  menuIcon: { fontSize: 18, marginRight: 12 },
  menuLabel: { color: '#e4e4e7', fontSize: 15, flex: 1 },
  menuArrow: { color: '#52525b', fontSize: 20 },
  badge: { backgroundColor: '#facc15', borderRadius: 10, paddingHorizontal: 7, paddingVertical: 1, marginRight: 8 },
  badgeText: { color: '#000', fontSize: 11, fontWeight: 'bold' },

  logoutButton: { borderWidth: 1, borderColor: '#f87171', borderRadius: 12, paddingVertical: 14, alignItems: 'center' },
  logoutText: { color: '#f87171', fontSize: 15, fontWeight: 'bold' },
});
