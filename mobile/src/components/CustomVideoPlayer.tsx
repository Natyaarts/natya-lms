import React, { useEffect, useState } from 'react';
import { View, StyleSheet, TouchableOpacity, Text, Modal } from 'react-native';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useEventListener } from 'expo';
import Slider from '@react-native-community/slider';

// A helper to format seconds to mm:ss
const formatTime = (seconds: number) => {
  if (isNaN(seconds)) return '00:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
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
}

// How far the alternate audio track is allowed to drift from the video
// before we force a re-sync (seconds). Small drift is normal and re-syncing
// too aggressively causes audible stutter.
const SYNC_DRIFT_TOLERANCE = 0.3;

export default function CustomVideoPlayer({ source, audioTracks = [], lessonKey }: CustomVideoPlayerProps) {
  const [showControls, setShowControls] = useState(true);
  const [showSettings, setShowSettings] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [duration, setDuration] = useState(0);

  // 'en' = original video audio. Anything else must match an audioTracks[].code.
  const [activeAudioCode, setActiveAudioCode] = useState<string>('en');

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

  // Keep the original video's own audio muted while a translated track is active.
  useEffect(() => {
    player.muted = activeAudioCode !== 'en';
  }, [activeAudioCode, player]);

  // Reset to English and stop any alternate audio whenever the lesson changes,
  // so audio from the previous lesson never keeps playing into the new one.
  useEffect(() => {
    setActiveAudioCode('en');
    setShowSettings(false);
  }, [lessonKey]);

  // Track time updates
  useEventListener(player, 'timeUpdate', (payload) => {
    setCurrentTime(payload.currentTime);
    // Periodically correct alternate-audio drift (e.g. after buffering stalls).
    if (activeAudioCode !== 'en' && Math.abs(altPlayer.currentTime - payload.currentTime) > SYNC_DRIFT_TOLERANCE) {
      altPlayer.currentTime = payload.currentTime;
    }
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
    // Keep controls visible for a bit after clicking
    setShowControls(true);
  };

  const handleSeek = (value: number) => {
    player.currentTime = value;
    if (activeAudioCode !== 'en') {
      altPlayer.currentTime = value;
    }
    // Keep controls visible
    setShowControls(true);
  };

  const selectAudioTrack = (code: string) => {
    setActiveAudioCode(code);
    setShowSettings(false);
  };

  return (
    <View style={styles.container}>
      {/*
        We use nativeControls={false} because we are building a completely custom UI
        overlaid on top of the VideoView.
      */}
      <TouchableOpacity
        style={styles.videoTouchArea}
        activeOpacity={1}
        onPress={() => setShowControls(prev => !prev)}
      >
        <VideoView
          player={player}
          style={styles.video}
          nativeControls={false}
        />
      </TouchableOpacity>

      {/* Custom Controls Overlay */}
      {showControls && (
        <View style={styles.controlsOverlay} pointerEvents="box-none">

          {/* Top Bar: Audio Track Selector */}
          <View style={styles.topBar} pointerEvents="box-none">
            {audioTracks.length > 0 && (
              <TouchableOpacity
                style={styles.settingsButton}
                onPress={() => setShowSettings(true)}
              >
                <Text style={styles.settingsIcon}>
                  🎧 {activeAudioCode === 'en' ? 'English' : (activeTrack?.name || activeAudioCode)}
                </Text>
              </TouchableOpacity>
            )}
          </View>

          {/* Center: Play/Pause */}
          <View style={styles.centerControls} pointerEvents="box-none">
            <TouchableOpacity onPress={togglePlayPause} style={styles.playPauseButton}>
              <Text style={styles.playPauseIcon}>{isPlaying ? '⏸' : '▶️'}</Text>
            </TouchableOpacity>
          </View>

          {/* Bottom Bar: Scrubber and Time */}
          <View style={styles.bottomBar}>
            <Text style={styles.timeText}>{formatTime(currentTime)}</Text>

            <Slider
              style={styles.slider}
              minimumValue={0}
              maximumValue={duration > 0 ? duration : 1}
              value={currentTime}
              onSlidingComplete={handleSeek}
              minimumTrackTintColor="#facc15"
              maximumTrackTintColor="#555"
              thumbTintColor="#facc15"
            />

            <Text style={styles.timeText}>{formatTime(duration)}</Text>
          </View>
        </View>
      )}

      {/* Audio Track Selector Modal */}
      <Modal
        visible={showSettings}
        transparent={true}
        animationType="fade"
        onRequestClose={() => setShowSettings(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>Audio Languages</Text>

            <TouchableOpacity
              style={[styles.trackItem, activeAudioCode === 'en' && styles.trackItemActive]}
              onPress={() => selectAudioTrack('en')}
            >
              <Text style={[styles.trackText, activeAudioCode === 'en' && styles.trackTextActive]}>
                English (Original)
              </Text>
            </TouchableOpacity>

            {audioTracks.map((track) => (
              <TouchableOpacity
                key={track.code}
                style={[
                  styles.trackItem,
                  activeAudioCode === track.code && styles.trackItemActive
                ]}
                onPress={() => selectAudioTrack(track.code)}
              >
                <Text style={[
                  styles.trackText,
                  activeAudioCode === track.code && styles.trackTextActive
                ]}>
                  {track.name}
                </Text>
              </TouchableOpacity>
            ))}

            <TouchableOpacity
              style={styles.closeModalButton}
              onPress={() => setShowSettings(false)}
            >
              <Text style={styles.closeModalText}>Close</Text>
            </TouchableOpacity>
          </View>
        </View>
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
  controlsOverlay: {
    ...StyleSheet.absoluteFill,
    justifyContent: 'space-between',
    backgroundColor: 'rgba(0,0,0,0.3)', // Slight dim when controls are active
  },
  topBar: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    padding: 10,
  },
  settingsButton: {
    backgroundColor: 'rgba(0,0,0,0.6)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
  },
  settingsIcon: {
    color: '#fff',
    fontSize: 14,
    fontWeight: 'bold',
  },
  centerControls: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  playPauseButton: {
    backgroundColor: 'rgba(0,0,0,0.6)',
    width: 60,
    height: 60,
    borderRadius: 30,
    justifyContent: 'center',
    alignItems: 'center',
  },
  playPauseIcon: {
    color: '#fff',
    fontSize: 24,
    marginLeft: 4, // slight offset for play icon
  },
  bottomBar: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingBottom: 10,
  },
  slider: {
    flex: 1,
    height: 40,
    marginHorizontal: 10,
  },
  timeText: {
    color: '#fff',
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
    backgroundColor: '#18181b',
    width: '80%',
    borderRadius: 12,
    padding: 20,
  },
  modalTitle: {
    color: '#fff',
    fontSize: 18,
    fontWeight: 'bold',
    marginBottom: 16,
    textAlign: 'center',
  },
  trackItem: {
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 8,
    marginBottom: 8,
    backgroundColor: '#27272a',
  },
  trackItemActive: {
    backgroundColor: 'rgba(250, 204, 21, 0.1)',
    borderWidth: 1,
    borderColor: '#facc15',
  },
  trackText: {
    color: '#a1a1aa',
    fontSize: 16,
    textAlign: 'center',
  },
  trackTextActive: {
    color: '#facc15',
    fontWeight: 'bold',
  },
  closeModalButton: {
    marginTop: 12,
    paddingVertical: 12,
  },
  closeModalText: {
    color: '#facc15',
    fontSize: 16,
    fontWeight: 'bold',
    textAlign: 'center',
  },
});
