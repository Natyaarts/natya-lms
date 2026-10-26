import React, { useEffect, useRef, useState } from 'react';
import { View, StyleSheet, TouchableOpacity, Text, Modal, ActivityIndicator } from 'react-native';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useEventListener } from 'expo';
import * as ScreenCapture from 'expo-screen-capture';
import Slider from '@react-native-community/slider';
import Icon, { MaterialIcon } from './Icon';
import { colors, spacing, radius } from '../theme';

// A helper to format seconds to mm:ss (or h:mm:ss for anything over an hour)
const formatTime = (seconds: number) => {
  if (!isFinite(seconds) || isNaN(seconds) || seconds < 0) return '00:00';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
};

// One manually-uploaded translated audio track for the active lesson.
// English isn't included here -- it's always the original video's own audio.
export interface AudioTrackOption {
  code: string;
  name: string;
  url: string;
}

export interface WatermarkConfig {
  enabled?: boolean;
  text?: string;
  student_id?: string;
  moving?: boolean;
  interval_seconds?: number;
}

export interface PlaybackRefreshResult {
  source: string;
  audioTracks?: AudioTrackOption[];
  expiresIn?: number;
}

interface CustomVideoPlayerProps {
  source: string;
  // Available alternate-language audio tracks for the current lesson
  // (backend-driven -- see courses/serializers.py TranslatedAudioSerializer).
  audioTracks?: AudioTrackOption[];
  // Changes whenever the active lesson changes, so the player can reset
  // language selection and stop any alternate audio from the old lesson.
  lessonKey?: string | number;
  // Saved resume position from backend progress API (in seconds)
  initialPosition?: number;
  // Phase 4.9: fired on every timeUpdate tick with the current playback
  // position and known duration -- lets the screen that owns this player
  // save lesson-completion progress without this component needing to
  // know anything about lessons/courses/the API itself.
  onProgress?: (currentTime: number, duration: number) => void;
  // Student watermark configuration
  watermark?: WatermarkConfig;
  // Screen-capture protection enabled from backend
  screenCaptureProtectionEnabled?: boolean;
  onRefreshUrls?: () => Promise<PlaybackRefreshResult | null>;
  expiresIn?: number;
}

// How far the alternate audio track is allowed to drift from the video
// before we force a re-sync (seconds). Small drift is normal and re-syncing
// too aggressively causes audible stutter.
const SYNC_DRIFT_TOLERANCE = 0.3;

const SEEK_STEP = 10;
const SPEED_OPTIONS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

