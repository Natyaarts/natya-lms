import axios from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';

// Base API URL for production
export const API_URL = 'https://academy-api.natyaarts.com/api/';
// Same host the app already builds absolute media URLs against (thumbnails,
// video_file, translated audio) -- exported once here so every screen stops
// re-hardcoding this string (see resolveMediaUrl below).
export const MEDIA_BASE_URL = 'https://academy-api.natyaarts.com';

export const resolveMediaUrl = (url?: string | null): string | undefined => {
  if (!url) return undefined;
  return url.startsWith('/') ? `${MEDIA_BASE_URL}${url}` : url;
};

const client = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Phase 4.9: session refresh + expiration handling. Previously this file had
// no response interceptor at all -- a request made after the 1-day access
// token expired (SIMPLE_JWT.ACCESS_TOKEN_LIFETIME) just failed with a bare
// 401 that every screen already swallows into a console.error/empty state,
// silently stranding the student rather than either refreshing quietly or
// sending them back to Login. This adds both halves: a queued, single-flight
// refresh using POST users/mobile-token-refresh/ (the plain, unwrapped
// simplejwt TokenRefreshView added alongside this change specifically for
// bearer-token mobile clients -- api/auth/token/refresh/, dj_rest_auth's own
// endpoint, is cookie-oriented and would silently strand mobile after
// exactly one refresh; see that URL's own comment in backend/users/urls.py
// for the full reasoning), and a tiny pub/sub so App.tsx can react to a
// fully-expired session (refresh itself failed) without this file needing
// to import React Navigation.
type UnauthorizedListener = () => void;
const unauthorizedListeners: UnauthorizedListener[] = [];

export const onSessionExpired = (listener: UnauthorizedListener) => {
  unauthorizedListeners.push(listener);
  return () => {
    const idx = unauthorizedListeners.indexOf(listener);
    if (idx !== -1) unauthorizedListeners.splice(idx, 1);
  };
};

const notifySessionExpired = () => {
  unauthorizedListeners.forEach((listener) => listener());
};

// A plain axios instance (NOT `client`) for the refresh call itself -- it
// must never go through this same interceptor, or a failed refresh would
// recursively trigger another refresh attempt.
const refreshClient = axios.create({ baseURL: API_URL });

// Single-flight: if several requests 401 at (roughly) the same moment
// (e.g. a screen that fires 3 parallel GETs right after the access token
// expired), they must all wait on the SAME refresh call and then each
// retry with the new token -- never one refresh call per failed request.
let refreshPromise: Promise<string | null> | null = null;

const performRefresh = async (): Promise<string | null> => {
  const storedRefreshToken = await AsyncStorage.getItem('refresh_token');
  if (!storedRefreshToken) return null;
  try {
    const res = await refreshClient.post('users/mobile-token-refresh/', { refresh: storedRefreshToken });
    const { access, refresh } = res.data;
    if (!access) return null;
    await AsyncStorage.setItem('access_token', access);
    // ROTATE_REFRESH_TOKENS is on server-side -- the old refresh token is
    // blacklisted the instant this call succeeds, so the new one MUST be
    // persisted or the next refresh attempt would fail outright.
    if (refresh) {
      await AsyncStorage.setItem('refresh_token', refresh);
    }
    return access;
  } catch (err) {
    return null;
  }
};

client.interceptors.request.use(
  async (config) => {
    const token = await AsyncStorage.getItem('access_token');
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    // Only ever retry once per request (the `_retry` flag), and only for a
    // genuine 401 (an expired/invalid access token) -- a 403 (e.g. locked
    // course content, grading-authorization) is a real, intentional
    // rejection, not an expired-session signal, and must never trigger a
    // logout.
    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true;

      if (!refreshPromise) {
        refreshPromise = performRefresh().finally(() => {
          refreshPromise = null;
        });
      }
      const newAccessToken = await refreshPromise;

      if (newAccessToken) {
        originalRequest.headers['Authorization'] = `Bearer ${newAccessToken}`;
        return client(originalRequest);
      }

      // Refresh itself failed (refresh token missing, expired, or already
      // blacklisted) -- this session is genuinely over. Clear local tokens
      // and let App.tsx (the only place with a navigation ref) decide how
      // to get the student back to Login.
      await AsyncStorage.removeItem('access_token');
      await AsyncStorage.removeItem('refresh_token');
      notifySessionExpired();
    }
    return Promise.reject(error);
  }
);

export default client;
