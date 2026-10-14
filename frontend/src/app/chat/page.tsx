"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  MessageSquare,
  Send,
  Paperclip,
  X,
  FileText,
  Download,
  AlertCircle,
  RefreshCw,
  Search,
  ChevronLeft,
  User,
  Shield,
  GraduationCap,
  BookOpen,
  Plus,
  CheckCircle2,
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import {
  Conversation,
  Message,
  UserMinimal,
  MAX_CHAT_ATTACHMENT_SIZE_BYTES,
  fetchConversations,
  fetchMessages,
  sendMessageWithProgress,
  markConversationRead,
  createDirectConversation,
  fetchDirectChatRecipients,
  validateChatAttachment,
  formatFileSize,
} from "@/lib/chatApi";

export default function ChatPage() {
  const { user, isAuthenticated, loading: authLoading } = useAuth();
  const searchParams = useSearchParams();
  const initialConvId = searchParams.get("conversation");

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingConv, setLoadingConv] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  // Message input state
  const [inputText, setInputText] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  // Search filter
  const [searchQuery, setSearchQuery] = useState("");

  // New Chat Modal state
  const [showNewChatModal, setShowNewChatModal] = useState(false);
  const [recipients, setRecipients] = useState<UserMinimal[]>([]);
  const [loadingRecipients, setLoadingRecipients] = useState(false);
  const [startingChat, setStartingChat] = useState<number | null>(null);

  // Mobile navigation state
  const [mobileShowThread, setMobileShowThread] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Load conversations list
  const loadConversations = useCallback(async (selectId?: number) => {
    try {
      const data = await fetchConversations();
      setConversations(data);
      if (selectId) {
        const found = data.find((c) => c.id === selectId);
        if (found) {
          setActiveConversation(found);
          setMobileShowThread(true);
        }
      }
    } catch (err) {
      console.error("Failed to load conversations:", err);
    } finally {
      setLoadingConv(false);
    }
  }, []);

  useEffect(() => {
    if (isAuthenticated) {
      loadConversations(initialConvId ? Number(initialConvId) : undefined);
    }
  }, [isAuthenticated, initialConvId, loadConversations]);

  // Load messages for the active conversation
  const loadActiveMessages = useCallback(async (conversationId: number) => {
    setLoadingMessages(true);
    try {
      const res = await fetchMessages(conversationId);
      setMessages(res.results);
      setNextBefore(res.next_before);
      setHasMore(res.has_more);
      // Mark as read in backend
      markConversationRead(conversationId);
      // Update unread count locally
      setConversations((prev) =>
        prev.map((c) => (c.id === conversationId ? { ...c, unread_count: 0 } : c))
      );
    } catch (err) {
      console.error("Failed to load messages:", err);
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  useEffect(() => {
    if (activeConversation) {
      loadActiveMessages(activeConversation.id);
    } else {
      setMessages([]);
    }
  }, [activeConversation, loadActiveMessages]);

  // Auto-scroll to bottom of messages
  useEffect(() => {
    if (!loadingMessages && !loadingMore) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, loadingMessages, loadingMore]);

  // Load earlier messages (Reverse cursor pagination)
  const handleLoadEarlier = async () => {
    if (!activeConversation || !nextBefore || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await fetchMessages(activeConversation.id, nextBefore);
      setMessages((prev) => [...res.results, ...prev]);
      setNextBefore(res.next_before);
      setHasMore(res.has_more);
    } catch (err) {
      console.error("Failed to load earlier messages:", err);
    } finally {
      setLoadingMore(false);
    }
  };

  // Background polling every 4 seconds to sync messages and unread counts
  useEffect(() => {
    if (!isAuthenticated) return;
    const interval = setInterval(async () => {
      // 1. Refresh conversations to update preview and badges
      try {
        const updatedConvs = await fetchConversations();
        setConversations(updatedConvs);

        // 2. If in an active conversation, fetch latest messages
        if (activeConversation) {
          const res = await fetchMessages(activeConversation.id);
          // Check if there are new messages
          setMessages((prev) => {
            if (res.results.length === 0) return prev;
            const prevIds = new Set(prev.map((m) => m.id));
            const newOnes = res.results.filter((m) => !prevIds.has(m.id));
            if (newOnes.length > 0) {
              markConversationRead(activeConversation.id);
              return [...prev, ...newOnes];
            }
            return prev;
          });
        }
      } catch {
        // Silent fail on background poll
      }
    }, 4000);
    return () => clearInterval(interval);
  }, [isAuthenticated, activeConversation]);

  // File selection and client-side validation
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setUploadError(null);
    const file = e.target.files?.[0];
    if (!file) return;

    const validation = validateChatAttachment(file);
    if (!validation.valid) {
      setUploadError(validation.error || "Invalid file");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    setSelectedFile(file);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeSelectedFile = () => {
    setSelectedFile(null);
    setUploadError(null);
    setUploadProgress(null);
  };

  // Send message
  const handleSendMessage = async () => {
    if (!activeConversation || sending) return;
    const content = inputText.trim();
    if (!content && !selectedFile) return;

    setSending(true);
    setUploadError(null);
    setUploadProgress(selectedFile ? 0 : null);

    try {
      const newMsg = await sendMessageWithProgress(
        activeConversation.id,
        content,
        selectedFile || undefined,
        (percent) => setUploadProgress(percent)
      );

      setMessages((prev) => [...prev, newMsg]);
      setInputText("");
      setSelectedFile(null);
      setUploadProgress(null);

      // Update conversation preview
      setConversations((prev) =>
        prev.map((c) =>
          c.id === activeConversation.id
            ? {
                ...c,
                last_message_at: newMsg.created_at,
                last_message_preview: newMsg.content || newMsg.attachment_name || "Attachment",
              }
            : c
        )
      );
    } catch (err: any) {
      setUploadError(err.message || "Failed to send message. Please try again.");
    } finally {
      setSending(false);
      setUploadProgress(null);
    }
  };

  // Keyboard send on Enter (Shift+Enter for newline)
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Start new conversation modal
  const openNewChatModal = async () => {
    setShowNewChatModal(true);
    setLoadingRecipients(true);
    try {
      const list = await fetchDirectChatRecipients();
      setRecipients(list);
    } catch (err) {
      console.error("Failed to load recipients:", err);
    } finally {
      setLoadingRecipients(false);
    }
  };

  const handleStartChatWithUser = async (recipientId: number) => {
    setStartingChat(recipientId);
    try {
      const conv = await createDirectConversation(recipientId);
      setShowNewChatModal(false);
      await loadConversations(conv.id);
      setActiveConversation(conv);
      setMobileShowThread(true);
    } catch (err: any) {
      alert(err.message || "Could not start conversation");
    } finally {
      setStartingChat(null);
    }
  };

  // Filter conversations
  const filteredConversations = conversations.filter((c) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    const otherName = c.other_participant
      ? `${c.other_participant.first_name} ${c.other_participant.last_name} ${c.other_participant.username}`.toLowerCase()
      : c.title.toLowerCase();
    return otherName.includes(q) || (c.last_message_preview && c.last_message_preview.toLowerCase().includes(q));
  });

  const getPartnerDisplay = (c: Conversation) => {
    if (c.other_participant) {
      const name =
        [c.other_participant.first_name, c.other_participant.last_name].filter(Boolean).join(" ") ||
        c.other_participant.username;
      const role = c.other_participant.role;
      return { name, role, initial: (name[0] || "U").toUpperCase() };
    }
    return { name: c.title || "Conversation", role: "GROUP", initial: "C" };
  };

  if (authLoading) {
    return (
      <div className="min-h-screen bg-[#050505] flex items-center justify-center">
        <div className="w-10 h-10 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen bg-[#050505] flex flex-col items-center justify-center p-6 text-center">
        <MessageSquare className="w-16 h-16 text-[#facc15] mb-4" />
        <h1 className="text-2xl font-bold text-white mb-2">Sign in to access Messages</h1>
        <p className="text-zinc-400 max-w-md mb-6">
          Connect directly with your mentors, course instructors, and academy staff.
        </p>
        <Link
          href="/login?next=/chat"
          className="px-6 py-3 bg-[#facc15] text-black font-bold rounded-full hover:bg-yellow-400 transition-colors"
        >
          Sign In
        </Link>
      </div>
    );
  }

  return (
    <div className="pt-20 min-h-screen bg-[#050505] text-zinc-100 flex flex-col">
      <div className="flex-1 max-w-7xl w-full mx-auto p-2 sm:p-4 md:p-6 flex flex-col">
        {/* Main 2-Column Chat Box */}
        <div className="flex-1 bg-[#0c0c0e] border border-white/10 rounded-2xl overflow-hidden shadow-2xl flex relative min-h-[600px] h-[calc(100vh-130px)]">
          {/* ================= LEFT COLUMN: CONVERSATION LIST ================= */}
          <div
            className={`w-full md:w-80 lg:w-96 border-r border-white/10 flex flex-col bg-[#09090b] ${
              mobileShowThread ? "hidden md:flex" : "flex"
            }`}
          >
            {/* Header & New Chat Button */}
            <div className="p-4 border-b border-white/10 flex items-center justify-between gap-3">
              <div>
                <h1 className="text-lg font-bold text-white flex items-center gap-2">
                  <MessageSquare className="w-5 h-5 text-[#facc15]" />
                  Messages
                </h1>
                <p className="text-xs text-zinc-400">Mentors & Course Discussions</p>
              </div>
              <button
                onClick={openNewChatModal}
                className="px-3 py-1.5 bg-[#facc15] hover:bg-yellow-400 text-black font-semibold text-xs rounded-full flex items-center gap-1.5 transition-all shadow-md active:scale-95"
                title="Start a new chat"
              >
                <Plus className="w-4 h-4" />
                <span>New</span>
              </button>
            </div>

            {/* Search filter */}
            <div className="p-3 border-b border-white/10 bg-black/30">
              <div className="relative">
                <Search className="w-4 h-4 text-zinc-400 absolute left-3 top-2.5" />
                <input
                  type="text"
                  placeholder="Search conversations..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full bg-zinc-900 border border-white/10 rounded-xl pl-9 pr-3 py-2 text-xs text-white placeholder-zinc-500 focus:outline-none focus:border-[#facc15]/50 transition-colors"
                />
                {searchQuery && (
                  <button
                    onClick={() => setSearchQuery("")}
                    className="absolute right-2.5 top-2.5 text-zinc-500 hover:text-white"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>

            {/* Conversation List Scroll Area */}
            <div className="flex-1 overflow-y-auto divide-y divide-white/5">
              {loadingConv ? (
                <div className="p-6 text-center text-zinc-500">
                  <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                  <p className="text-xs">Loading conversations...</p>
                </div>
              ) : filteredConversations.length === 0 ? (
                <div className="p-8 text-center text-zinc-500 flex flex-col items-center">
                  <MessageSquare className="w-10 h-10 text-zinc-700 mb-3" />
                  <p className="text-sm font-medium text-zinc-400 mb-1">No conversations yet</p>
                  <p className="text-xs text-zinc-500 mb-4 max-w-[200px]">
                    Start a direct conversation with your assigned mentor or instructor.
                  </p>
                  <button
                    onClick={openNewChatModal}
                    className="px-4 py-2 bg-white/5 hover:bg-white/10 text-[#facc15] text-xs font-semibold rounded-full border border-[#facc15]/30 transition-colors"
                  >
                    Start Discussion
                  </button>
                </div>
              ) : (
                filteredConversations.map((conv) => {
                  const partner = getPartnerDisplay(conv);
                  const isSelected = activeConversation?.id === conv.id;
                  return (
                    <button
                      key={conv.id}
                      onClick={() => {
                        setActiveConversation(conv);
                        setMobileShowThread(true);
                      }}
                      className={`w-full p-3.5 flex items-start gap-3 text-left transition-colors relative ${
                        isSelected
                          ? "bg-white/10 border-l-4 border-[#facc15]"
                          : "hover:bg-white/5"
                      }`}
                    >
                      {/* Avatar */}
                      <div className="w-11 h-11 rounded-full bg-zinc-800 border border-white/10 flex items-center justify-center font-bold text-sm text-[#facc15] shrink-0">
                        {partner.initial}
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-1 mb-1">
                          <p className="text-sm font-semibold text-white truncate">{partner.name}</p>
                          {conv.last_message_at && (
                            <span className="text-[10px] text-zinc-500 shrink-0">
                              {new Date(conv.last_message_at).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </span>
                          )}
                        </div>

                        <div className="flex items-center justify-between gap-2">
                          <p className="text-xs text-zinc-400 truncate">
                            {conv.last_message_preview || "No messages yet"}
                          </p>
                          {conv.unread_count > 0 && (
                            <span className="px-2 py-0.5 bg-[#facc15] text-black text-[10px] font-bold rounded-full shrink-0 animate-pulse">
                              {conv.unread_count}
                            </span>
                          )}
                        </div>

                        {/* Role tag */}
                        <div className="mt-1">
                          <span
                            className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${
                              partner.role === "MENTOR"
                                ? "bg-purple-950 text-purple-300 border border-purple-800/40"
                                : partner.role === "TEACHER"
                                ? "bg-blue-950 text-blue-300 border border-blue-800/40"
                                : partner.role === "STAFF"
                                ? "bg-amber-950 text-amber-300 border border-amber-800/40"
                                : "bg-zinc-800 text-zinc-400"
                            }`}
                          >
                            {partner.role}
                          </span>
                        </div>
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>

          {/* ================= RIGHT COLUMN: ACTIVE THREAD ================= */}
          <div
            className={`flex-1 flex flex-col bg-[#070709] ${
              mobileShowThread ? "flex" : "hidden md:flex"
            }`}
          >
            {activeConversation ? (
              <>
                {/* Active Thread Header */}
                <div className="p-3.5 sm:p-4 border-b border-white/10 bg-[#0c0c0e] flex items-center justify-between gap-3 shrink-0">
                  <div className="flex items-center gap-3 min-w-0">
                    {/* Mobile Back Button */}
                    <button
                      onClick={() => setMobileShowThread(false)}
                      className="md:hidden p-1.5 text-zinc-400 hover:text-white rounded-lg hover:bg-white/5"
                    >
                      <ChevronLeft className="w-5 h-5" />
                    </button>

                    <div className="w-10 h-10 rounded-full bg-zinc-800 border border-white/10 flex items-center justify-center font-bold text-sm text-[#facc15] shrink-0">
                      {getPartnerDisplay(activeConversation).initial}
                    </div>

                    <div className="min-w-0">
                      <h2 className="text-sm sm:text-base font-bold text-white truncate">
                        {getPartnerDisplay(activeConversation).name}
                      </h2>
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
                        <span className="text-xs text-zinc-400">
                          {getPartnerDisplay(activeConversation).role} • Active
                        </span>
                      </div>
                    </div>
                  </div>

                  <button
                    onClick={() => loadActiveMessages(activeConversation.id)}
                    className="p-2 text-zinc-400 hover:text-white rounded-xl hover:bg-white/5 transition-colors"
                    title="Refresh thread"
                  >
                    <RefreshCw className={`w-4 h-4 ${loadingMessages ? "animate-spin" : ""}`} />
                  </button>
                </div>

                {/* Messages Timeline */}
                <div className="flex-1 overflow-y-auto p-4 space-y-4">
                  {/* Load earlier messages banner */}
                  {hasMore && (
                    <div className="text-center pt-1 pb-3">
                      <button
                        onClick={handleLoadEarlier}
                        disabled={loadingMore}
                        className="px-4 py-1.5 bg-white/5 hover:bg-white/10 text-xs text-[#facc15] font-semibold rounded-full border border-white/10 transition-colors disabled:opacity-50"
                      >
                        {loadingMore ? "Loading earlier messages..." : "↑ Load earlier messages"}
                      </button>
                    </div>
                  )}

                  {loadingMessages ? (
                    <div className="flex items-center justify-center h-48 text-zinc-500">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin mr-2" />
                      <span className="text-xs">Loading messages...</span>
                    </div>
                  ) : messages.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-64 text-center text-zinc-500">
                      <MessageSquare className="w-12 h-12 text-zinc-800 mb-2" />
                      <p className="text-sm font-semibold text-zinc-400">Start the conversation</p>
                      <p className="text-xs text-zinc-500 max-w-sm mt-1">
                        Send a message or share an assignment/practice file up to 50 MB.
                      </p>
                    </div>
                  ) : (
                    messages.map((msg) => {
                      const isMe = msg.sender.id === user?.id;
                      return (
                        <div
                          key={msg.id}
                          className={`flex flex-col ${isMe ? "items-end" : "items-start"}`}
                        >
                          {/* Sender name for other users */}
                          {!isMe && (
                            <span className="text-[11px] font-semibold text-zinc-400 mb-1 ml-1">
                              {msg.sender.first_name || msg.sender.username}
                            </span>
                          )}

                          {/* Message Bubble */}
                          <div
                            className={`max-w-[85%] sm:max-w-[75%] rounded-2xl p-3.5 text-sm shadow-md ${
                              isMe
                                ? "bg-[#facc15] text-black rounded-tr-none"
                                : "bg-[#18181b] border border-white/10 text-zinc-100 rounded-tl-none"
                            }`}
                          >
                            {/* Text content */}
                            {msg.content && <p className="whitespace-pre-wrap break-words">{msg.content}</p>}

                            {/* Attachment renderer */}
                            {msg.has_attachment && (
                              <div
                                className={`mt-2.5 p-2.5 rounded-xl border flex flex-col gap-2 ${
                                  isMe
                                    ? "bg-black/10 border-black/20"
                                    : "bg-black/40 border-white/10"
                                Dark-card}`}
                              >
                                {msg.message_type === "IMAGE" ? (
                                  <div className="flex flex-col gap-1.5">
                                    <div className="flex items-center justify-between text-xs font-semibold">
                                      <span className="truncate">{msg.attachment_name}</span>
                                      <span className="text-[10px] opacity-75">
                                        {formatFileSize(msg.attachment_size)}
                                      </span>
                                    </div>
                                    <a
                                      href={msg.attachment_url || `/api/chat/messages/${msg.id}/attachment/`}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className={`inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg transition-colors ${
                                        isMe
                                          ? "bg-black text-[#facc15] hover:bg-zinc-900"
                                          : "bg-[#facc15] text-black hover:bg-yellow-400"
                                      }`}
                                    >
                                      <Download className="w-3.5 h-3.5" />
                                      View / Download Image
                                    </a>
                                  </div>
                                ) : (
                                  <div className="flex items-center justify-between gap-3">
                                    <div className="flex items-center gap-2 min-w-0">
                                      <FileText className="w-5 h-5 shrink-0 opacity-80" />
                                      <div className="min-w-0">
                                        <p className="text-xs font-semibold truncate">
                                          {msg.attachment_name || "Attachment"}
                                        </p>
                                        <p className="text-[10px] opacity-75">
                                          {formatFileSize(msg.attachment_size)} • Max 50 MB
                                        </p>
                                      </div>
                                    </div>
                                    <a
                                      href={msg.attachment_url || `/api/chat/messages/${msg.id}/attachment/`}
                                      download={msg.attachment_name}
                                      className={`p-2 rounded-lg transition-colors shrink-0 ${
                                        isMe
                                          ? "bg-black text-[#facc15] hover:bg-zinc-900"
                                          : "bg-[#facc15] text-black hover:bg-yellow-400"
                                      }`}
                                      title="Download attachment"
                                    >
                                      <Download className="w-4 h-4" />
                                    </a>
                                  </div>
                                )}
                              </div>
                            )}

                            {/* Timestamp */}
                            <div
                              className={`text-[10px] mt-1.5 text-right font-medium ${
                                isMe ? "text-black/60" : "text-zinc-500"
                              }`}
                            >
                              {new Date(msg.created_at).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </div>
                          </div>
                        </div>
                      );
                    })
                  )}
                  <div ref={messagesEndRef} />
                </div>

                {/* Selected attachment preview & progress */}
                {selectedFile && (
                  <div className="px-4 py-2 bg-zinc-900/90 border-t border-white/10 flex flex-col gap-1.5">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 min-w-0">
                        <FileText className="w-4 h-4 text-[#facc15] shrink-0" />
                        <span className="text-xs text-white font-medium truncate">
                          {selectedFile.name}
                        </span>
                        <span className="text-[10px] text-zinc-400 shrink-0">
                          ({formatFileSize(selectedFile.size)} / 50 MB)
                        </span>
                      </div>
                      <button
                        onClick={removeSelectedFile}
                        disabled={sending}
                        className="p-1 text-zinc-400 hover:text-white disabled:opacity-50"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>

                    {/* Upload progress bar */}
                    {uploadProgress !== null && (
                      <div className="w-full bg-zinc-800 rounded-full h-1.5 overflow-hidden">
                        <div
                          className="bg-[#facc15] h-1.5 rounded-full transition-all duration-150"
                          style={{ width: `${uploadProgress}%` }}
                        />
                      </div>
                    )}
                  </div>
                )}

                {/* Upload Error Banner */}
                {uploadError && (
                  <div className="px-4 py-2 bg-red-950/80 border-t border-red-800/50 flex items-center justify-between text-xs text-red-200">
                    <div className="flex items-center gap-2">
                      <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                      <span>{uploadError}</span>
                    </div>
                    <button
                      onClick={() => setUploadError(null)}
                      className="text-red-400 hover:text-white p-1"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}

                {/* Input Bar */}
                <div className="p-3 sm:p-4 border-t border-white/10 bg-[#0c0c0e] flex items-end gap-2 shrink-0">
                  {/* File attach button */}
                  <input
                    type="file"
                    ref={fileInputRef}
                    onChange={handleFileChange}
                    className="hidden"
                    accept=".jpg,.jpeg,.png,.webp,.pdf,.docx,.txt"
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={sending}
                    className="p-2.5 rounded-xl bg-white/5 border border-white/10 text-zinc-300 hover:text-white hover:bg-white/10 transition-colors shrink-0 disabled:opacity-50"
                    title="Attach file (Max 50 MB)"
                  >
                    <Paperclip className="w-5 h-5" />
                  </button>

                  {/* Textarea */}
                  <div className="flex-1 min-w-0 bg-zinc-900 border border-white/10 rounded-2xl focus-within:border-[#facc15]/50 transition-colors p-2">
                    <textarea
                      ref={textareaRef}
                      value={inputText}
                      onChange={(e) => setInputText(e.target.value)}
                      onKeyDown={handleKeyDown}
                      placeholder="Type a message (Shift+Enter for newline)..."
                      rows={1}
                      disabled={sending}
                      className="w-full bg-transparent text-sm text-white placeholder-zinc-500 focus:outline-none resize-none max-h-32 min-h-[24px]"
                    />
                  </div>

                  {/* Send Button */}
                  <button
                    type="button"
                    onClick={handleSendMessage}
                    disabled={sending || (!inputText.trim() && !selectedFile)}
                    className="p-2.5 rounded-xl bg-[#facc15] hover:bg-yellow-400 text-black font-bold transition-all shrink-0 disabled:opacity-30 disabled:cursor-not-allowed shadow-md"
                    title="Send message"
                  >
                    {sending ? (
                      <div className="w-5 h-5 border-2 border-black border-t-transparent rounded-full animate-spin" />
                    ) : (
                      <Send className="w-5 h-5" />
                    )}
                  </button>
                </div>
              </>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-zinc-500">
                <MessageSquare className="w-16 h-16 text-zinc-800 mb-4" />
                <h3 className="text-lg font-bold text-zinc-300 mb-1">Select a conversation</h3>
                <p className="text-xs text-zinc-500 max-w-sm mb-6">
                  Choose a thread from the left or start a new conversation with your mentor or teacher.
                </p>
                <button
                  onClick={openNewChatModal}
                  className="px-5 py-2.5 bg-[#facc15] text-black font-bold text-xs rounded-full hover:bg-yellow-400 transition-colors shadow-lg"
                >
                  Start New Conversation
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ================= NEW CONVERSATION MODAL ================= */}
      {showNewChatModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#121214] border border-white/10 rounded-2xl max-w-md w-full overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-150">
            <div className="p-4 border-b border-white/10 flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-white">Start Conversation</h3>
                <p className="text-xs text-zinc-400">Select an approved contact</p>
              </div>
              <button
                onClick={() => setShowNewChatModal(false)}
                className="p-1 text-zinc-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-4 max-h-80 overflow-y-auto space-y-2">
              {loadingRecipients ? (
                <div className="py-8 text-center text-zinc-500">
                  <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                  <p className="text-xs">Finding available mentors and teachers...</p>
                </div>
              ) : recipients.length === 0 ? (
                <div className="py-6 text-center text-zinc-400">
                  <GraduationCap className="w-10 h-10 text-zinc-600 mx-auto mb-2" />
                  <p className="text-sm font-semibold text-white mb-1">No contacts available</p>
                  <p className="text-xs text-zinc-500 max-w-xs mx-auto">
                    Direct messaging is available between students and their assigned mentors or instructors in enrolled courses.
                  </p>
                </div>
              ) : (
                recipients.map((r) => {
                  const name = [r.first_name, r.last_name].filter(Boolean).join(" ") || r.username;
                  return (
                    <div
                      key={r.id}
                      className="p-3 bg-zinc-900 border border-white/5 rounded-xl flex items-center justify-between gap-3 hover:border-white/20 transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="w-10 h-10 rounded-full bg-zinc-800 border border-white/10 flex items-center justify-center font-bold text-sm text-[#facc15] shrink-0">
                          {(name[0] || "U").toUpperCase()}
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-white truncate">{name}</p>
                          <span
                            className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${
                              r.role === "MENTOR"
                                ? "bg-purple-950 text-purple-300"
                                : "bg-blue-950 text-blue-300"
                            }`}
                          >
                            {r.role}
                          </span>
                        </div>
                      </div>

                      <button
                        onClick={() => handleStartChatWithUser(r.id)}
                        disabled={startingChat === r.id}
                        className="px-3.5 py-1.5 bg-[#facc15] hover:bg-yellow-400 text-black text-xs font-bold rounded-full transition-colors disabled:opacity-50 shrink-0"
                      >
                        {startingChat === r.id ? "Connecting..." : "Chat"}
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
