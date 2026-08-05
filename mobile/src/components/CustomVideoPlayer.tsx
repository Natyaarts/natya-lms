import React, { useState, useEffect } from 'react';
import { View, StyleSheet, TouchableOpacity, Text, Modal, SafeAreaView, ActivityIndicator } from 'react-native';
import { useVideoPlayer, VideoView, VideoSource, AudioTrack } from 'expo-video';
import { useEventListener } from 'expo';
import Slider from '@react-native-community/slider';

// A helper to format seconds to mm:ss
const formatTime = (seconds: number) => {
  if (isNaN(seconds)) return '00:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
};

interface CustomVideoPlayerProps {
  source: string;
}

export default function CustomVideoPlayer({ source }: CustomVideoPlayerProps) {
  const [showControls, setShowControls] = useState(true);
  const [showSettings, setShowSettings] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [duration, setDuration] = useState(0);
  const [audioTracks, setAudioTracks] = useState<AudioTrack[]>([]);
  const [currentAudioTrack, setCurrentAudioTrack] = useState<AudioTrack | null>(null);

  const player = useVideoPlayer(source, player => {
    player.loop = false;
    player.play();
  });

  // Track time updates
  useEventListener(player, 'timeUpdate', (payload) => {
    setCurrentTime(payload.currentTime);
  });

  // Track playing state
  useEventListener(player, 'playingChange', (payload) => {
    setIsPlaying(payload.isPlaying);
  });

  // Track duration when source loads
  useEventListener(player, 'sourceLoad', (payload) => {
    setDuration(payload.duration);
    setAudioTracks(payload.availableAudioTracks || []);
  });

  // Track changes in available audio tracks dynamically
  useEventListener(player, 'availableAudioTracksChange', (payload) => {
    setAudioTracks(payload.availableAudioTracks || []);
  });

  // Track changes in the current audio track
  useEventListener(player, 'audioTrackChange', (payload) => {
    setCurrentAudioTrack(payload.audioTrack || null);
  });

  // Hide controls after 3 seconds of inactivity
  useEffect(() => {
    let timeout: NodeJS.Timeout;
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
    // Keep controls visible
    setShowControls(true);
  };

  const handleAudioTrackSelect = (track: AudioTrack) => {
    player.audioTrack = track;
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
          allowsFullscreen={false} // Custom full screen logic would go here if needed
        />
      </TouchableOpacity>

      {/* Custom Controls Overlay */}
      {showControls && (
        <View style={styles.controlsOverlay} pointerEvents="box-none">
          
          {/* Top Bar: Settings Icon */}
          <View style={styles.topBar} pointerEvents="box-none">
            {audioTracks.length > 1 && (
              <TouchableOpacity 
                style={styles.settingsButton}
                onPress={() => setShowSettings(true)}
              >
                <Text style={styles.settingsIcon}>⚙️ Audio</Text>
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
            
            {audioTracks.map((track, idx) => (
              <TouchableOpacity
                key={track.id || idx.toString()}
                style={[
                  styles.trackItem, 
                  currentAudioTrack?.id === track.id && styles.trackItemActive
                ]}
                onPress={() => handleAudioTrackSelect(track)}
              >
                <Text style={[
                  styles.trackText,
                  currentAudioTrack?.id === track.id && styles.trackTextActive
                ]}>
                  {track.label || track.language || `Track ${idx + 1}`}
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
    ...StyleSheet.absoluteFillObject,
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
