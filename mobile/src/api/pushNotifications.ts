import { Platform } from 'react-native';
import type * as NotificationsType from 'expo-notifications';
import * as Device from 'expo-device';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { isRunningInExpoGo } from 'expo';
import AsyncStorage from '@react-native-async-storage/async-storage';
import client from './client';

export const isExpoGo = Boolean(
  isRunningInExpoGo?.() ||
  Constants.executionEnvironment === ExecutionEnvironment.StoreClient ||
  (Constants as any).appOwnership === 'expo'
);

/**
 * Safely resolves expo-notifications only when NOT running in Expo Go on Android.
 * In Expo Go on Android (SDK 53+), importing/evaluating expo-notifications at top level
 * automatically executes DevicePushTokenAutoRegistration.fx at bundle startup,
 * which calls addPushTokenListener -> warnOfExpoGoPushUsage and throws a fatal Error.
 */
export function getNotifications(): typeof NotificationsType | null {
  if (Platform.OS === 'android' && isExpoGo) {
    return null;
  }
  try {
    return require('expo-notifications');
  } catch {
    return null;
  }
}

// Foreground notification presentation -- standard Expo boilerplate.
// Only registered if the native notifications module is available.
const notificationsModule = getNotifications();
if (notificationsModule) {
  try {
    notificationsModule.setNotificationHandler({
      handleNotification: async () => ({
        shouldShowBanner: true,
        shouldShowList: true,
        shouldPlaySound: false,
        shouldSetBadge: false,
      }),
    });
  } catch (e) {
    console.warn('setNotificationHandler failed:', e);
  }
}

const STORAGE_KEY = 'expo_push_token';

/**
 * Requests notification permission (if not already granted) and, on
 * success, obtains this device's Expo push token and registers it with
 * the backend. Safe to call repeatedly (e.g. on every app start / login)
 * -- registration is an upsert (see DeviceTokenView), and this function
 * itself skips re-registering an unchanged token. Never throws; a denied
 * permission or a failed network call just means push isn't set up this
 * time, which must never block login/app usage.
 */
export async function registerForPushNotificationsAsync(): Promise<void> {
  try {
    // Remote notifications functionality was removed from Expo Go on Android in SDK 53+.
    // Calling getExpoPushTokenAsync in Expo Go on Android throws an error.
    // Skip registration in Expo Go; supported builds (development/production) continue normally.
    if (isExpoGo) return;

    // Push tokens don't exist on a simulator/emulator -- Device.isDevice
    // is Expo's own documented way to detect that before ever asking.
    if (!Device.isDevice) return;

    const notifications = getNotifications();
    if (!notifications) return;

    const existing = await notifications.getPermissionsAsync();
    let granted = existing.granted;
    if (!granted) {
      const requested = await notifications.requestPermissionsAsync();
      granted = requested.granted;
    }
    if (!granted) return;

    const projectId = Constants.expoConfig?.extra?.eas?.projectId;
    const { data: token } = await notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : undefined
    );
    if (!token) return;

    const previouslyRegistered = await AsyncStorage.getItem(STORAGE_KEY);
    if (previouslyRegistered === token) return; // already registered, nothing changed

    const platform = Platform.OS === 'ios' ? 'IOS' : 'ANDROID';
    await client.post('notifications/device-token/', { token, platform });
    await AsyncStorage.setItem(STORAGE_KEY, token);
  } catch (err) {
    // Never breaks app usage over a push-registration failure -- log as warning
    // so it doesn't pop a RedBox crash modal in development.
    console.warn('Push notification registration skipped or failed:', err);
  }
}

/**
 * Deactivates this device's token on logout, best-effort. Clears the
 * locally-remembered token either way so a subsequent login always
 * re-registers fresh rather than trusting a now-deactivated one.
 */
export async function unregisterForPushNotificationsAsync(): Promise<void> {
  try {
    if (isExpoGo) return;
    const token = await AsyncStorage.getItem(STORAGE_KEY);
    if (token) {
      await client.delete('notifications/device-token/', { data: { token } });
    }
  } catch (err) {
    console.warn('Push notification unregistration failed:', err);
  } finally {
    await AsyncStorage.removeItem(STORAGE_KEY);
  }
}

export type ResolvedRoute = { name: string; params?: Record<string, any> };

/**
 * Push delivery gap fix -- Step 6 (tap/deep-link). Maps a notification's
 * action_url to one of this app's own EXISTING registered screens, using
 * an explicit allow-list of exact patterns, never generic URL parsing.
 * Only the action_url shapes the backend is actually known to produce
 * today are recognized (see backend/notifications/services.py,
 * notifications/signals.py, courses/tasks.py, courses/views.py):
 *   /dashboard              -> MainTabs (Home tab)
 *   /courses/<id>/learn     -> Learn screen for that course
 *   /courses/<id>/live      -> LiveClasses (this screen has no
 *                              per-course filter today, so this is the
 *                              closest existing match, not a precise one)
 *   /live-classes           -> LiveClasses
 * Anything else -- including a missing/empty action_url, any absolute/
 * external URL, or a shape the backend doesn't currently produce --
 * safely falls back to Home rather than being "opened" at all. Home
 * (not the Notifications screen) is the intentional fallback: a
 * notification whose payload doesn't name a specific destination should
 * still land somewhere useful, and Home is that default. This function
 * performs no navigation itself and never touches window/Linking -- it
 * only returns a plain {name, params} object for App.tsx's own
 * navigationRef to act on.
 */
export function resolveActionUrlToRoute(actionUrl: string | null | undefined): ResolvedRoute {
  const fallback: ResolvedRoute = { name: 'MainTabs', params: { screen: 'Home' } };
  if (!actionUrl || typeof actionUrl !== 'string') return fallback;

  const url = actionUrl.trim();

  if (url === '/dashboard') {
    return { name: 'MainTabs', params: { screen: 'Home' } };
  }

  let match = url.match(/^\/courses\/(\d+)\/learn$/);
  if (match) {
    return { name: 'Learn', params: { courseId: Number(match[1]), isEnrolled: true } };
  }

  match = url.match(/^\/courses\/(\d+)\/live$/);
  if (match) {
    return { name: 'LiveClasses' };
  }

  if (url === '/live-classes') {
    return { name: 'LiveClasses' };
  }

  return fallback;
}
