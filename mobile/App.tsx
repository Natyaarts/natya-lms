import React, { useEffect, useState } from 'react';
import { NavigationContainer, createNavigationContainerRef } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { StatusBar, ActivityIndicator, View } from 'react-native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import AsyncStorage from '@react-native-async-storage/async-storage';

import LoginScreen from './src/screens/LoginScreen';
import DashboardScreen from './src/screens/DashboardScreen';
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
import * as Notifications from 'expo-notifications';
import { resolveActionUrlToRoute } from './src/api/pushNotifications';
import { initSentry, Sentry } from './src/api/sentry';

initSentry();

const Stack = createNativeStackNavigator();
const Tab = createBottomTabNavigator();

// Phase 4.9: lets src/api/client.ts (a plain module, not a component) send
// this app back to Login the instant a session is confirmed fully expired
// (refresh itself failed -- see client.ts's onSessionExpired). The standard
// React Navigation pattern for "navigate from outside a component".
export const navigationRef = createNavigationContainerRef();

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: '#050505', borderTopColor: '#27272a' },
        tabBarActiveTintColor: '#facc15',
        tabBarInactiveTintColor: '#a1a1aa',
      }}
    >
      <Tab.Screen name="My Learning" component={DashboardScreen} />
      <Tab.Screen name="Catalog" component={CatalogScreen} />
      <Tab.Screen name="Profile" component={ProfileScreen} />
    </Tab.Navigator>
  );
}

function App() {
  const [initialRoute, setInitialRoute] = useState<string | null>(null);

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
    // Push delivery gap fix -- Step 6 (tap/deep-link). Two cases per
    // Expo's own documented pattern: the app was already running/
    // backgrounded (addNotificationResponseReceivedListener fires), or
    // the app was fully closed and this tap is what launched it
    // (getLastNotificationResponseAsync, checked once on mount). Both
    // paths funnel through the same resolveActionUrlToRoute allow-list --
    // never navigate anywhere action_url itself literally says, only to
    // whichever known screen that helper maps it to (Notifications, as a
    // safe fallback, if nothing matches).
    const navigateForResponse = (response: Notifications.NotificationResponse | null | undefined) => {
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

    Notifications.getLastNotificationResponseAsync().then(navigateForResponse).catch(() => {});
    const subscription = Notifications.addNotificationResponseReceivedListener(navigateForResponse);
    return () => subscription.remove();
  }, []);

  if (!initialRoute) {
    return (
      <View style={{ flex: 1, backgroundColor: '#050505', justifyContent: 'center', alignItems: 'center' }}>
        <ActivityIndicator color="#facc15" size="large" />
      </View>
    );
  }

  return (
    <>
      <StatusBar barStyle="light-content" backgroundColor="#050505" />
      <NavigationContainer ref={navigationRef}>
        <Stack.Navigator initialRouteName={initialRoute} screenOptions={{ headerShown: false, contentStyle: { backgroundColor: '#050505' } }}>
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
    <View style={{ flex: 1, backgroundColor: '#050505', justifyContent: 'center', alignItems: 'center', padding: 24 }}>
      <ActivityIndicator color="#facc15" size="large" style={{ marginBottom: 16 }} />
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
    <Sentry.ErrorBoundary fallback={<ErrorFallback />}>
      <App />
    </Sentry.ErrorBoundary>
  );
}

export default Sentry.wrap(AppRoot);
