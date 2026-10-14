// Admin Chat API Client and Types for Natya LMS
// Secure administrative client with strictly audited actions and full type safety.

export interface AdminUser {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  full_name: string;
  email: string;
  phone_number?: string;
  role: 'ADMIN' | 'TEACHER' | 'MENTOR' | 'STUDENT';
  is_teacher: boolean;
  is_mentor: boolean;
  is_student: boolean;
  is_staff: boolean;
  is_superuser: boolean;
}

export interface AdminConversationParticipant {
  id: number;
  user: AdminUser;
  is_admin: boolean;
  is_muted: boolean;
  last_read_at?: string | null;
  joined_at: string;
}

export interface AdminConversation {
  id: string;
  conversation_type: 'DIRECT' | 'COURSE_GROUP' | 'BATCH_GROUP' | 'SUPPORT';
  conversation_type_display: string;
  title: string;
  course?: number | null;
  course_title?: string | null;
  batch?: number | null;
  batch_name?: string | null;
  created_by?: number | null;
  created_by_user?: {
    id: number;
    username: string;
    first_name: string;
    last_name: string;
    role: string;
  } | null;
  last_message_at?: string | null;
  last_message_preview?: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  participants: AdminConversationParticipant[];
}

export interface AdminMessage {
  id: number;
  conversation: string;
  sender: AdminUser;
  content: string;
  message_type: 'TEXT' | 'IMAGE' | 'FILE' | 'SYSTEM';
  attachment_name?: string;
  attachment_size?: number;
  attachment_mime_type?: string;
  has_attachment: boolean;
  attachment_url?: string | null;
  is_deleted: boolean;
  deleted_at?: string | null;
  deleted_by?: { id: number; username: string } | null;
  moderation_reason?: string;
  created_at: string;
}

export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface AdminConversationFilters {
  page?: number;
  page_size?: number;
  type?: string;
  course_id?: number | string;
  batch_id?: number | string;
  participant_id?: number | string;
  date_from?: string;
  date_to?: string;
  search?: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const getCsrfToken = (): string => {
  if (typeof document === 'undefined') return '';
  let csrfToken = '';
  if (document.cookie && document.cookie !== '') {
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.startsWith('csrftoken=')) {
        csrfToken = decodeURIComponent(cookie.substring('csrftoken='.length));
        break;
      }
    }
  }
  return csrfToken;
};

export const formatFileSize = (bytes?: number): string => {
  if (!bytes || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
};

export async function fetchAdminConversations(
  filters: AdminConversationFilters = {}
): Promise<PaginatedResponse<AdminConversation>> {
  const url = new URL(`${API_BASE}/api/chat/admin/conversations/`);
  if (filters.page) url.searchParams.set('page', String(filters.page));
  if (filters.page_size) url.searchParams.set('page_size', String(filters.page_size));
  if (filters.type && filters.type !== 'ALL') url.searchParams.set('type', filters.type);
  if (filters.course_id) url.searchParams.set('course_id', String(filters.course_id));
  if (filters.batch_id) url.searchParams.set('batch_id', String(filters.batch_id));
  if (filters.participant_id) url.searchParams.set('participant_id', String(filters.participant_id));
  if (filters.date_from) url.searchParams.set('date_from', filters.date_from);
  if (filters.date_to) url.searchParams.set('date_to', filters.date_to);
  if (filters.search) url.searchParams.set('search', filters.search);

  const res = await fetch(url.toString(), {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!res.ok) {
    if (res.status === 403) {
      throw new Error('Access denied: You do not have administrator permissions to view all conversations.');
    }
    const errData = await res.json().catch(() => null);
    throw new Error(errData?.detail || `Failed to fetch conversations (HTTP ${res.status})`);
  }

  return res.json();
}

export async function fetchAdminConversationDetail(
  conversationId: string
): Promise<AdminConversation> {
  const res = await fetch(`${API_BASE}/api/chat/admin/conversations/${conversationId}/`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!res.ok) {
    if (res.status === 403) {
      throw new Error('Access denied: Administrator permissions required.');
    }
    const errData = await res.json().catch(() => null);
    throw new Error(errData?.detail || `Failed to fetch conversation details (HTTP ${res.status})`);
  }

  return res.json();
}

export async function fetchAdminMessages(
  conversationId: string,
  page = 1
): Promise<PaginatedResponse<AdminMessage>> {
  const url = new URL(`${API_BASE}/api/chat/admin/conversations/${conversationId}/messages/`);
  url.searchParams.set('page', String(page));

  const res = await fetch(url.toString(), {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!res.ok) {
    if (res.status === 403) {
      throw new Error('Access denied: Administrator permissions required.');
    }
    const errData = await res.json().catch(() => null);
    throw new Error(errData?.detail || `Failed to fetch messages (HTTP ${res.status})`);
  }

  return res.json();
}

export async function moderateAdminMessage(
  messageId: number,
  action: 'soft_delete' | 'restore',
  reason?: string
): Promise<AdminMessage> {
  const csrf = getCsrfToken();
  const res = await fetch(`${API_BASE}/api/chat/admin/messages/${messageId}/moderate/`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf,
    },
    body: JSON.stringify({ action, reason: reason || '' }),
  });

  if (!res.ok) {
    const errData = await res.json().catch(() => null);
    const reasonErr = errData?.reason || errData?.detail;
    throw new Error(reasonErr || `Failed to moderate message (HTTP ${res.status})`);
  }

  return res.json();
}

export async function getAdminAttachmentPresignedUrl(
  messageId: number
): Promise<{
  download_url: string;
  expires_in: number;
  attachment_name?: string;
  attachment_size?: number;
  mime_type?: string;
}> {
  const res = await fetch(`${API_BASE}/api/chat/messages/${messageId}/attachment-url/`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!res.ok) {
    const errData = await res.json().catch(() => null);
    throw new Error(errData?.detail || `Failed to generate attachment URL (HTTP ${res.status})`);
  }

  return res.json();
}
