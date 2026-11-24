import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, ActivityIndicator, ScrollView, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import client from '../api/client';
import ScreenHeader from '../components/ScreenHeader';
import CustomVideoPlayer, { PlaybackRefreshResult } from '../components/CustomVideoPlayer';
import Icon from '../components/Icon';
import { parseVtt, SubtitleCue } from '../utils/vtt';
import { colors, spacing, radius, typography } from '../theme';

// Plays one historical recording. Every request is authorized by the backend
// (404 when the recording is not mapped to one of the student's batches):
//   GET courses/historical-recordings/files/<id>/playback/  -> 15-minute signed URL
//   GET courses/historical-recordings/files/<id>/subtitles/ -> WebVTT text
// The signed URL is refreshed before it expires (onRefreshUrls), so long
// recordings keep playing.

type Playback = { url: string; expires_in: number; has_subtitles?: boolean; subtitle_url?: string | null };

function playbackError(err: any) {
  const status = err?.response?.status;
  if (status === 404) return 'This recording is not available to your account.';
  if (status === 401) return 'Please sign in again.';
  return 'Could not start playback. Please try again.';
}

export default function HistoricalRecordingPlayerScreen({ navigation, route }: any) {
  const { fileId, title, courseTitle, teacherName, actualStart, duration } = route?.params || {};
  const [playback, setPlayback] = useState<Playback | null>(null);
  const [captions, setCaptions] = useState<SubtitleCue[] | undefined>(undefined);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  const fetchPlayback = useCallback(async (): Promise<Playback> => {
    const res = await client.get(`courses/historical-recordings/files/${encodeURIComponent(String(fileId))}/playback/`);
    return res.data;
  }, [fileId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchPlayback();
        if (cancelled) return;
        setPlayback(data);
        setError('');
        if (data.has_subtitles || data.subtitle_url) {
          try {
            const subs = await client.get(
              `courses/historical-recordings/files/${encodeURIComponent(String(fileId))}/subtitles/`,
              { responseType: 'text', transformResponse: (r: any) => r }
            );
            const cues = parseVtt(String(subs.data || ''));
            if (!cancelled && cues.length) setCaptions(cues);
          } catch {
            // Subtitles are optional; playback continues without them.
          }
        }
      } catch (err) {
        if (!cancelled) setError(playbackError(err));
      }
    })();
    return () => { cancelled = true; };
  }, [fetchPlayback, fileId, attempt]);

  const handleRefreshUrls = useCallback(async (): Promise<PlaybackRefreshResult | null> => {
    try {
      const fresh = await fetchPlayback();
      return { source: fresh.url, expiresIn: fresh.expires_in };
    } catch (err: any) {
      if (err?.response?.status === 404) setError(playbackError(err)); // access removed meanwhile
      return null;
    }
  }, [fetchPlayback]);

  const when = (() => {
    if (!actualStart) return '';
    const d = new Date(actualStart);
    return isNaN(d.getTime()) ? '' : d.toLocaleString();
  })();

  return (
    <SafeAreaView style={styles.container} edges={['top', 'left', 'right', 'bottom']}>
      <ScreenHeader title="Recording" onBack={() => navigation.goBack()} />
      <View style={styles.videoContainer}>
        {error ? (
          <View style={styles.centered}>
            <Icon name="alert-circle" size={22} color={colors.danger} />
            <Text style={styles.errorText}>{error}</Text>
            {error !== 'This recording is not available to your account.' && (
              <TouchableOpacity
                style={styles.retryButton}
                onPress={() => { setError(''); setPlayback(null); setAttempt(a => a + 1); }}
                accessibilityRole="button"
                accessibilityLabel="Try again"
              >
                <Text style={styles.retryText}>Try again</Text>
              </TouchableOpacity>
            )}
          </View>
        ) : !playback ? (
          <View style={styles.centered}>
            <ActivityIndicator color={colors.accent} size="large" />
            <Text style={styles.preparing}>Preparing secure playback…</Text>
          </View>
        ) : (
          <CustomVideoPlayer
            key={String(fileId)}
            source={playback.url}
            expiresIn={playback.expires_in}
            onRefreshUrls={handleRefreshUrls}
            screenCaptureProtectionEnabled
            captions={captions}
          />
        )}
      </View>

      <ScrollView contentContainerStyle={styles.details}>
        <Text style={styles.title}>{title || 'Class recording'}</Text>
        {!!courseTitle && <Text style={styles.meta}>{courseTitle}</Text>}
        {!!teacherName && <Text style={styles.meta}>By {teacherName}</Text>}
        {!!(when || duration) && <Text style={styles.when}>{[when, duration].filter(Boolean).join(' · ')}</Text>}
        {!!captions?.length && <Text style={styles.hint}>Subtitles available — tap the screen and use CC to turn them on or off.</Text>}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  videoContainer: { width: '100%', aspectRatio: 16 / 9, backgroundColor: '#000', position: 'relative' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.lg },
  preparing: { ...typography.caption, color: colors.textTertiary, marginTop: spacing.sm },
  errorText: { color: colors.text, fontSize: 14, textAlign: 'center', marginTop: spacing.sm },
  retryButton: {
    marginTop: spacing.md, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm,
    borderRadius: radius.md, backgroundColor: colors.accent,
  },
  retryText: { color: colors.textInverse, fontWeight: '700', fontSize: 13 },
  details: { padding: spacing.lg },
  title: { ...typography.section, color: colors.text, fontSize: 17, fontWeight: '700' },
  meta: { ...typography.meta, color: colors.textSecondary, marginTop: 4 },
  when: { ...typography.caption, color: colors.textTertiary, marginTop: 6 },
  hint: { ...typography.caption, color: colors.textTertiary, marginTop: spacing.md },
});
