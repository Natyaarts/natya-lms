import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  SafeAreaView,
  ActivityIndicator,
  RefreshControl,
  TextInput,
  Modal,
  Alert,
} from 'react-native';
import client from '../api/client';
import Icon from '../components/Icon';
import { colors, spacing, radius, typography } from '../theme';

interface UserMinimal {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  role: 'STUDENT' | 'TEACHER' | 'MENTOR' | 'STAFF';
}

interface Conversation {
  id: number;
  conversation_type: 'DIRECT' | 'COURSE' | 'BATCH' | 'SUPPORT';
  title: string;
  last_message_at?: string | null;
  last_message_preview?: string;
  unread_count: number;
  other_participant?: UserMinimal | null;
}

export default function ChatListScreen({ navigation }: any) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  // New Chat Modal state
  const [showModal, setShowModal] = useState(false);
  const [recipients, setRecipients] = useState<UserMinimal[]>([]);
  const [loadingRecipients, setLoadingRecipients] = useState(false);
  const [connectingId, setConnectingId] = useState<number | null>(null);

  const fetchConversationsList = useCallback(async () => {
    try {
      const res = await client.get('chat/conversations/');
      setConversations(res.data || []);
    } catch (err: any) {
      console.warn('Failed to load conversations:', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchConversationsList();
    const unsubscribe = navigation.addListener('focus', () => {
      fetchConversationsList();
    });
    return unsubscribe;
  }, [navigation, fetchConversationsList]);

  const onRefresh = () => {
    setRefreshing(true);
    fetchConversationsList();
  };

  const openNewChat = async () => {
    setShowModal(true);
    setLoadingRecipients(true);
    try {
      // 1. Fetch current user data to find assigned mentor
      const userRes = await client.get('users/me/');
      const list: UserMinimal[] = [];
      const seen = new Set<number>();

      if (userRes.data?.mentor && !seen.has(userRes.data.mentor.id)) {
        list.push({
          id: userRes.data.mentor.id,
          username: userRes.data.mentor.username || 'Mentor',
          first_name: userRes.data.mentor.first_name || '',
          last_name: userRes.data.mentor.last_name || '',
          email: userRes.data.mentor.email || '',
          role: 'MENTOR',
        });
        seen.add(userRes.data.mentor.id);
      }

      // 2. Fetch instructors from enrolled courses
      try {
        const coursesRes = await client.get('courses/my-learning/');
        const coursesList = Array.isArray(coursesRes.data)
          ? coursesRes.data
          : coursesRes.data?.results || [];

        coursesList.forEach((c: any) => {
          if (c.instructors && Array.isArray(c.instructors)) {
            c.instructors.forEach((inst: any) => {
              const u = inst.user || inst;
              if (u && u.id && !seen.has(u.id)) {
                list.push({
                  id: u.id,
                  username: u.username || 'Instructor',
                  first_name: u.first_name || '',
                  last_name: u.last_name || '',
                  email: u.email || '',
                  role: 'TEACHER',
                });
                seen.add(u.id);
              }
            });
          }
        });
      } catch {
        // Enrolled courses check fallback
      }

      setRecipients(list);
    } catch (err: any) {
      console.warn('Failed to load chat recipients:', err);
    } finally {
      setLoadingRecipients(false);
    }
  };

  const handleStartConversation = async (recipientId: number, partnerName: string) => {
    setConnectingId(recipientId);
    try {
      const res = await client.post('chat/conversations/', {
        conversation_type: 'DIRECT',
        recipient_id: recipientId,
      });
      setShowModal(false);
      navigation.navigate('Conversation', {
        conversationId: res.data.id,
        title: partnerName,
      });
    } catch (err: any) {
      Alert.alert(
        'Unable to Start Chat',
        err.response?.data?.error || err.response?.data?.detail || 'You do not have permission to message this user.'
      );
    } finally {
      setConnectingId(null);
    }
  };

  const filteredConversations = conversations.filter((c) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    const otherName = c.other_participant
      ? `${c.other_participant.first_name} ${c.other_participant.last_name} ${c.other_participant.username}`.toLowerCase()
      : c.title.toLowerCase();
    return otherName.includes(q) || (c.last_message_preview && c.last_message_preview.toLowerCase().includes(q));
  });

  const totalUnread = conversations.reduce((acc, c) => acc + (c.unread_count || 0), 0);

  const getPartnerName = (c: Conversation) => {
    if (c.other_participant) {
      return (
        [c.other_participant.first_name, c.other_participant.last_name].filter(Boolean).join(' ') ||
        c.other_participant.username
      );
    }
    return c.title || 'Discussion';
  };

  const renderConversationItem = ({ item }: { item: Conversation }) => {
    const partnerName = getPartnerName(item);
    const initial = (partnerName[0] || 'U').toUpperCase();
    const role = item.other_participant?.role || 'CHAT';

    return (
      <TouchableOpacity
        style={styles.chatRow}
        activeOpacity={0.7}
        onPress={() =>
          navigation.navigate('Conversation', {
            conversationId: item.id,
            title: partnerName,
            otherUser: item.other_participant,
          })
        }
      >
        {/* Avatar */}
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{initial}</Text>
        </View>

        {/* Info */}
        <View style={styles.chatInfo}>
          <View style={styles.chatHeaderRow}>
            <Text style={styles.chatName} numberOfLines={1}>
              {partnerName}
            </Text>
            {item.last_message_at && (
              <Text style={styles.timestamp}>
                {new Date(item.last_message_at).toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </Text>
            )}
          </View>

          <View style={styles.chatSubRow}>
            <Text style={styles.previewText} numberOfLines={1}>
              {item.last_message_preview || 'No messages yet'}
            </Text>
            {item.unread_count > 0 && (
              <View style={styles.unreadBadge}>
                <Text style={styles.unreadBadgeText}>{item.unread_count}</Text>
              </View>
            )}
          </View>

          {/* Role badge */}
          <View style={styles.roleTag}>
            <Text style={styles.roleTagText}>{role}</Text>
          </View>
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <SafeAreaView style={styles.container}>
      {/* Top Header */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <TouchableOpacity
            style={styles.backButton}
            onPress={() => navigation.goBack()}
            accessibilityLabel="Go back"
          >
            <Icon name="arrow-left" size={22} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Messages</Text>
          {totalUnread > 0 && (
            <View style={styles.headerUnreadPill}>
              <Text style={styles.headerUnreadText}>{totalUnread}</Text>
            </View>
          )}
        </View>

        <TouchableOpacity
          style={styles.newChatButton}
          onPress={openNewChat}
          activeOpacity={0.8}
        >
          <Icon name="plus" size={16} color={colors.textInverse} />
          <Text style={styles.newChatText}>New</Text>
        </TouchableOpacity>
      </View>

      {/* Search Bar */}
      <View style={styles.searchContainer}>
        <View style={styles.searchBox}>
          <Icon name="search" size={16} color={colors.textTertiary} />
          <TextInput
            style={styles.searchInput}
            placeholder="Search conversations..."
            placeholderTextColor={colors.textTertiary}
            value={searchQuery}
            onChangeText={setSearchQuery}
            autoCorrect={false}
          />
          {searchQuery.length > 0 && (
            <TouchableOpacity onPress={() => setSearchQuery('')}>
              <Icon name="x" size={16} color={colors.textTertiary} />
            </TouchableOpacity>
          )}
        </View>
      </View>

      {/* Conversations List */}
      {loading ? (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="large" color={colors.accent} />
          <Text style={styles.loadingText}>Loading conversations...</Text>
        </View>
      ) : filteredConversations.length === 0 ? (
        <View style={styles.emptyContainer}>
          <Icon name="message-square" size={48} color={colors.borderStrong} />
          <Text style={styles.emptyTitle}>No conversations yet</Text>
          <Text style={styles.emptySubtitle}>
            Connect with your assigned mentor or course instructors.
          </Text>
          <TouchableOpacity style={styles.emptyButton} onPress={openNewChat}>
            <Text style={styles.emptyButtonText}>Start a Discussion</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <FlatList
          data={filteredConversations}
          keyExtractor={(item) => String(item.id)}
          renderItem={renderConversationItem}
          contentContainerStyle={styles.listContent}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={colors.accent}
              colors={[colors.accent]}
            />
          }
        />
      )}

      {/* New Conversation Modal */}
      <Modal
        visible={showModal}
        animationType="slide"
        transparent={true}
        onRequestClose={() => setShowModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <View style={styles.modalHeader}>
              <View>
                <Text style={styles.modalTitle}>New Conversation</Text>
                <Text style={styles.modalSubtitle}>Select an approved contact</Text>
              </View>
              <TouchableOpacity onPress={() => setShowModal(false)} style={styles.closeButton}>
                <Icon name="x" size={20} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>

            {loadingRecipients ? (
              <View style={styles.modalLoading}>
                <ActivityIndicator size="small" color={colors.accent} />
                <Text style={styles.modalLoadingText}>Finding contacts...</Text>
              </View>
            ) : recipients.length === 0 ? (
              <View style={styles.modalEmpty}>
                <Icon name="users" size={36} color={colors.textTertiary} />
                <Text style={styles.modalEmptyTitle}>No direct contacts found</Text>
                <Text style={styles.modalEmptySub}>
                  Direct messaging is enabled with your assigned mentor and enrolled course teachers.
                </Text>
              </View>
            ) : (
              <FlatList
                data={recipients}
                keyExtractor={(item) => String(item.id)}
                contentContainerStyle={{ padding: spacing.md }}
                renderItem={({ item }) => {
                  const name = [item.first_name, item.last_name].filter(Boolean).join(' ') || item.username;
                  return (
                    <TouchableOpacity
                      style={styles.recipientRow}
                      activeOpacity={0.7}
                      disabled={connectingId === item.id}
                      onPress={() => handleStartConversation(item.id, name)}
                    >
                      <View style={styles.avatarSmall}>
                        <Text style={styles.avatarTextSmall}>{(name[0] || 'U').toUpperCase()}</Text>
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.recipientName}>{name}</Text>
                        <Text style={styles.recipientRole}>{item.role}</Text>
                      </View>
                      {connectingId === item.id ? (
                        <ActivityIndicator size="small" color={colors.accent} />
                      ) : (
                        <View style={styles.connectPill}>
                          <Text style={styles.connectPillText}>Chat</Text>
                        </View>
                      )}
                    </TouchableOpacity>
                  );
                }}
              />
            )}
          </View>
        </View>
      </Modal>
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
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  backButton: {
    padding: spacing.xs,
    marginRight: spacing.xs,
  },
  headerTitle: {
    ...typography.title,
    color: colors.text,
  },
  headerUnreadPill: {
    backgroundColor: colors.accent,
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: radius.pill,
  },
  headerUnreadText: {
    fontSize: 10,
    fontWeight: '700',
    color: colors.textInverse,
  },
  newChatButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.accent,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.pill,
    gap: 4,
  },
  newChatText: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.textInverse,
  },
  searchContainer: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    backgroundColor: colors.bgElevated,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  searchBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.card,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    height: 38,
    gap: spacing.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  searchInput: {
    flex: 1,
    color: colors.text,
    fontSize: 13,
  },
  listContent: {
    paddingVertical: spacing.xs,
  },
  chatRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.divider,
    gap: spacing.md,
  },
  avatar: {
    width: 46,
    height: 46,
    borderRadius: radius.pill,
    backgroundColor: colors.cardAlt,
    borderWidth: 1.5,
    borderColor: colors.accentBorder,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarText: {
    color: colors.accent,
    fontSize: 16,
    fontWeight: '700',
  },
  chatInfo: {
    flex: 1,
  },
  chatHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 2,
  },
  chatName: {
    ...typography.section,
    color: colors.text,
    flex: 1,
  },
  timestamp: {
    ...typography.caption,
    color: colors.textTertiary,
    marginLeft: spacing.sm,
  },
  chatSubRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  previewText: {
    ...typography.bodyRegular,
    fontSize: 13,
    color: colors.textSecondary,
    flex: 1,
  },
  unreadBadge: {
    backgroundColor: colors.accent,
    borderRadius: radius.pill,
    minWidth: 18,
    height: 18,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 4,
    marginLeft: spacing.sm,
  },
  unreadBadgeText: {
    color: colors.textInverse,
    fontSize: 10,
    fontWeight: '800',
  },
  roleTag: {
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(255,255,255,0.06)',
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radius.sm,
    marginTop: 4,
  },
  roleTagText: {
    fontSize: 9,
    fontWeight: '700',
    color: colors.textTertiary,
    letterSpacing: 0.5,
  },
  centerContainer: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xl,
  },
  loadingText: {
    ...typography.bodyRegular,
    color: colors.textSecondary,
    marginTop: spacing.md,
  },
  emptyContainer: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xl,
  },
  emptyTitle: {
    ...typography.title,
    color: colors.text,
    marginTop: spacing.md,
  },
  emptySubtitle: {
    ...typography.bodyRegular,
    color: colors.textSecondary,
    textAlign: 'center',
    marginTop: spacing.xs,
    marginBottom: spacing.xl,
  },
  emptyButton: {
    backgroundColor: colors.accentMuted,
    borderWidth: 1,
    borderColor: colors.accentBorder,
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.sm + 2,
    borderRadius: radius.pill,
  },
  emptyButtonText: {
    color: colors.accent,
    fontWeight: '600',
    fontSize: 13,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: colors.scrim,
    justifyContent: 'flex-end',
  },
  modalCard: {
    backgroundColor: colors.card,
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    maxHeight: '70%',
    paddingBottom: spacing.xxl,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: spacing.lg,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  modalTitle: {
    ...typography.title,
    color: colors.text,
  },
  modalSubtitle: {
    ...typography.caption,
    color: colors.textSecondary,
    marginTop: 2,
  },
  closeButton: {
    padding: spacing.xs,
  },
  modalLoading: {
    padding: spacing.xxl,
    alignItems: 'center',
    gap: spacing.sm,
  },
  modalLoadingText: {
    ...typography.bodyRegular,
    color: colors.textSecondary,
  },
  modalEmpty: {
    padding: spacing.xxl,
    alignItems: 'center',
  },
  modalEmptyTitle: {
    ...typography.section,
    color: colors.text,
    marginTop: spacing.md,
  },
  modalEmptySub: {
    ...typography.bodyRegular,
    color: colors.textSecondary,
    textAlign: 'center',
    marginTop: spacing.xs,
  },
  recipientRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.bgElevated,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
    gap: spacing.md,
  },
  avatarSmall: {
    width: 36,
    height: 36,
    borderRadius: radius.pill,
    backgroundColor: colors.cardAlt,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarTextSmall: {
    color: colors.accent,
    fontWeight: '700',
    fontSize: 13,
  },
  recipientName: {
    ...typography.section,
    color: colors.text,
  },
  recipientRole: {
    fontSize: 10,
    fontWeight: '600',
    color: colors.textSecondary,
    marginTop: 1,
  },
  connectPill: {
    backgroundColor: colors.accent,
    paddingHorizontal: spacing.md,
    paddingVertical: 5,
    borderRadius: radius.pill,
  },
  connectPillText: {
    color: colors.textInverse,
    fontSize: 11,
    fontWeight: '700',
  },
});