export default function CustomVideoPlayer({
  source,
  audioTracks = [],
  lessonKey,
  initialPosition = 0,
  onProgress,
  watermark,
  screenCaptureProtectionEnabled = true,
  onRefreshUrls,
  expiresIn = 900,
}: CustomVideoPlayerProps) {
  const isRefreshingRef = useRef(false);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const consecutiveErrorsRef = useRef(0);
  const [showControls, setShowControls] = useState(true);
  const [showLanguageMenu, setShowLanguageMenu] = useState(false);
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [duration, setDuration] = useState(0);
  const [status, setStatus] = useState<'idle' | 'loading' | 'readyToPlay' | 'error'>('idle');
  const [speed, setSpeed] = useState(1);
  const speedRef = useRef(1);
  const [muted, setMuted] = useState(false);

  // 'en' = original video audio. Anything else must match an audioTracks[].code.
  const [activeAudioCode, setActiveAudioCode] = useState<string>('en');

  // Screenshot deterrence warning banner state (iOS deterrence & watermark attribution)
  const [showScreenshotWarning, setShowScreenshotWarning] = useState(false);
  const screenshotWarningTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const videoViewRef = useRef<VideoView>(null);
  const hasSoughtInitialRef = useRef(false);

  const player = useVideoPlayer(source, p => {
    p.loop = false;
    p.play();
  });

  const activeTrack = activeAudioCode !== 'en' ? audioTracks.find(t => t.code === activeAudioCode) : null;
  const altSource = activeTrack?.url ?? null;

  // A second, headless player used purely as an audio source for the
  // selected translated track. expo-video plays audio-only sources just
  // like video, so this reuses the one video/audio library already in the
  // project instead of adding a new dependency. Its source is recreated by
  // expo-video whenever `altSource` changes, which also cleanly releases
  // the previous track's player -- exactly what we want on language switch
  // or lesson change (when altSource becomes null again).
  const altPlayer = useVideoPlayer(altSource, p => {
    p.loop = false;
    if (altSource) {
      p.currentTime = player.currentTime;
      p.muted = false;
      if (player.playing) {
        p.play();
      }
    }
  });

  // Helper to safely seek to initialPosition only once per lesson
  const trySeekInitial = (targetPos: number, currentDuration: number) => {
    if (
      !hasSoughtInitialRef.current &&
      targetPos > 0 &&
      currentDuration > 0 &&
      targetPos < currentDuration
    ) {
      hasSoughtInitialRef.current = true;
      player.currentTime = targetPos;
      if (activeAudioCode !== 'en') {
        altPlayer.currentTime = targetPos;
      }
    }
  };

  // Screen-capture protection lifecycle (conditional on backend setting)
  useEffect(() => {
    if (screenCaptureProtectionEnabled) {
      ScreenCapture.preventScreenCaptureAsync('playback-protection').catch(err => {
        console.warn('Could not activate screen capture protection:', err);
      });
    }

    return () => {
      if (screenCaptureProtectionEnabled) {
        ScreenCapture.allowScreenCaptureAsync('playback-protection').catch(err => {
          console.warn('Could not release screen capture protection:', err);
        });
      }
    };
  }, [screenCaptureProtectionEnabled]);

  // iOS screenshot deterrence listener (Expo ScreenCapture API)
  // When screen_capture_protection_enabled is true during recorded lesson playback,
  // listen for screenshot events and display an in-player warning referencing the student watermark ID.
  // Note: On iOS, screenshots cannot be blocked at the OS level without FairPlay DRM; detection acts as a deterrent.
  // On Android, FLAG_SECURE (via preventScreenCaptureAsync above) blocks captures at the OS window manager.
  useEffect(() => {
    if (!screenCaptureProtectionEnabled || !source) {
      return;
    }

    let subscription: { remove: () => void } | null = null;

    try {
      if (typeof ScreenCapture.addScreenshotListener === 'function') {
        subscription = ScreenCapture.addScreenshotListener(() => {
          setShowScreenshotWarning(true);
          if (screenshotWarningTimerRef.current) {
            clearTimeout(screenshotWarningTimerRef.current);
          }
          screenshotWarningTimerRef.current = setTimeout(() => {
            setShowScreenshotWarning(false);
          }, 5000);
        });
      }
    } catch (err) {
      // Gracefully handle environments (such as web or unsupported platforms)
      console.warn('Screenshot listener registration failed:', err);
    }

    return () => {
      if (screenshotWarningTimerRef.current) {
        clearTimeout(screenshotWarningTimerRef.current);
      }
      try {
        if (subscription?.remove) {
          subscription.remove();
        } else if (subscription && typeof ScreenCapture.removeScreenshotListener === 'function') {
          ScreenCapture.removeScreenshotListener(subscription as any);
        }
      } catch (err) {
        console.warn('Error removing screenshot listener:', err);
      }
    };
  }, [screenCaptureProtectionEnabled, source]);

  const dismissScreenshotWarning = () => {
    if (screenshotWarningTimerRef.current) {
      clearTimeout(screenshotWarningTimerRef.current);
    }
    setShowScreenshotWarning(false);
  };

  // Watermark configuration & dynamic positioning
  const watermarkEnabled = watermark?.enabled !== false && !!(watermark?.text || watermark?.student_id);
  const watermarkText = watermark?.text || watermark?.student_id || '';
  // Stable student watermark identifier for attribution/deterrence (NAT-XXXXXX)
  const studentWatermarkId =
    watermark?.student_id ||
    (watermark?.text && watermark.text.startsWith('NAT-') ? watermark.text : undefined);
  const isMovingWatermark = watermark?.moving !== false;
  const watermarkInterval =
    watermark?.interval_seconds && watermark.interval_seconds >= 3
      ? watermark.interval_seconds * 1000
      : 15000;

  const [watermarkPos, setWatermarkPos] = useState({ top: 16, left: 14 });

  useEffect(() => {
    if (!watermarkEnabled || !isMovingWatermark) return;

    const moveWatermark = () => {
      // Safe visible boundaries: top: 8%..75%, left: 6%..62%
      const nextTop = Math.floor(8 + Math.random() * 67);
      const nextLeft = Math.floor(6 + Math.random() * 56);
      setWatermarkPos({ top: nextTop, left: nextLeft });
    };

    const intervalId = setInterval(moveWatermark, watermarkInterval);
    return () => clearInterval(intervalId);
  }, [watermarkEnabled, isMovingWatermark, watermarkInterval]);

  // Keep the original video's own audio muted while a translated track is active.
  useEffect(() => {
    player.muted = activeAudioCode !== 'en' ? true : muted;
  }, [activeAudioCode, player, muted]);

  // Reset to English and stop any alternate audio whenever the lesson changes,
  // so audio from the previous lesson never keeps playing into the new one.
  useEffect(() => {
    hasSoughtInitialRef.current = false;
    setActiveAudioCode('en');
    setShowLanguageMenu(false);
    setShowSpeedMenu(false);
    consecutiveErrorsRef.current = 0;
  }, [lessonKey]);

  // Track time updates
  useEventListener(player, 'timeUpdate', (payload) => {
    setCurrentTime(payload.currentTime);
    // Periodically correct alternate-audio drift (e.g. after buffering stalls).
    if (activeAudioCode !== 'en' && Math.abs(altPlayer.currentTime - payload.currentTime) > SYNC_DRIFT_TOLERANCE) {
      altPlayer.currentTime = payload.currentTime;
    }
    onProgress?.(payload.currentTime, duration);
  });

  // Track playing state and mirror play/pause onto the alternate audio track.
  useEventListener(player, 'playingChange', (payload) => {
    setIsPlaying(payload.isPlaying);
    if (activeAudioCode !== 'en') {
      if (payload.isPlaying) {
        if (Math.abs(altPlayer.currentTime - player.currentTime) > SYNC_DRIFT_TOLERANCE) {
          altPlayer.currentTime = player.currentTime;
        }
        altPlayer.play();
      } else {
        altPlayer.pause();
      }
    }
  });

  // Track duration when source loads
  useEventListener(player, 'sourceLoad', (payload) => {
    setDuration(payload.duration);
    if (initialPosition > 0) {
      trySeekInitial(initialPosition, payload.duration);
    }
  });

  // Loading/buffering state, straight from the player's own real status --
  // never simulated locally.
  useEventListener(player, 'statusChange', (payload) => {
    setStatus(payload.status);
    if (payload.status === 'readyToPlay') {
      consecutiveErrorsRef.current = 0;
      if (initialPosition > 0) {
        trySeekInitial(initialPosition, duration || player.duration);
      }
    } else if (payload.status === 'error') {
      if (onRefreshUrls && consecutiveErrorsRef.current < 2 && !isRefreshingRef.current) {
        consecutiveErrorsRef.current += 1;
        performRefresh(true);
      }
    }
  });

  const scheduleProactiveRefresh = (expirySec: number) => {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    if (!onRefreshUrls) return;

    // Refresh around 12 minutes (or expirySec - 180s)
    const delaySeconds = Math.max(5, expirySec - 180);
    refreshTimerRef.current = setTimeout(() => {
      performRefresh(false);
    }, delaySeconds * 1000);
  };

  const performRefresh = async (isErrorRecovery = false): Promise<boolean> => {
    if (!onRefreshUrls || isRefreshingRef.current) return false;
    isRefreshingRef.current = true;
    try {
      const result = await onRefreshUrls();
      if (!result || !result.source) {
        if (refreshTimerRef.current) {
          clearTimeout(refreshTimerRef.current);
          refreshTimerRef.current = null;
        }
        return false;
      }

      const currentPos = player.currentTime;
      const wasPlaying = player.playing;
      const currentSpeed = speedRef.current;

      // In expo-video, replaceAsync / replace swaps the underlying source in-place
      if (typeof (player as any).replaceAsync === 'function') {
        await (player as any).replaceAsync(result.source);
      } else if (typeof player.replace === 'function') {
        player.replace(result.source);
      }
      player.playbackRate = currentSpeed;
      player.currentTime = currentPos;
      if (wasPlaying) {
        player.play();
      }

      // Update alternate audio if active
      if (activeAudioCode !== 'en' && result.audioTracks) {
        const matchingTrack = result.audioTracks.find(t => t.code === activeAudioCode);
        if (matchingTrack) {
          if (typeof (altPlayer as any).replaceAsync === 'function') {
            await (altPlayer as any).replaceAsync(matchingTrack.url);
          } else if (typeof altPlayer.replace === 'function') {
            altPlayer.replace(matchingTrack.url);
          }
          altPlayer.playbackRate = currentSpeed;
          altPlayer.currentTime = currentPos;
          if (wasPlaying) {
            altPlayer.play();
          }
        }
      }

      consecutiveErrorsRef.current = 0;
      scheduleProactiveRefresh(result.expiresIn || expiresIn || 900);
      return true;
    } catch (err) {
      console.warn('Failed to refresh media playback URLs.');
      return false;
    } finally {
      isRefreshingRef.current = false;
    }
  };

  // Timer lifecycle for proactive refresh
  useEffect(() => {
    if (onRefreshUrls) {
      scheduleProactiveRefresh(expiresIn || 900);
    }
    return () => {
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
      isRefreshingRef.current = false;
    };
  }, [lessonKey, onRefreshUrls, expiresIn]);

  // Double check seek if initialPosition arrives after duration is already known
  useEffect(() => {
    if (initialPosition > 0 && (duration > 0 || player.duration > 0)) {
      trySeekInitial(initialPosition, duration || player.duration);
    }
  }, [initialPosition, duration]);

  // Keep the alternate track's rate in lockstep with the main video whenever
  // the user changes playback speed.
  useEventListener(player, 'playbackRateChange', (payload) => {
    setSpeed(payload.playbackRate);
    speedRef.current = payload.playbackRate;
    if (activeAudioCode !== 'en') {
      altPlayer.playbackRate = payload.playbackRate;
    }
  });

  // Hide controls after 3 seconds of inactivity
  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    if (showControls && isPlaying) {
      timeout = setTimeout(() => {
        setShowControls(false);
      }, 3000);
    }
    return () => clearTimeout(timeout);
  }, [showControls, isPlaying, currentTime]);

  const togglePlayPause = () => {
    if (isPlaying) {
      player.pause();
    } else {
      player.play();
    }
    setShowControls(true);
  };

  const handleSeek = (value: number) => {
    player.currentTime = value;
    if (activeAudioCode !== 'en') {
      altPlayer.currentTime = value;
    }
    setShowControls(true);
  };

  const seekBy = (deltaSeconds: number) => {
    player.seekBy(deltaSeconds);
    if (activeAudioCode !== 'en') {
      // seekBy has no direct alt-player equivalent here; nudge it to match
      // and let the SYNC_DRIFT_TOLERANCE correction above keep it locked.
      altPlayer.currentTime = Math.max(0, Math.min(duration || Infinity, altPlayer.currentTime + deltaSeconds));
    }
    setShowControls(true);
  };

  const selectAudioTrack = (code: string) => {
    setActiveAudioCode(code);
    setShowLanguageMenu(false);
  };

  const selectSpeed = (rate: number) => {
    player.playbackRate = rate;
    setShowSpeedMenu(false);
  };

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    if (activeAudioCode === 'en') {
      player.muted = next;
    } else {
      altPlayer.muted = next;
    }
  };

  const enterFullscreen = () => {
    videoViewRef.current?.enterFullscreen();
  };

  return (
    <View style={styles.container}>
      <TouchableOpacity
        style={styles.videoTouchArea}
        activeOpacity={1}
        onPress={() => setShowControls(prev => !prev)}
      >
        <VideoView
          ref={videoViewRef}
          player={player}
          style={styles.video}
          nativeControls={false}
          contentFit="contain"
          fullscreenOptions={{ enable: true, orientation: 'landscape', autoExitOnRotate: true }}
        />
      </TouchableOpacity>

      {/* Student Watermark Overlay (visible over playback) */}
      {watermarkEnabled && !!watermarkText && (
        <View
          pointerEvents="none"
          style={[
            styles.watermarkContainer,
            {
              top: `${watermarkPos.top}%`,
              left: `${watermarkPos.left}%`,
            },
          ]}
        >
          <View style={styles.watermarkBadge}>
            <Text style={styles.watermarkText}>{watermarkText}</Text>
            <Text style={styles.watermarkSubtext}>Natya LMS</Text>
          </View>
        </View>
      )}

      {/* iOS Screenshot Deterrence Warning Banner (discreet, accessible) */}
      {showScreenshotWarning && (
        <View
          style={styles.screenshotWarningBanner}
          accessibilityRole="alert"
          accessibilityLiveRegion="assertive"
          accessibilityLabel={
            studentWatermarkId
              ? `Protected content — this capture contains your student watermark: ${studentWatermarkId}`
              : 'Protected content — captures are watermarked and unauthorized sharing is prohibited.'
          }
        >
          <View style={styles.screenshotWarningIconContainer}>
            <Icon name="shield" size={14} color={colors.warning} />
          </View>
          <View style={styles.screenshotWarningTextContainer}>
            <Text style={styles.screenshotWarningTitle}>Screenshot Detected</Text>
            <Text style={styles.screenshotWarningBody} numberOfLines={2}>
              {studentWatermarkId
                ? `Protected content — this capture contains your student watermark: ${studentWatermarkId}`
                : 'Protected content — captures are watermarked and unauthorized sharing is prohibited.'}
            </Text>
          </View>
          <TouchableOpacity
            style={styles.screenshotWarningDismiss}
            onPress={dismissScreenshotWarning}
            accessibilityRole="button"
            accessibilityLabel="Dismiss screenshot warning"
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          >
            <Icon name="x" size={14} color={colors.textSecondary} />
          </TouchableOpacity>
        </View>
      )}

      {status === 'loading' && (
        <View style={styles.loadingOverlay} pointerEvents="none">
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      )}

      {showControls && (
        <View style={styles.controlsOverlay} pointerEvents="box-none">
          {/* Top Bar: audio language + playback speed */}
          <View style={styles.topBar} pointerEvents="box-none">
            <View style={styles.topBarRight}>
              {audioTracks.length > 0 && (
                <TouchableOpacity
                  style={styles.pillButton}
                  onPress={() => setShowLanguageMenu(true)}
                  accessibilityRole="button"
                  accessibilityLabel="Audio language"
                >
                  <Icon name="volume-2" size={14} color={colors.text} />
                  <Text style={styles.pillButtonText}>
                    {activeAudioCode === 'en' ? 'English' : (activeTrack?.name || activeAudioCode)}
                  </Text>
                </TouchableOpacity>
              )}
              <TouchableOpacity
                style={styles.pillButton}
                onPress={() => setShowSpeedMenu(true)}
                accessibilityRole="button"
                accessibilityLabel="Playback speed"
              >
                <MaterialIcon name="gauge" size={14} color={colors.text} />
                <Text style={styles.pillButtonText}>{speed === 1 ? '1x' : `${speed}x`}</Text>
              </TouchableOpacity>
            </View>
          </View>

          {/* Center: seek back / play-pause / seek forward */}
          <View style={styles.centerControls} pointerEvents="box-none">
            <TouchableOpacity
              onPress={() => seekBy(-SEEK_STEP)}
              style={styles.seekButton}
              accessibilityRole="button"
              accessibilityLabel={`Rewind ${SEEK_STEP} seconds`}
            >
              <Icon name="chevron-left" size={26} color={colors.text} />
            </TouchableOpacity>
            <TouchableOpacity
              onPress={togglePlayPause}
              style={styles.playPauseButton}
              accessibilityRole="button"
              accessibilityLabel={isPlaying ? 'Pause' : 'Play'}
            >
              <Icon name={isPlaying ? 'pause' : 'play'} size={26} color={colors.text} />
            </TouchableOpacity>
            <TouchableOpacity
              onPress={() => seekBy(SEEK_STEP)}
              style={styles.seekButton}
              accessibilityRole="button"
              accessibilityLabel={`Forward ${SEEK_STEP} seconds`}
            >
              <Icon name="chevron-right" size={26} color={colors.text} />
            </TouchableOpacity>
          </View>

          {/* Bottom Bar: mute, time, scrubber, duration, fullscreen */}
          <View style={styles.bottomBar}>
            <TouchableOpacity
              onPress={toggleMute}
              style={styles.iconButtonSmall}
              accessibilityRole="button"
              accessibilityLabel={muted ? 'Unmute' : 'Mute'}
            >
              <Icon name={muted ? 'volume-x' : 'volume-2'} size={16} color={colors.text} />
            </TouchableOpacity>

            <Text style={styles.timeText}>{formatTime(currentTime)}</Text>

            <Slider
              style={styles.slider}
              minimumValue={0}
              maximumValue={duration > 0 ? duration : 1}
              value={currentTime}
              onSlidingComplete={handleSeek}
              minimumTrackTintColor={colors.accent}
              maximumTrackTintColor="rgba(255,255,255,0.25)"
              thumbTintColor={colors.accent}
            />

            <Text style={styles.timeText}>{formatTime(duration)}</Text>

            <TouchableOpacity
              onPress={enterFullscreen}
              style={styles.iconButtonSmall}
              accessibilityRole="button"
              accessibilityLabel="Enter fullscreen"
            >
              <Icon name="maximize" size={16} color={colors.text} />
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* Audio Track Selector Modal */}
      <Modal visible={showLanguageMenu} transparent animationType="fade" onRequestClose={() => setShowLanguageMenu(false)}>
        <TouchableOpacity style={styles.modalOverlay} activeOpacity={1} onPress={() => setShowLanguageMenu(false)}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>Audio Language</Text>

            <TouchableOpacity
              style={[styles.optionRow, activeAudioCode === 'en' && styles.optionRowActive]}
              onPress={() => selectAudioTrack('en')}
            >
              <Text style={[styles.optionText, activeAudioCode === 'en' && styles.optionTextActive]}>English (Original)</Text>
              {activeAudioCode === 'en' && <Icon name="check" size={16} color={colors.accent} />}
            </TouchableOpacity>

            {audioTracks.map((track) => (
              <TouchableOpacity
                key={track.code}
                style={[styles.optionRow, activeAudioCode === track.code && styles.optionRowActive]}
                onPress={() => selectAudioTrack(track.code)}
              >
                <Text style={[styles.optionText, activeAudioCode === track.code && styles.optionTextActive]}>{track.name}</Text>
                {activeAudioCode === track.code && <Icon name="check" size={16} color={colors.accent} />}
              </TouchableOpacity>
            ))}
          </View>
        </TouchableOpacity>
      </Modal>

      {/* Playback Speed Modal */}
      <Modal visible={showSpeedMenu} transparent animationType="fade" onRequestClose={() => setShowSpeedMenu(false)}>
        <TouchableOpacity style={styles.modalOverlay} activeOpacity={1} onPress={() => setShowSpeedMenu(false)}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>Playback Speed</Text>
            {SPEED_OPTIONS.map((rate) => (
              <TouchableOpacity
                key={rate}
                style={[styles.optionRow, speed === rate && styles.optionRowActive]}
                onPress={() => selectSpeed(rate)}
              >
                <Text style={[styles.optionText, speed === rate && styles.optionTextActive]}>{rate === 1 ? 'Normal (1x)' : `${rate}x`}</Text>
                {speed === rate && <Icon name="check" size={16} color={colors.accent} />}
              </TouchableOpacity>
            ))}
          </View>
        </TouchableOpacity>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: '100%',
    aspectRatio: 16 / 9,
    backgroundColor: '#000',
    position: 'relative',
  },
  videoTouchArea: {
    width: '100%',
    height: '100%',
  },
  video: {
    width: '100%',
    height: '100%',
  },
  loadingOverlay: {
    ...StyleSheet.absoluteFill,
    alignItems: 'center',
    justifyContent: 'center',
  },
  controlsOverlay: {
    ...StyleSheet.absoluteFill,
    justifyContent: 'space-between',
    backgroundColor: 'rgba(0,0,0,0.28)',
  },
  topBar: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    padding: spacing.sm,
  },
  topBarRight: { flexDirection: 'row', gap: spacing.sm },
  pillButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(0,0,0,0.55)',
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.pill,
    marginLeft: spacing.sm,
    gap: 6,
  },
  pillButtonText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: '600',
    marginLeft: 6,
  },
  centerControls: {
    flex: 1,
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    gap: spacing.xxl,
  },
  seekButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  playPauseButton: {
    backgroundColor: 'rgba(0,0,0,0.55)',
    width: 62,
    height: 62,
    borderRadius: 31,
    justifyContent: 'center',
    alignItems: 'center',
    marginHorizontal: spacing.xl,
  },
  bottomBar: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.sm,
    paddingBottom: spacing.sm,
  },
  iconButtonSmall: {
    width: 32,
    height: 32,
    alignItems: 'center',
    justifyContent: 'center',
  },
  slider: {
    flex: 1,
    height: 36,
    marginHorizontal: spacing.sm,
  },
  timeText: {
    color: colors.text,
    fontSize: 12,
    fontVariant: ['tabular-nums'],
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.7)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  modalContent: {
    backgroundColor: colors.card,
    width: '80%',
    borderRadius: radius.lg,
    padding: spacing.xl,
    borderWidth: 1,
    borderColor: colors.border,
  },
  modalTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: '700',
    marginBottom: spacing.lg,
    textAlign: 'center',
  },
  optionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.md,
    marginBottom: spacing.sm,
    backgroundColor: colors.cardAlt,
  },
  optionRowActive: {
    backgroundColor: colors.accentMuted,
    borderWidth: 1,
    borderColor: colors.accentBorder,
  },
  optionText: {
    color: colors.textSecondary,
    fontSize: 15,
  },
  optionTextActive: {
    color: colors.accent,
    fontWeight: '700',
  },
  watermarkContainer: {
    position: 'absolute',
    zIndex: 15,
  },
  watermarkBadge: {
    backgroundColor: 'rgba(0, 0, 0, 0.45)',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.15)',
  },
  watermarkText: {
    color: 'rgba(255, 255, 255, 0.78)',
    fontSize: 11,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
    letterSpacing: 1,
  },
  watermarkSubtext: {
    color: 'rgba(255, 255, 255, 0.48)',
    fontSize: 8,
    fontWeight: '500',
  },
  screenshotWarningBanner: {
    position: 'absolute',
    top: spacing.sm,
    left: spacing.sm,
    right: spacing.sm,
    zIndex: 30,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(15, 15, 15, 0.95)',
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: 'rgba(251, 146, 60, 0.45)',
    paddingVertical: 6,
    paddingHorizontal: spacing.sm,
    gap: spacing.sm,
  },
  screenshotWarningIconContainer: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: 'rgba(251, 146, 60, 0.16)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  screenshotWarningTextContainer: {
    flex: 1,
  },
  screenshotWarningTitle: {
    color: colors.warning,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  screenshotWarningBody: {
    color: colors.text,
    fontSize: 11,
    fontWeight: '500',
    lineHeight: 14,
  },
  screenshotWarningDismiss: {
    padding: spacing.xs,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
