import React, { useEffect, useRef, useState } from 'react';
import { View, StyleSheet, TouchableOpacity, Text, Modal, ActivityIndicator } from 'react-native';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useEventListener } from 'expo';
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
}

// How far the alternate audio track is allowed to drift from the video
// before we force a re-sync (seconds). Small drift is normal and re-syncing
// too aggressively causes audible stutter.
const SYNC_DRIFT_TOLERANCE = 0.3;

const SEEK_STEP = 10;
const SPEED_OPTIONS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

export default function CustomVideoPlayer({ source, audioTracks = [], lessonKey, initialPosition = 0, onProgress }: CustomVideoPlayerProps) {
  const [showControls, setShowControls] = useState(true);
  const [showLanguageMenu, setShowLanguageMenu] = useState(false);
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [duration, setDuration] = useState(0);
  const [status, setStatus] = useState<'idle' | 'loading' | 'readyToPlay' | 'error'>('idle');
  const [speed, setSpeed] = useState(1);
  const [muted, setMuted] = useState(false);

  // 'en' = original video audio. Anything else must match an audioTracks[].code.
  const [activeAudioCode, setActiveAudioCode] = useState<string>('en');

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
    if (payload.status === 'readyToPlay' && initialPosition > 0) {
      trySeekInitial(initialPosition, duration || player.duration);
    }
  });

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
});
