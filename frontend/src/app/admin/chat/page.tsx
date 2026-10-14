"use client";

import React, { useEffect, useState, useCallback } from "react";
import {
  MessageSquare,
  Search,
  Filter,
  RefreshCw,
  User,
  Users,
  Shield,
  GraduationCap,
  Sparkles,
  Calendar,
  Clock,
  Paperclip,
  Download,
  AlertTriangle,
  RotateCcw,
  CheckCircle,
  X,
  ChevronLeft,
  ChevronRight,
  FileText,
  Image as ImageIcon,
  ExternalLink,
  ShieldAlert,
  ChevronDown,
} from "lucide-react";
import {
  AdminConversation,
  AdminMessage,
  AdminConversationFilters,
  fetchAdminConversations,
  fetchAdminConversationDetail,
  fetchAdminMessages,
  moderateAdminMessage,
  getAdminAttachmentPresignedUrl,
  formatFileSize,
} from "@/lib/adminChatApi";

export default function AdminChatPage() {
  // State for conversation list
  const [conversations, setConversations] = useState<AdminConversation[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(20);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  // Filters state
  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [courseIdFilter, setCourseIdFilter] = useState("");
  const [batchIdFilter, setBatchIdFilter] = useState("");
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false);

  // State for active conversation detail & messages
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [selectedConversation, setSelectedConversation] = useState<AdminConversation | null>(null);
  const [messages, setMessages] = useState<AdminMessage[]>([]);
  const [messagesCount, setMessagesCount] = useState(0);
  const [messagesPage, setMessagesPage] = useState(1);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);

  // Moderation modal state
  const [moderatingMessage, setModeratingMessage] = useState<AdminMessage | null>(null);
  const [moderationAction, setModerationAction] = useState<"soft_delete" | "restore">("soft_delete");
  const [moderationReason, setModerationReason] = useState("");
  const [submittingModeration, setSubmittingModeration] = useState(false);
  const [moderationError, setModerationError] = useState<string | null>(null);
  const [moderationSuccessToast, setModerationSuccessToast] = useState<string | null>(null);

  // Attachment preview downloading state
  const [downloadingAttachmentId, setDownloadingAttachmentId] = useState<number | null>(null);

  // Mobile detail view toggle
  const [mobileShowDetail, setMobileShowDetail] = useState(false);

  // Load conversations
  const loadConversations = useCallback(
    async (pageToLoad = 1) => {
      setLoadingList(true);
      setListError(null);
      try {
        const filters: AdminConversationFilters = {
          page: pageToLoad,
          page_size: pageSize,
          search: searchQuery.trim() || undefined,
          type: typeFilter,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          course_id: courseIdFilter.trim() || undefined,
          batch_id: batchIdFilter.trim() || undefined,
        };
        const data = await fetchAdminConversations(filters);
        setConversations(data.results);
        setTotalCount(data.count);
        setCurrentPage(pageToLoad);

        // If currently selected conversation is not in new list and not mobile, keep or select first
        if (data.results.length > 0 && !selectedConversationId) {
          selectConversation(data.results[0].id);
        }
      } catch (err: unknown) {
        setListError(err instanceof Error ? err.message : "Failed to load conversations");
      } finally {
        setLoadingList(false);
      }
    },
    [pageSize, searchQuery, typeFilter, dateFrom, dateTo, courseIdFilter, batchIdFilter, selectedConversationId]
  );

  useEffect(() => {
    loadConversations(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeFilter]);

  // Handle Search submit
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    loadConversations(1);
  };

  const handleResetFilters = () => {
    setSearchQuery("");
    setTypeFilter("ALL");
    setDateFrom("");
    setDateTo("");
    setCourseIdFilter("");
    setBatchIdFilter("");
    setTimeout(() => {
      loadConversations(1);
    }, 0);
  };

  // Select conversation & load inspection view
  const selectConversation = async (convId: string) => {
    setSelectedConversationId(convId);
    setMobileShowDetail(true);
    setLoadingMessages(true);
    setMessagesError(null);

    try {
      const [detail, msgsResponse] = await Promise.all([
        fetchAdminConversationDetail(convId),
        fetchAdminMessages(convId, 1),
      ]);
      setSelectedConversation(detail);
      setMessages(msgsResponse.results);
      setMessagesCount(msgsResponse.count);
      setMessagesPage(1);
    } catch (err: unknown) {
      setMessagesError(err instanceof Error ? err.message : "Failed to load conversation details");
    } finally {
      setLoadingMessages(false);
    }
  };

  // Open moderation modal
  const openModerationModal = (message: AdminMessage, action: "soft_delete" | "restore") => {
    setModeratingMessage(message);
    setModerationAction(action);
    setModerationReason("");
    setModerationError(null);
  };

  // Submit message moderation
  const handleModerateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!moderatingMessage) return;

    if (moderationAction === "soft_delete" && !moderationReason.trim()) {
      setModerationError("A reason is mandatory when soft-deleting a message.");
      return;
    }

    setSubmittingModeration(true);
    setModerationError(null);

    try {
      const updatedMessage = await moderateAdminMessage(
        moderatingMessage.id,
        moderationAction,
        moderationReason.trim()
      );

      // Update message in local stream
      setMessages((prev) =>
        prev.map((m) => (m.id === updatedMessage.id ? updatedMessage : m))
      );

      // Close modal & show toast
      setModeratingMessage(null);
      const actionMsg =
        moderationAction === "soft_delete"
          ? "Message soft-deleted and hidden from student chat."
          : "Message restored to student chat.";
      setModerationSuccessToast(actionMsg);
      setTimeout(() => setModerationSuccessToast(null), 4000);
    } catch (err: unknown) {
      setModerationError(err instanceof Error ? err.message : "Moderation action failed");
    } finally {
      setSubmittingModeration(false);
    }
  };

  // Secure attachment download
  const handleDownloadAttachment = async (messageId: number, filename?: string) => {
    setDownloadingAttachmentId(messageId);
    try {
      const data = await getAdminAttachmentPresignedUrl(messageId);
      if (data.download_url) {
        window.open(data.download_url, "_blank", "noopener,noreferrer");
      }
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Could not generate secure attachment link");
    } finally {
      setDownloadingAttachmentId(null);
    }
  };

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

  // Role Badge Helper
  const renderRoleBadge = (role: string) => {
    switch (role) {
      case "ADMIN":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20">
            <Shield className="w-2.5 h-2.5" /> Admin
          </span>
        );
      case "TEACHER":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <GraduationCap className="w-2.5 h-2.5" /> Teacher
          </span>
        );
      case "MENTOR":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-teal-500/10 text-teal-400 border border-teal-500/20">
            <Sparkles className="w-2.5 h-2.5" /> Mentor
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-zinc-800 text-zinc-300 border border-zinc-700">
            <User className="w-2.5 h-2.5" /> Student
          </span>
        );
    }
  };

  // Conversation Type Badge Helper
  const renderTypeBadge = (type: string, display?: string) => {
    switch (type) {
      case "DIRECT":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20">
            Direct (1:1)
          </span>
        );
      case "BATCH_GROUP":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            Batch Group
          </span>
        );
      case "COURSE_GROUP":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
            Course Group
          </span>
        );
      case "SUPPORT":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-purple-500/10 text-purple-400 border border-purple-500/20">
            Support
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-zinc-800 text-zinc-300">
            {display || type}
          </span>
        );
    }
  };

  return (
    <div className="space-y-6 max-w-[1600px] mx-auto pb-12">
      {/* Toast Notification */}
      {moderationSuccessToast && (
        <div className="fixed top-6 right-6 z-50 flex items-center gap-2 px-4 py-3 bg-emerald-950 border border-emerald-500/30 text-emerald-200 rounded-lg shadow-xl shadow-black/50 text-sm animate-in fade-in slide-in-from-top-2">
          <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" />
          <span>{moderationSuccessToast}</span>
        </div>
      )}

      {/* Header Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-white/5 pb-5">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
              <MessageSquare className="w-6 h-6 text-[#facc15]" />
              Chat Management
            </h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-white/10 text-zinc-300">
              {totalCount} {totalCount === 1 ? "Conversation" : "Conversations"}
            </span>
          </div>
          <p className="text-sm text-zinc-400 mt-1">
            Platform-wide conversation inspection, participant oversight, attachment verification, and message moderation.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => loadConversations(currentPage)}
            disabled={loadingList}
            className="flex items-center gap-2 px-3 py-2 text-xs font-medium bg-white/5 hover:bg-white/10 text-zinc-200 border border-white/10 rounded-lg transition-colors disabled:opacity-50"
            title="Refresh conversation list"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loadingList ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Search and Filters Bar */}
      <div className="bg-[#121215] border border-white/5 rounded-xl p-4 space-y-3">
        <form onSubmit={handleSearchSubmit} className="flex flex-col md:flex-row gap-3">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by participant name, phone, email, user ID, or conversation title..."
              className="w-full bg-[#18181b] border border-white/10 rounded-lg pl-9 pr-4 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-[#facc15]/50 transition-colors"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="bg-[#18181b] border border-white/10 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-[#facc15]/50 transition-colors"
            >
              <option value="ALL">All Conversation Types</option>
              <option value="DIRECT">Direct (1:1)</option>
              <option value="BATCH_GROUP">Live Batch Group</option>
              <option value="COURSE_GROUP">Course Community</option>
              <option value="SUPPORT">Support</option>
            </select>

            <button
              type="button"
              onClick={() => setShowAdvancedFilters(!showAdvancedFilters)}
              className={`flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border transition-colors ${
                showAdvancedFilters || dateFrom || dateTo || courseIdFilter || batchIdFilter
                  ? "bg-[#facc15]/10 border-[#facc15]/30 text-[#facc15]"
                  : "bg-[#18181b] border-white/10 text-zinc-300 hover:bg-white/5"
              }`}
            >
              <Filter className="w-3.5 h-3.5" />
              <span>Filters</span>
              <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAdvancedFilters ? "rotate-180" : ""}`} />
            </button>

            <button
              type="submit"
              className="px-4 py-2 text-sm font-semibold bg-[#facc15] hover:bg-[#eab308] text-zinc-900 rounded-lg transition-colors"
            >
              Search
            </button>

            {(searchQuery || typeFilter !== "ALL" || dateFrom || dateTo || courseIdFilter || batchIdFilter) && (
              <button
                type="button"
                onClick={handleResetFilters}
                className="px-3 py-2 text-sm text-zinc-400 hover:text-white transition-colors"
              >
                Reset
              </button>
            )}
          </div>
        </form>

        {/* Collapsible Advanced Filters Tray */}
        {showAdvancedFilters && (
          <div className="pt-3 border-t border-white/5 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Created From Date</label>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="w-full bg-[#18181b] border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-[#facc15]/50"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Created To Date</label>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="w-full bg-[#18181b] border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-[#facc15]/50"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Course ID</label>
              <input
                type="number"
                value={courseIdFilter}
                onChange={(e) => setCourseIdFilter(e.target.value)}
                placeholder="e.g. 12"
                className="w-full bg-[#18181b] border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white placeholder-zinc-600 focus:outline-none focus:border-[#facc15]/50"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Batch ID</label>
              <input
                type="number"
                value={batchIdFilter}
                onChange={(e) => setBatchIdFilter(e.target.value)}
                placeholder="e.g. 5"
                className="w-full bg-[#18181b] border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white placeholder-zinc-600 focus:outline-none focus:border-[#facc15]/50"
              />
            </div>
          </div>
        )}
      </div>

      {/* Main Content Layout: Split Master-Detail */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 min-h-[680px]">
        {/* Left Column: Conversation List (5 cols on lg) */}
        <div
          className={`lg:col-span-5 flex flex-col bg-[#121215] border border-white/5 rounded-xl overflow-hidden ${
            mobileShowDetail ? "hidden lg:flex" : "flex"
          }`}
        >
          <div className="p-3.5 border-b border-white/5 flex items-center justify-between bg-[#151518]">
            <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
              Conversations ({totalCount})
            </span>
            <span className="text-[11px] text-zinc-500">
              Page {currentPage} of {totalPages}
            </span>
          </div>

          {/* List Scroll Area */}
          <div className="flex-1 overflow-y-auto divide-y divide-white/5 custom-scrollbar max-h-[700px]">
            {loadingList ? (
              <div className="p-8 text-center space-y-3">
                <RefreshCw className="w-6 h-6 animate-spin text-[#facc15] mx-auto" />
                <p className="text-xs text-zinc-400">Loading platform conversations...</p>
              </div>
            ) : listError ? (
              <div className="p-8 text-center space-y-3">
                <AlertTriangle className="w-8 h-8 text-rose-400 mx-auto" />
                <p className="text-sm font-medium text-rose-300">Failed to load conversations</p>
                <p className="text-xs text-zinc-400">{listError}</p>
                <button
                  onClick={() => loadConversations(currentPage)}
                  className="px-3 py-1.5 text-xs bg-white/10 hover:bg-white/15 text-white rounded-md transition-colors"
                >
                  Retry
                </button>
              </div>
            ) : conversations.length === 0 ? (
              <div className="p-12 text-center space-y-3">
                <MessageSquare className="w-10 h-10 text-zinc-600 mx-auto stroke-[1.5]" />
                <p className="text-sm font-medium text-zinc-300">No conversations found</p>
                <p className="text-xs text-zinc-500 max-w-xs mx-auto">
                  Try adjusting your search query, type filters, or date range.
                </p>
              </div>
            ) : (
              conversations.map((conv) => {
                const isSelected = selectedConversationId === conv.id;
                const participantsList = conv.participants || [];
                const participantSummary = participantsList
                  .map((p) => p.user?.full_name || p.user?.username)
                  .filter(Boolean)
                  .join(", ");

                return (
                  <div
                    key={conv.id}
                    onClick={() => selectConversation(conv.id)}
                    className={`p-4 cursor-pointer transition-colors relative ${
                      isSelected
                        ? "bg-[#facc15]/10 border-l-4 border-l-[#facc15]"
                        : "hover:bg-white/[0.03]"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2 mb-1.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        {renderTypeBadge(conv.conversation_type, conv.conversation_type_display)}
                        <span className="text-xs font-mono text-zinc-500">#{conv.id.slice(0, 8)}</span>
                      </div>
                      <span className="text-[11px] text-zinc-500 whitespace-nowrap">
                        {conv.last_message_at
                          ? new Date(conv.last_message_at).toLocaleDateString([], {
                              month: "short",
                              day: "numeric",
                            })
                          : new Date(conv.created_at).toLocaleDateString([], {
                              month: "short",
                              day: "numeric",
                            })}
                      </span>
                    </div>

                    <h3 className="text-sm font-semibold text-white truncate mb-1">
                      {conv.title || "Untitled Conversation"}
                    </h3>

                    {/* Associated Course or Batch */}
                    {(conv.course_title || conv.batch_name) && (
                      <div className="text-[11px] text-zinc-400 truncate mb-1 flex items-center gap-1.5">
                        {conv.course_title && (
                          <span className="text-amber-400/90 font-medium truncate">
                            📚 {conv.course_title}
                          </span>
                        )}
                        {conv.batch_name && (
                          <span className="text-emerald-400/90 font-medium truncate">
                            • 👥 {conv.batch_name}
                          </span>
                        )}
                      </div>
                    )}

                    {/* Participant Summary */}
                    <p className="text-xs text-zinc-400 truncate mb-2">
                      <span className="text-zinc-500">Participants:</span> {participantSummary || "None"}
                    </p>

                    {/* Last message preview */}
                    <div className="flex items-center justify-between text-xs text-zinc-500">
                      <span className="truncate max-w-[260px] italic">
                        {conv.last_message_preview || "No messages yet"}
                      </span>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-white/5 text-zinc-400 shrink-0">
                        {conv.message_count} {conv.message_count === 1 ? "msg" : "msgs"}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* List Pagination */}
          {totalPages > 1 && (
            <div className="p-3 border-t border-white/5 bg-[#151518] flex items-center justify-between text-xs">
              <button
                onClick={() => loadConversations(currentPage - 1)}
                disabled={currentPage <= 1 || loadingList}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-white/5 hover:bg-white/10 text-zinc-300 disabled:opacity-40 transition-colors"
              >
                <ChevronLeft className="w-3.5 h-3.5" /> Previous
              </button>
              <span className="text-zinc-400">
                Page {currentPage} of {totalPages}
              </span>
              <button
                onClick={() => loadConversations(currentPage + 1)}
                disabled={currentPage >= totalPages || loadingList}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-white/5 hover:bg-white/10 text-zinc-300 disabled:opacity-40 transition-colors"
              >
                Next <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </div>

        {/* Right Column: Conversation Inspection Stream (7 cols on lg) */}
        <div
          className={`lg:col-span-7 flex flex-col bg-[#121215] border border-white/5 rounded-xl overflow-hidden ${
            mobileShowDetail ? "flex" : "hidden lg:flex"
          }`}
        >
          {selectedConversation ? (
            <>
              {/* Inspection Header */}
              <div className="p-4 border-b border-white/5 bg-[#151518] flex flex-col gap-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setMobileShowDetail(false)}
                        className="lg:hidden p-1.5 rounded-md bg-white/5 text-zinc-300 hover:text-white"
                        title="Back to list"
                      >
                        <ChevronLeft className="w-4 h-4" />
                      </button>
                      <h2 className="text-base font-bold text-white flex items-center gap-2">
                        {selectedConversation.title || "Conversation Inspection"}
                      </h2>
                      {renderTypeBadge(selectedConversation.conversation_type)}
                    </div>
                    <div className="text-xs text-zinc-400 flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span>
                        <strong className="text-zinc-300">ID:</strong>{" "}
                        <span className="font-mono text-[11px]">{selectedConversation.id}</span>
                      </span>
                      <span>
                        <strong className="text-zinc-300">Created:</strong>{" "}
                        {new Date(selectedConversation.created_at).toLocaleString([], {
                          dateStyle: "medium",
                          timeStyle: "short",
                        })}
                      </span>
                      {selectedConversation.course_title && (
                        <span>
                          <strong className="text-zinc-300">Course:</strong>{" "}
                          <span className="text-amber-400 font-medium">
                            {selectedConversation.course_title} (ID: {selectedConversation.course})
                          </span>
                        </span>
                      )}
                      {selectedConversation.batch_name && (
                        <span>
                          <strong className="text-zinc-300">Batch:</strong>{" "}
                          <span className="text-emerald-400 font-medium">
                            {selectedConversation.batch_name} (ID: {selectedConversation.batch})
                          </span>
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="text-right shrink-0">
                    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold bg-white/5 text-zinc-300 border border-white/10">
                      <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
                      Admin Inspection Mode
                    </span>
                  </div>
                </div>

                {/* Participant Details Drawer / Bar */}
                <div className="pt-2 border-t border-white/5">
                  <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                    <Users className="w-3.5 h-3.5" /> Participants ({selectedConversation.participants?.length || 0})
                  </div>
                  <div className="flex flex-wrap gap-2 max-h-28 overflow-y-auto custom-scrollbar">
                    {selectedConversation.participants?.map((part) => (
                      <div
                        key={part.id}
                        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-[#18181b] border border-white/5 text-xs text-zinc-300"
                      >
                        <div className="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center font-bold text-[10px] text-white shrink-0">
                          {part.user?.first_name?.[0] || part.user?.username?.[0] || "U"}
                        </div>
                        <div className="truncate max-w-[150px]">
                          <div className="font-medium text-white truncate">
                            {part.user?.full_name || part.user?.username}
                          </div>
                          <div className="text-[10px] text-zinc-500 truncate">
                            {part.user?.email || part.user?.phone_number || `@${part.user?.username}`}
                          </div>
                        </div>
                        {renderRoleBadge(part.user?.role || "STUDENT")}
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Message Transcript Stream */}
              <div className="flex-1 p-4 overflow-y-auto space-y-4 custom-scrollbar max-h-[580px] bg-[#0c0c0e]">
                {loadingMessages ? (
                  <div className="py-20 text-center space-y-3">
                    <RefreshCw className="w-6 h-6 animate-spin text-[#facc15] mx-auto" />
                    <p className="text-xs text-zinc-400">Loading full message history & attachments...</p>
                  </div>
                ) : messagesError ? (
                  <div className="py-20 text-center space-y-3">
                    <AlertTriangle className="w-8 h-8 text-rose-400 mx-auto" />
                    <p className="text-sm font-medium text-rose-300">Error loading messages</p>
                    <p className="text-xs text-zinc-400">{messagesError}</p>
                    <button
                      onClick={() => selectConversation(selectedConversation.id)}
                      className="px-3 py-1.5 text-xs bg-white/10 hover:bg-white/15 text-white rounded-md transition-colors"
                    >
                      Retry
                    </button>
                  </div>
                ) : messages.length === 0 ? (
                  <div className="py-20 text-center space-y-2">
                    <MessageSquare className="w-8 h-8 text-zinc-600 mx-auto stroke-[1.5]" />
                    <p className="text-sm font-medium text-zinc-400">No messages found in this conversation</p>
                  </div>
                ) : (
                  messages.map((msg) => {
                    const isSystem = msg.message_type === "SYSTEM";
                    const isDeleted = msg.is_deleted;

                    if (isSystem) {
                      return (
                        <div key={msg.id} className="flex justify-center my-2">
                          <div className="px-3 py-1 rounded-full bg-white/5 border border-white/5 text-[11px] text-zinc-400 flex items-center gap-1.5">
                            <Clock className="w-3 h-3 text-zinc-500" />
                            <span>{msg.content}</span>
                            <span className="text-zinc-600">• {new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                          </div>
                        </div>
                      );
                    }

                    return (
                      <div
                        key={msg.id}
                        className={`rounded-xl p-4 transition-colors border ${
                          isDeleted
                            ? "bg-rose-950/20 border-rose-500/30"
                            : "bg-[#151518] border-white/5 hover:border-white/10"
                        }`}
                      >
                        {/* Message Sender Header */}
                        <div className="flex items-start justify-between gap-2 mb-2">
                          <div className="flex items-center gap-2">
                            <div className="w-7 h-7 rounded-full bg-white/10 flex items-center justify-center font-bold text-xs text-white">
                              {msg.sender?.first_name?.[0] || msg.sender?.username?.[0] || "U"}
                            </div>
                            <div>
                              <div className="flex items-center gap-1.5">
                                <span className="text-xs font-bold text-white">
                                  {msg.sender?.full_name || msg.sender?.username}
                                </span>
                                {renderRoleBadge(msg.sender?.role || "STUDENT")}
                                <span className="text-[10px] text-zinc-500 font-mono">
                                  (ID: {msg.sender?.id})
                                </span>
                              </div>
                              <div className="text-[10px] text-zinc-500">
                                {msg.sender?.email && <span>{msg.sender.email}</span>}
                                {msg.sender?.phone_number && (
                                  <span className="ml-2 font-mono">{msg.sender.phone_number}</span>
                                )}
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center gap-2 shrink-0">
                            <span className="text-[11px] text-zinc-400">
                              {new Date(msg.created_at).toLocaleString([], {
                                month: "short",
                                day: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </span>

                            {/* Moderation Trigger Button */}
                            {isDeleted ? (
                              <button
                                onClick={() => openModerationModal(msg, "restore")}
                                className="flex items-center gap-1 px-2 py-1 rounded text-xs font-semibold bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 transition-colors"
                                title="Restore message to regular chat view"
                              >
                                <RotateCcw className="w-3 h-3" />
                                Restore
                              </button>
                            ) : (
                              <button
                                onClick={() => openModerationModal(msg, "soft_delete")}
                                className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 transition-colors"
                                title="Soft-delete with reason"
                              >
                                <ShieldAlert className="w-3 h-3" />
                                Moderate
                              </button>
                            )}
                          </div>
                        </div>

                        {/* Soft-deletion Banner if deleted */}
                        {isDeleted && (
                          <div className="mb-3 px-3 py-2 rounded-lg bg-rose-950/40 border border-rose-500/30 text-rose-200 text-xs space-y-1">
                            <div className="flex items-center gap-1.5 font-semibold text-rose-300">
                              <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
                              <span>Soft-Deleted by Administrator</span>
                              {msg.deleted_by && (
                                <span className="font-normal text-rose-300">
                                  ({msg.deleted_by.username})
                                </span>
                              )}
                              {msg.deleted_at && (
                                <span className="text-[10px] text-rose-400">
                                  • {new Date(msg.deleted_at).toLocaleString()}
                                </span>
                              )}
                            </div>
                            {msg.moderation_reason && (
                              <p className="text-[11px] text-rose-200/90 pl-5">
                                <strong>Reason:</strong> {msg.moderation_reason}
                              </p>
                            )}
                          </div>
                        )}

                        {/* Content text */}
                        <p className={`text-sm leading-relaxed whitespace-pre-wrap ${isDeleted ? "text-zinc-400 italic" : "text-zinc-200"}`}>
                          {msg.content}
                        </p>

                        {/* Attachment Box */}
                        {msg.has_attachment && (
                          <div className="mt-3 p-3 rounded-lg bg-[#18181b] border border-white/10 flex items-center justify-between gap-3 max-w-lg">
                            <div className="flex items-center gap-2.5 truncate">
                              <div className="p-2 rounded bg-white/5 text-[#facc15] shrink-0">
                                {msg.attachment_mime_type?.startsWith("image/") ? (
                                  <ImageIcon className="w-4 h-4" />
                                ) : (
                                  <FileText className="w-4 h-4" />
                                )}
                              </div>
                              <div className="truncate">
                                <p className="text-xs font-semibold text-white truncate">
                                  {msg.attachment_name || "Attachment"}
                                </p>
                                <p className="text-[10px] text-zinc-500">
                                  {formatFileSize(msg.attachment_size)} • {msg.attachment_mime_type || "file"}
                                </p>
                              </div>
                            </div>

                            <button
                              onClick={() => handleDownloadAttachment(msg.id, msg.attachment_name)}
                              disabled={downloadingAttachmentId === msg.id}
                              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-[#facc15]/10 hover:bg-[#facc15]/20 text-[#facc15] border border-[#facc15]/30 transition-colors shrink-0 disabled:opacity-50"
                              title="Generate secure presigned download link"
                            >
                              {downloadingAttachmentId === msg.id ? (
                                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                              ) : (
                                <Download className="w-3.5 h-3.5" />
                              )}
                              <span>Secure Download</span>
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-12 text-center text-zinc-500 space-y-3">
              <MessageSquare className="w-12 h-12 text-zinc-700 stroke-[1.2]" />
              <p className="text-sm font-medium text-zinc-400">Select a conversation to inspect</p>
              <p className="text-xs text-zinc-600 max-w-sm">
                Choose any conversation from the list to view its complete audit history, participants, shared files, and moderation actions.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Moderation Modal Dialog */}
      {moderatingMessage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 animate-in fade-in">
          <div className="bg-[#18181b] border border-white/10 rounded-xl max-w-md w-full p-6 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between border-b border-white/10 pb-3">
              <div className="flex items-center gap-2">
                {moderationAction === "soft_delete" ? (
                  <ShieldAlert className="w-5 h-5 text-rose-400" />
                ) : (
                  <RotateCcw className="w-5 h-5 text-emerald-400" />
                )}
                <h3 className="text-base font-bold text-white">
                  {moderationAction === "soft_delete" ? "Moderate Message (Soft Delete)" : "Restore Message"}
                </h3>
              </div>
              <button
                onClick={() => setModeratingMessage(null)}
                className="text-zinc-400 hover:text-white transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="text-xs text-zinc-400 space-y-2">
              <p>
                <strong>Sender:</strong> {moderatingMessage.sender?.full_name || moderatingMessage.sender?.username} (
                {moderatingMessage.sender?.role})
              </p>
              <div className="p-3 rounded bg-black/40 border border-white/5 font-mono text-[11px] text-zinc-300 max-h-24 overflow-y-auto">
                &ldquo;{moderatingMessage.content}&rdquo;
              </div>
              <p className="text-zinc-500">
                {moderationAction === "soft_delete"
                  ? "Soft-deleting hides this message from regular participants while preserving the full record for administrators. This action is permanently recorded in AdminAuditLog."
                  : "Restoring unhides this message and makes it visible again in the conversation. This action is recorded in AdminAuditLog."}
              </p>
            </div>

            <form onSubmit={handleModerateSubmit} className="space-y-4">
              {moderationAction === "soft_delete" && (
                <div>
                  <label className="block text-xs font-semibold text-zinc-300 mb-1.5">
                    Moderation Reason <span className="text-rose-400">*</span>
                  </label>
                  <textarea
                    rows={3}
                    required
                    value={moderationReason}
                    onChange={(e) => setModerationReason(e.target.value)}
                    placeholder="e.g. Violation of community standards, inappropriate conduct, spam, or harassment..."
                    className="w-full bg-[#121215] border border-white/10 rounded-lg p-2.5 text-xs text-white placeholder-zinc-600 focus:outline-none focus:border-[#facc15]/50"
                  />
                </div>
              )}

              {moderationError && (
                <div className="p-2.5 rounded bg-rose-950/50 border border-rose-500/30 text-rose-300 text-xs flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>{moderationError}</span>
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-white/10">
                <button
                  type="button"
                  onClick={() => setModeratingMessage(null)}
                  disabled={submittingModeration}
                  className="px-3 py-2 text-xs font-medium text-zinc-400 hover:text-white rounded-lg transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submittingModeration}
                  className={`px-4 py-2 text-xs font-bold rounded-lg transition-colors flex items-center gap-1.5 ${
                    moderationAction === "soft_delete"
                      ? "bg-rose-600 hover:bg-rose-500 text-white"
                      : "bg-emerald-600 hover:bg-emerald-500 text-white"
                  } disabled:opacity-50`}
                >
                  {submittingModeration && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                  {moderationAction === "soft_delete" ? "Confirm Soft-Delete" : "Confirm Restore"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
