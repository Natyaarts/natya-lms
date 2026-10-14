import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TextInput,
  TouchableOpacity,
  SafeAreaView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Keyboard,
  Alert,
  Linking,
} from 'react-native';
import * as DocumentPicker from 'expo-document-picker';
import client, { resolveMediaUrl } from '../api/client';
import Icon from '../components/Icon';
import { colors, spacing, radius, typography } from '../theme';

const MAX_CHAT_ATTACHMENT_SIZE_BYTES = 52428800; // Exact 50 MiB (52,428,800 bytes)
const ALLOWED_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'pdf', 'docx', 'txt'];

interface Message {
  id: number;
  conversation: number | string;
  sender: {
    id: number;
    username: string;
    first_name: string;
    last_name: string;
    role: string;
  };
  content: string;
  message_type: 'TEXT' | 'IMAGE' | 'FILE' | 'SYSTEM';
  attachment_name?: string;
  attachment_size?: number;
  attachment_mime_type?: string;
  has_attachment: boolean;
  attachment_url?: string | null;
  created_at: string;
}

export default function ConversationScreen({ route, navigation }: any) {
  const { conversationId, title } = route.params;

  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const [inputText, setInputText] = useState('');
  const [selectedFile, setSelectedFile] = useState<DocumentPicker.DocumentPickerAsset | null>(null);
  const [sending, setSending] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [currentUserId, setCurrentUserId] = useState<number | null>(null);

  const flatListRef = useRef<FlatList>(null);

  // Load current user ID for alignment
  useEffect(() => {
    client.get('users/me/').then((res) => {
      setCurrentUserId(res.data.id);
    }).catch(() => {});
  }, []);

  // Fetch initial messages & mark read
  const loadMessages = useCallback(async () => {
    try {
      const res = await client.get(`chat/conversations/${conversationId}/messages/?limit=30`);
      setMessages(res.data.results || []);
      setNextBefore(res.data.next_before);
      setHasMore(res.data.has_more);
      // Mark as read in backend using canonical endpoint
      client.post(`chat/conversations/${conversationId}/read/`).catch(() => {});
    } catch (err: any) {
      console.warn('Failed to load messages:', err);
    } finally {
      setLoading(false);
    }
  }, [conversationId]);

  useEffect(() => {
    loadMessages();
  }, [loadMessages]);

  // Periodic poll for incoming messages every 4 seconds
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await client.get(`chat/conversations/${conversationId}/messages/?limit=20`);
        const incoming: Message[] = res.data.results || [];
        setMessages((prev) => {
          if (incoming.length === 0) return prev;
          const prevIds = new Set(prev.map((m) => m.id));
          const newItems = incoming.filter((m) => !prevIds.has(m.id));
          if (newItems.length > 0) {
            client.post(`chat/conversations/${conversationId}/read/`).catch(() => {});
            return [...prev, ...newItems];
          }
          return prev;
        });
      } catch {
        // Silent poll error
      }
    }, 4000);
    return () => clearInterval(interval);
  }, [conversationId]);

  // Load older messages (Reverse cursor pagination)
  const handleLoadEarlier = async () => {
    if (!nextBefore || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await client.get(`chat/conversations/${conversationId}/messages/?before=${nextBefore}&limit=30`);
      setMessages((prev) => [...(res.data.results || []), ...prev]);
      setNextBefore(res.data.next_before);
      setHasMore(res.data.has_more);
    } catch (err: any) {
      console.warn('Failed to load earlier messages:', err);
    } finally {
      setLoadingMore(false);
    }
  };

  // Pick attachment with 50 MiB client validation
  const handlePickFile = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: '*/*',
        copyToCacheDirectory: true,
      });

      if (result.canceled || !result.assets?.[0]) return;
      const asset = result.assets[0];

      // File extension check
      const ext = asset.name.split('.').pop()?.toLowerCase() || '';
      if (!ALLOWED_EXTENSIONS.includes(ext)) {
        Alert.alert(
          'Unsupported File Type',
          `".${ext}" files are not supported. Allowed formats: ${ALLOWED_EXTENSIONS.join(', ')}.`
        );
        return;
      }

      // Exact 50 MiB limit check (52,428,800 bytes)
      if (typeof asset.size === 'number' && asset.size > MAX_CHAT_ATTACHMENT_SIZE_BYTES) {
        const sizeMb = (asset.size / (1024 * 1024)).toFixed(1);
        Alert.alert(
          'File Exceeds Limit',
          `This file is ${sizeMb} MB. Maximum allowed attachment size is 50 MB (52,428,800 bytes).`
        );
        return;
      }

      setSelectedFile(asset);
    } catch (err: any) {
      Alert.alert('Error', 'Could not open file picker.');
    }
  };

  const formatSize = (bytes?: number) => {
    if (!bytes) return '';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };

  // Send message with progress and timeout
  const handleSendMessage = async () => {
    const content = inputText.trim();
    if (!content && !selectedFile) return;

    Keyboard.dismiss();
    setSending(true);
    setUploadProgress(selectedFile ? 0 : null);

    try {
      const formData = new FormData();
      if (content) {
        formData.append('content', content);
      }
      if (selectedFile) {
        formData.append('attachment', {
          uri: selectedFile.uri,
          name: selectedFile.name,
          type: selectedFile.mimeType || 'application/octet-stream',
        } as any);
      }

      const res = await client.post(
        `chat/conversations/${conversationId}/messages/`,
        formData,
        {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 120000, // 120-second timeout for 50 MiB uploads
          onUploadProgress: (progressEvent) => {
            if (progressEvent.total) {
              const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
              setUploadProgress(percent);
            }
          },
        }
      );

      setInputText('');
      setSelectedFile(null);
      setUploadProgress(null);
      setMessages((prev) => [...prev, res.data]);
    } catch (err: any) {
      const errorMsg =
        err.response?.data?.attachment?.[0] ||
        err.response?.data?.error ||
        err.response?.data?.detail ||
        (err.code === 'ECONNABORTED' ? 'Upload timed out after 120 seconds.' : 'Could not send message.');
      Alert.alert('Failed to Send', errorMsg);
    } finally {
      setSending(false);
      setUploadProgress(null);
    }
  };

  const handleOpenAttachment = async (messageId: number, directUrl?: string | null) => {
    try {
      // 1. Authenticated request to obtain short-lived S3 presigned URL
      const res = await client.get(`chat/messages/${messageId}/attachment-url/`);
      const downloadUrl = res.data?.download_url;
      if (downloadUrl) {
        await Linking.openURL(downloadUrl);
        return;
      }
    } catch (err: any) {
      console.warn('Failed to obtain presigned attachment URL:', err);
      // Fallback if direct URL is usable
      if (directUrl) {
        const resolved = resolveMediaUrl(directUrl);
        if (resolved) {
          Linking.openURL(resolved).catch(() => {
            Alert.alert('Download Error', 'Could not open attachment URL.');
          });
          return;
        }
      }
      Alert.alert('Download Error', err.response?.data?.detail || 'Could not access attachment.');
    }
  };

  const renderMessageItem = ({ item }: { item: Message }) => {
    const isMe = item.sender.id === currentUserId;

    return (
      <View style={[styles.bubbleWrapper, isMe ? styles.bubbleWrapperRight : styles.bubbleWrapperLeft]}>
        {!isMe && (
          <Text style={styles.senderLabel}>
            {item.sender.first_name || item.sender.username}
          </Text>
        )}

        <View style={[styles.bubble, isMe ? styles.bubbleRight : styles.bubbleLeft]}>
          {item.content ? (
            <Text style={[styles.messageText, isMe ? styles.messageTextRight : styles.messageTextLeft]}>
              {item.content}
            </Text>
          ) : null}

          {/* Attachment Preview Card */}
          {item.has_attachment && (
            <View style={[styles.attachmentCard, isMe ? styles.attachmentCardRight : styles.attachmentCardLeft]}>
              <View style={styles.attachmentInfoRow}>
                <Icon
                  name={item.message_type === 'IMAGE' ? 'image' : 'file-text'}
                  size={18}
                  color={isMe ? colors.textInverse : colors.accent}
                />
                <View style={{ flex: 1 }}>
                  <Text
                    style={[styles.attachmentName, isMe ? styles.attachmentNameRight : styles.attachmentNameLeft]}
                    numberOfLines={1}
                  >
                    {item.attachment_name || 'Attachment'}
                  </Text>
                  {item.attachment_size && (
                    <Text style={[styles.attachmentMeta, isMe ? styles.attachmentMetaRight : styles.attachmentMetaLeft]}>
                      {formatSize(item.attachment_size)} (Max 50 MB)
                    </Text>
                  )}
                </View>
              </View>

              <TouchableOpacity
                style={[styles.downloadBtn, isMe ? styles.downloadBtnRight : styles.downloadBtnLeft]}
                onPress={() => handleOpenAttachment(item.id, item.attachment_url)}
                activeOpacity={0.8}
              >
                <Icon name="download" size={13} color={isMe ? colors.accent : colors.textInverse} />
                <Text style={[styles.downloadBtnText, isMe ? styles.downloadBtnTextRight : styles.downloadBtnTextLeft]}>
                  Download
                </Text>
              </TouchableOpacity>
            </View>
          )}

          <Text style={[styles.timeText, isMe ? styles.timeTextRight : styles.timeTextLeft]}>
            {new Date(item.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </Text>
        </View>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Icon name="arrow-left" size={22} color={colors.text} />
        </TouchableOpacity>
        <View style={styles.headerTitleContainer}>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {title || 'Conversation'}
          </Text>
          <View style={styles.statusRow}>
            <View style={styles.onlineDot} />
            <Text style={styles.statusText}>Active</Text>
          </View>
        </View>
      </View>

      {/* Main Messages & Input wrapped in KeyboardAvoidingView */}
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 90 : 0}
      >
        {loading ? (
          <View style={styles.centerContainer}>
            <ActivityIndicator size="large" color={colors.accent} />
          </View>
        ) : (
          <FlatList
            ref={flatListRef}
            data={messages}
            keyExtractor={(item) => String(item.id)}
            renderItem={renderMessageItem}
            contentContainerStyle={styles.listContent}
            keyboardDismissMode="on-drag"
            onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: false })}
            ListHeaderComponent={
              hasMore ? (
                <TouchableOpacity
                  style={styles.loadMoreButton}
                  onPress={handleLoadEarlier}
                  disabled={loadingMore}
                >
                  {loadingMore ? (
                    <ActivityIndicator size="small" color={colors.accent} />
                  ) : (
                    <Text style={styles.loadMoreText}>↑ Load earlier messages</Text>
                  )}
                </TouchableOpacity>
              ) : null
            }
          />
        )}

        {/* Selected File Chip */}
        {selectedFile && (
          <View style={styles.selectedFileContainer}>
            <View style={styles.selectedFileInfo}>
              <Icon name="file-text" size={16} color={colors.accent} />
              <Text style={styles.selectedFileName} numberOfLines={1}>
                {selectedFile.name}
              </Text>
              <Text style={styles.selectedFileSize}>
                ({formatSize(selectedFile.size || undefined)} / 50 MB)
              </Text>
            </View>
            <TouchableOpacity onPress={() => setSelectedFile(null)} disabled={sending}>
              <Icon name="x" size={16} color={colors.textTertiary} />
            </TouchableOpacity>
          </View>
        )}

        {/* Upload Progress Bar */}
        {uploadProgress !== null && (
          <View style={styles.progressBarContainer}>
            <View style={[styles.progressBarFill, { width: `${uploadProgress}%` }]} />
          </View>
        )}

        {/* Input Bar */}
        <View style={styles.inputBar}>
          <TouchableOpacity
            style={styles.attachButton}
            onPress={handlePickFile}
            disabled={sending}
            accessibilityLabel="Attach file (Max 50 MB)"
          >
            <Icon name="paperclip" size={20} color={colors.textSecondary} />
          </TouchableOpacity>

          <TextInput
            style={styles.textInput}
            placeholder="Type a message..."
            placeholderTextColor={colors.textTertiary}
            value={inputText}
            onChangeText={setInputText}
            multiline={true}
            editable={!sending}
          />

          <TouchableOpacity
            style={[
              styles.sendButton,
              (!inputText.trim() && !selectedFile) || sending ? styles.sendButtonDisabled : null,
            ]}
            onPress={handleSendMessage}
            disabled={(!inputText.trim() && !selectedFile) || sending}
            accessibilityLabel="Send message"
          >
            {sending ? (
              <ActivityIndicator size="small" color={colors.textInverse} />
            ) : (
              <Icon name="send" size={16} color={colors.textInverse} />
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    backgroundColor: colors.bgElevated,
    gap: spacing.md,
  },
  backButton: {
    padding: spacing.xs,
  },
  headerTitleContainer: {
    flex: 1,
  },
  headerTitle: {
    ...typography.section,
    color: colors.text,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: 2,
  },
  onlineDot: {
    width: 6,
    height: 6,
    borderRadius: radius.pill,
    backgroundColor: colors.success,
  },
  statusText: {
    ...typography.caption,
    color: colors.textTertiary,
    fontSize: 10,
  },
  centerContainer: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  listContent: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
  },
  loadMoreButton: {
    alignSelf: 'center',
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.lg,
    paddingVertical: 6,
    marginBottom: spacing.md,
  },
  loadMoreText: {
    fontSize: 11,
    fontWeight: '600',
    color: colors.accent,
  },
  bubbleWrapper: {
    marginBottom: spacing.md,
    maxWidth: '82%',
  },
  bubbleWrapperRight: {
    alignSelf: 'flex-end',
  },
  bubbleWrapperLeft: {
    alignSelf: 'flex-start',
  },
  senderLabel: {
    fontSize: 10,
    fontWeight: '600',
    color: colors.textTertiary,
    marginBottom: 2,
    marginLeft: 4,
  },
  bubble: {
    borderRadius: radius.lg,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
  },
  bubbleRight: {
    backgroundColor: colors.accent,
    borderTopRightRadius: 2,
  },
  bubbleLeft: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderTopLeftRadius: 2,
  },
  messageText: {
    fontSize: 14,
    lineHeight: 20,
  },
  messageTextRight: {
    color: colors.textInverse,
    fontWeight: '500',
  },
  messageTextLeft: {
    color: colors.text,
  },
  timeText: {
    fontSize: 9,
    marginTop: 4,
    textAlign: 'right',
  },
  timeTextRight: {
    color: 'rgba(0,0,0,0.5)',
  },
  timeTextLeft: {
    color: colors.textTertiary,
  },
  attachmentCard: {
    borderRadius: radius.md,
    padding: spacing.sm,
    marginTop: spacing.xs,
    marginBottom: spacing.xs,
  },
  attachmentCardRight: {
    backgroundColor: 'rgba(0,0,0,0.1)',
  },
  attachmentCardLeft: {
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
  },
  attachmentInfoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginBottom: 6,
  },
  attachmentName: {
    fontSize: 12,
    fontWeight: '600',
  },
  attachmentNameRight: {
    color: colors.textInverse,
  },
  attachmentNameLeft: {
    color: colors.text,
  },
  attachmentMeta: {
    fontSize: 10,
  },
  attachmentMetaRight: {
    color: 'rgba(0,0,0,0.6)',
  },
  attachmentMetaLeft: {
    color: colors.textTertiary,
  },
  downloadBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 5,
    borderRadius: radius.sm,
    gap: 4,
  },
  downloadBtnRight: {
    backgroundColor: colors.bg,
  },
  downloadBtnLeft: {
    backgroundColor: colors.accent,
  },
  downloadBtnText: {
    fontSize: 11,
    fontWeight: '700',
  },
  downloadBtnTextRight: {
    color: colors.accent,
  },
  downloadBtnTextLeft: {
    color: colors.textInverse,
  },
  selectedFileContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.bgElevated,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  selectedFileInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    gap: spacing.xs,
  },
  selectedFileName: {
    fontSize: 12,
    color: colors.text,
    maxWidth: '65%',
  },
  selectedFileSize: {
    fontSize: 10,
    color: colors.textTertiary,
  },
  progressBarContainer: {
    height: 3,
    backgroundColor: colors.border,
    width: '100%',
  },
  progressBarFill: {
    height: '100%',
    backgroundColor: colors.accent,
  },
  inputBar: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.bgElevated,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    gap: spacing.sm,
  },
  attachButton: {
    padding: spacing.sm,
    borderRadius: radius.md,
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: 2,
  },
  textInput: {
    flex: 1,
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    paddingHorizontal: spacing.md,
    paddingTop: 8,
    paddingBottom: 8,
    color: colors.text,
    fontSize: 14,
    borderWidth: 1,
    borderColor: colors.border,
    minHeight: 38,
  },
  sendButton: {
    width: 38,
    height: 38,
    borderRadius: radius.pill,
    backgroundColor: colors.accent,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 2,
  },
  sendButtonDisabled: {
    opacity: 0.35,
  },
});
