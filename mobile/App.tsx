import React, { useEffect, useRef, useState } from 'react';
import { NavigationContainer, createNavigationContainerRef } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { StatusBar, ActivityIndicator, View, StyleSheet, Platform } from 'react-native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import LoginScreen from './src/screens/LoginScreen';
import DashboardScreen from './src/screens/DashboardScreen';
import MyLearningScreen from './src/screens/MyLearningScreen';
import LearnScreen from './src/screens/LearnScreen';
import OnboardingScreen from './src/screens/OnboardingScreen';
import CatalogScreen from './src/screens/CatalogScreen';
import CourseDetailsScreen from './src/screens/CourseDetailsScreen';
import ProfileScreen from './src/screens/ProfileScreen';
import EditProfileScreen from './src/screens/EditProfileScreen';
import AssessmentScreen from './src/screens/AssessmentScreen';
import AssessmentAttemptScreen from './src/screens/AssessmentAttemptScreen';
import AttemptHistoryScreen from './src/screens/AttemptHistoryScreen';
import AssignmentScreen from './src/screens/AssignmentScreen';
import LiveClassesScreen from './src/screens/LiveClassesScreen';
import SubscriptionScreen from './src/screens/SubscriptionScreen';
import NotificationsScreen from './src/screens/NotificationsScreen';
import CertificatesScreen from './src/screens/CertificatesScreen';
import InvoicesScreen from './src/screens/InvoicesScreen';
import PurchaseHistoryScreen from './src/screens/PurchaseHistoryScreen';
import client, { onSessionExpired } from './src/api/client';
import type { NotificationResponse } from 'expo-notifications';
import { resolveActionUrlToRoute, isExpoGo, getNotifications } from './src/api/pushNotifications';
import { initSentry, isSentryEnabled, Sentry } from './src/api/sentry';
import Icon from './src/components/Icon';
import { colors } from './src/theme';

initSentry();

const Stack = createNativeStackNavigator();
const Tab = createBottomTabNavigator();

// Phase 4.9: lets src/api/client.ts (a plain module, not a component) send
// this app back to Login the instant a session is confirmed fully expired
// (refresh itself failed -- see client.ts's onSessionExpired). The standard
// React Navigation pattern for "navigate from outside a component".
export const navigationRef = createNavigationContainerRef();

const TAB_ICONS = {
  Home: 'home',
  Explore: 'search',
  'My Learning': 'book-open',
  Profile: 'user',
} as const;

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarStyle: styles.tabBar,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.textSecondary,
        tabBarLabelStyle: styles.tabLabel,
        tabBarItemStyle: styles.tabItem,
        tabBarIcon: ({ color, focused }) => (
          <Icon
            name={TAB_ICONS[route.name as keyof typeof TAB_ICONS]}
            size={22}
            color={color}
          />
        ),
      })}
    >
      <Tab.Screen name="Home" component={DashboardScreen} />
      <Tab.Screen name="Explore" component={CatalogScreen} />
      <Tab.Screen name="My Learning" component={MyLearningScreen} />
      <Tab.Screen name="Profile" component={ProfileScreen} />
    </Tab.Navigator>
  );
}

const styles = StyleSheet.create({
  tabBar: {
    // No explicit `height` here on purpose: @react-navigation/bottom-tabs
    // (see its own BottomTabBar getTabBarHeight()) only adds the device's
    // real bottom safe-area inset on top of its own default height when
    // tabBarStyle does NOT set a numeric height itself -- a fixed height
    // bypasses that entirely. On a real Android device with gesture
    // navigation (a meaningfully large bottom inset that emulators/most
    // dev builds don't reproduce), a fixed height left no room for that
    // inset, so the bar (and its last/rightmost item, Profile) rendered
    // squeezed into or clipped by the system navigation area -- visible
    // and "registered" in code, but not actually reachable on-device.
    backgroundColor: colors.bg,
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: 8,
  },
  tabItem: { paddingTop: 2 },
  tabLabel: { fontSize: 11, fontWeight: '600', marginTop: 2 },
});

