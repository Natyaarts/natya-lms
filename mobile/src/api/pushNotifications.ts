import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import Constants from 'expo-constants';
import AsyncStorage from '@react-native-async-storage/async-storage';
import client from './client';

// Phase 4.10: REGISTRATION (asking for permission, obtaining an Expo push
// token, telling the backend about it). Push DELIVERY gap fix (this
// phase): the backend now actually sends a push through Expo for every
// new Notification row, so this file also now exports
// resolveActionUrlToRoute -- a pure, ALLOW-LISTED mapping from a
// notification's action_url to one of this app's own existing screens.
// It deliberately does not do generic path parsing/opening -- only the
// exact action_url shapes the backend is actually known to produce
// (see backend/notifications/services.py / signals.py / courses/tasks.py)
// are recognized; anything else (including any external URL) falls back
// to the Notifications screen rather than being "opened" at all. The
// actual navigation call stays in App.tsx (which already owns
// navigationRef) to avoid a circular import between this file and App.tsx.

// Foreground notification presentation -- standard Expo boilerplate,
// required once at app startup so a notification that arrives while the
// app is open is actually shown (Expo's default is to suppress it).
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

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
    // Push tokens don't exist on a simulator/emulator -- Device.isDevice
    // is Expo's own documented way to detect that before ever asking.
    if (!Device.isDevice) return;

    const existing = await Notifications.getPermissionsAsync();
    let granted = existing.granted;
    if (!granted) {
      const requested = await Notifications.requestPermissionsAsync();
      granted = requested.granted;
    }
    if (!granted) return;

    const projectId = Constants.expoConfig?.extra?.eas?.projectId;
    const { data: token } = await Notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : undefined
    );
    if (!token) return;

    const previouslyRegistered = await AsyncStorage.getItem(STORAGE_KEY);
    if (previouslyRegistered === token) return; // already registered, nothing changed

    const platform = Platform.OS === 'ios' ? 'IOS' : 'ANDROID';
    await client.post('notifications/device-token/', { token, platform });
    await AsyncStorage.setItem(STORAGE_KEY, token);
  } catch (err) {
    // Never breaks app usage over a push-registration failure -- exactly
    // the same "swallow and log, never propagate" principle the backend's
    // own NotificationService already follows for its own side effects.
    console.error('Push notification registration failed:', err);
  }
}

/**
 * Deactivates this device's token on logout, best-effort. Clears the
 * locally-remembered token either way so a subsequent login always
 * re-registers fresh rather than trusting a now-deactivated one.
 */
export async function unregisterForPushNotificationsAsync(): Promise<void> {
  try {
    const token = await AsyncStorage.getItem(STORAGE_KEY);
    if (token) {
      await client.delete('notifications/device-token/', { data: { token } });
    }
  } catch (err) {
    console.error('Push notification unregistration failed:', err);
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
 *   /dashboard              -> MainTabs (My Learning tab)
 *   /courses/<id>/learn     -> Learn screen for that course
 *   /courses/<id>/live      -> LiveClasses (this screen has no
 *                              per-course filter today, so this is the
 *                              closest existing match, not a precise one)
 *   /live-classes           -> LiveClasses
 * Anything else -- including any absolute/external URL, or an
 * action_url the backend doesn't currently produce -- safely falls back
 * to the Notifications screen instead of being "opened" at all. This
 * function performs no navigation itself and never touches
 * window/Linking -- it only returns a plain {name, params} object for
 * App.tsx's own navigationRef to act on.
 */
export function resolveActionUrlToRoute(actionUrl: string | null | undefined): ResolvedRoute {
  const fallback: ResolvedRoute = { name: 'Notifications' };
  if (!actionUrl || typeof actionUrl !== 'string') return fallback;

  const url = actionUrl.trim();

  if (url === '/dashboard') {
    return { name: 'MainTabs', params: { screen: 'My Learning' } };
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