function App() {
  const [initialRoute, setInitialRoute] = useState<string | null>(null);
  // getLastNotificationResponseAsync() and addNotificationResponseReceivedListener
  // can both fire for the exact same tap (Expo's own documented cold-start
  // behavior), which without a guard would call navigationRef.navigate() twice
  // for one notification tap. Tracked by the response's own stable identifier.
  const lastHandledNotificationId = useRef<string | null>(null);

  useEffect(() => {
    const checkToken = async () => {
      const token = await AsyncStorage.getItem('access_token');
      if (token) {
        try {
          // Verify with backend if onboarded -- routed through the shared
          // `client` (not a bare axios call) so a token that's expired but
          // still refreshable is silently refreshed here too, exactly like
          // every other screen's request, instead of duplicating that
          // logic with its own header injection.
          const res = await client.get('users/me/');
          if (res.data.is_onboarded) {
            setInitialRoute('MainTabs');
          } else {
            setInitialRoute('Onboarding');
          }
        } catch (err) {
          // Token missing/expired/unrefreshable, or a genuine network
          // failure on cold start -- either way there is nothing else this
          // screen can safely show, so fall back to Login (unchanged from
          // the app's existing behavior).
          setInitialRoute('Login');
        }
      } else {
        setInitialRoute('Login');
      }
    };
    checkToken();

    // A session that was valid when a screen loaded but expires (and fails
    // to silently refresh) DURING use -- e.g. mid-way through Learn or
    // Assessment -- resets straight to Login rather than leaving the
    // student stranded on a screen full of failed requests.
    const unsubscribe = onSessionExpired(() => {
      if (navigationRef.isReady()) {
        navigationRef.reset({ index: 0, routes: [{ name: 'Login' }] });
      }
    });
    return unsubscribe;
  }, []);

  useEffect(() => {
    // Remote notifications functionality was removed from Expo Go on Android in SDK 53+.
    // Skip notification response listener in Expo Go on Android; in dev client/standalone
    // production builds, listeners attach normally.
    if (Platform.OS === 'android' && isExpoGo) {
      return;
    }

    const notifications = getNotifications();
    if (!notifications) {
      return;
    }

    // Push delivery gap fix -- Step 6 (tap/deep-link). Two cases per
    // Expo's own documented pattern: the app was already running/
    // backgrounded (addNotificationResponseReceivedListener fires), or
    // the app was fully closed and this tap is what launched it
    // (getLastNotificationResponseAsync, checked once on mount). Both
    // paths funnel through the same resolveActionUrlToRoute allow-list --
    // never navigate anywhere action_url itself literally says, only to
    // whichever known screen that helper maps it to (Home, as a safe
    // fallback, if nothing more specific matches).
    const navigateForResponse = async (response: NotificationResponse | null | undefined) => {
      // Both listener paths can fire for the very same tap (documented
      // Expo behavior for a cold start), and getLastNotificationResponseAsync
      // keeps returning the same response on repeated calls -- this
      // identifier check stops a single tap from navigating twice.
      const notificationId = response?.notification?.request?.identifier;
      if (notificationId && lastHandledNotificationId.current === notificationId) return;
      if (notificationId) lastHandledNotificationId.current = notificationId;

      // Only route to authenticated screens if an access token exists.
      // If unauthenticated, the user must log in first before navigating to protected content.
      const token = await AsyncStorage.getItem('access_token');
      if (!token) return;

      const data = response?.notification?.request?.content?.data as { action_url?: string } | undefined;
      const route = resolveActionUrlToRoute(data?.action_url);

      let attempts = 0;
      const tryNavigate = () => {
        if (navigationRef.isReady()) {
          // navigationRef has no statically-typed param list anywhere in
          // this app (every screen's own navigation/route props are
          // already plainly `any`-typed, not a RootStackParamList) -- `as
          // any` here matches that same existing convention rather than
          // fighting React Navigation's strict overload typing for an
          // untyped ref.
          (navigationRef.navigate as any)(route.name, route.params);
        } else if (attempts < 10) {
          attempts += 1;
          setTimeout(tryNavigate, 300);
        }
        // Best-effort: after ~3s of the navigator still not being ready,
        // silently give up rather than risk crashing on a stale ref.
      };
      tryNavigate();
    };

    let subscription: { remove: () => void } | null = null;
    try {
      notifications.getLastNotificationResponseAsync().then(navigateForResponse).catch(() => {});
      subscription = notifications.addNotificationResponseReceivedListener(navigateForResponse);
    } catch (err) {
      console.warn('Notification listener registration failed:', err);
    }
    return () => subscription?.remove();
  }, []);

  if (!initialRoute) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg, justifyContent: 'center', alignItems: 'center' }}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  return (
    <>
      <StatusBar barStyle="light-content" backgroundColor={colors.bg} />
      <NavigationContainer ref={navigationRef}>
        <Stack.Navigator initialRouteName={initialRoute} screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }}>
          <Stack.Screen name="Login" component={LoginScreen} />
          <Stack.Screen name="Onboarding" component={OnboardingScreen} />
          <Stack.Screen name="MainTabs" component={MainTabs} />
          <Stack.Screen name="CourseDetails" component={CourseDetailsScreen} />
          <Stack.Screen name="Learn" component={LearnScreen} />
          <Stack.Screen name="Assessment" component={AssessmentScreen} />
          <Stack.Screen name="AssessmentAttempt" component={AssessmentAttemptScreen} />
          <Stack.Screen name="AttemptHistory" component={AttemptHistoryScreen} />
          <Stack.Screen name="Assignment" component={AssignmentScreen} />
          <Stack.Screen name="LiveClasses" component={LiveClassesScreen} />
          <Stack.Screen name="Subscription" component={SubscriptionScreen} />
          <Stack.Screen name="Notifications" component={NotificationsScreen} />
          <Stack.Screen name="Certificates" component={CertificatesScreen} />
          <Stack.Screen name="Invoices" component={InvoicesScreen} />
          <Stack.Screen name="PurchaseHistory" component={PurchaseHistoryScreen} />
          <Stack.Screen name="EditProfile" component={EditProfileScreen} />
        </Stack.Navigator>
      </NavigationContainer>
    </>
  );
}

// Fallback UI for a genuinely uncaught React render error -- matches the
// app's existing dark/yellow-accent design language (same colors as
// every screen's own loading/empty states), shown only if something
// escapes every other try/catch in the app.
function ErrorFallback() {
  return (
    <View style={{ flex: 1, backgroundColor: colors.bg, justifyContent: 'center', alignItems: 'center', padding: 24 }}>
      <ActivityIndicator color={colors.accent} size="large" style={{ marginBottom: 16 }} />
    </View>
  );
}

// Sentry.ErrorBoundary catches a React render error that would otherwise
// crash the whole app to a blank/native error screen with zero
// reporting; Sentry.wrap adds native touch-event breadcrumbs on top. Both
// are safe no-ops when initSentry() above never called Sentry.init() (no
// DSN configured) -- the fallback UI itself never depends on Sentry being
// active.
function AppRoot() {
  return (
    <SafeAreaProvider>
      <Sentry.ErrorBoundary fallback={<ErrorFallback />}>
        <App />
      </Sentry.ErrorBoundary>
    </SafeAreaProvider>
  );
}

export default (isSentryEnabled ? Sentry.wrap(AppRoot) : AppRoot);
