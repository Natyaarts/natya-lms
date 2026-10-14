// Natya LMS Chat API Client and Types
// Enforces exact 52,428,800 bytes (50 MiB) client-side boundary and secure attachment handling.

export const MAX_CHAT_ATTACHMENT_SIZE_BYTES = 52428800; // 50 * 1024 * 1024 bytes (50 MiB)
export const ALLOWED_CHAT_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'pdf', 'docx', 'txt'];

export interface UserMinimal {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  role: 'STUDENT' | 'TEACHER' | 'MENTOR' | 'STAFF';
  profile_image_url?: string | null;
}

export interface ConversationParticipant {
  id: number;
  user: UserMinimal;
  role: string;
  joined_at: string;
  last_read_at?: string | null;
  is_muted: boolean;
}

export interface Conversation {
  id: number | string;
  conversation_type: 'DIRECT' | 'COURSE' | 'BATCH' | 'SUPPORT';
  title: string;
  course?: number | null;
  batch?: number | null;
  created_by?: number | null;
  last_message_at?: string | null;
  last_message_preview?: string;
  created_at: string;
  updated_at: string;
  participants: ConversationParticipant[];
  unread_count: number;
  other_participant?: UserMinimal | null;
}

export interface Message {
  id: number;
  conversation: number | string;
  sender: UserMinimal;
  content: string;
  message_type: 'TEXT' | 'IMAGE' | 'FILE' | 'SYSTEM';
  attachment_name?: string;
  attachment_size?: number;
  attachment_mime_type?: string;
  has_attachment: boolean;
  attachment_url?: string | null;
  is_deleted: boolean;
  created_at: string;
}

export interface MessageListResponse {
  results: Message[];
  next_before: number | null;
  has_more: boolean;
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

export const validateChatAttachment = (file: File): { valid: boolean; error?: string } => {
  if (file.size > MAX_CHAT_ATTACHMENT_SIZE_BYTES) {
    return {
      valid: false,
      error: `File is too large (${formatFileSize(file.size)}). Maximum allowed size is 50 MB (52,428,800 bytes).`,
    };
  }

  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  if (!ALLOWED_CHAT_EXTENSIONS.includes(ext)) {
    return {
      valid: false,
      error: `File type ".${ext}" is not supported. Allowed formats: ${ALLOWED_CHAT_EXTENSIONS.join(', ')}.`,
    };
  }

  return { valid: true };
};

export async function fetchConversations(): Promise<Conversation[]> {
  const res = await fetch(`${API_BASE}/api/chat/conversations/`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    throw new Error(`Failed to load conversations: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchMessages(conversationId: number | string, before?: number, limit = 30): Promise<MessageListResponse> {
  const url = new URL(`${API_BASE}/api/chat/conversations/${conversationId}/messages/`);
  url.searchParams.set('limit', String(limit));
  if (before) {
    url.searchParams.set('before', String(before));
  }

  const res = await fetch(url.toString(), {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    throw new Error(`Failed to load messages: ${res.statusText}`);
  }
  return res.json();
}

export async function createDirectConversation(recipientId: number): Promise<Conversation> {
  const csrf = getCsrfToken();
  const res = await fetch(`${API_BASE}/api/chat/conversations/`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(csrf ? { 'X-CSRFToken': csrf } : {}),
    },
    body: JSON.stringify({
      conversation_type: 'DIRECT',
      recipient_id: recipientId,
    }),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    const message = errorData.error || errorData.detail || errorData.recipient_id?.[0] || 'Could not start conversation';
    throw new Error(message);
  }
  return res.json();
}

export function sendMessageWithProgress(
  conversationId: number | string,
  content: string,
  file?: File,
  onProgress?: (percent: number) => void
): Promise<Message> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const url = `${API_BASE}/api/chat/conversations/${conversationId}/messages/`;

    xhr.open('POST', url, true);
    xhr.withCredentials = true;
    xhr.timeout = 120000; // 120-second timeout for up to 50 MiB uploads

    const csrf = getCsrfToken();
    if (csrf) {
      xhr.setRequestHeader('X-CSRFToken', csrf);
    }

    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          const percent = Math.round((event.loaded / event.total) * 100);
          onProgress(percent);
        }
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          resolve(data);
        } catch {
          reject(new Error('Invalid response from server'));
        }
      } else {
        try {
          const err = JSON.parse(xhr.responseText);
          const errorMsg =
            err.error ||
            err.detail ||
            err.attachment?.[0] ||
            err.content?.[0] ||
            `Failed to send message (${xhr.status})`;
          reject(new Error(errorMsg));
        } catch {
          reject(new Error(`Server error (${xhr.status})`));
        }
      }
    };

    xhr.onerror = () => {
      reject(new Error('Network error during upload. Please check your connection.'));
    };

    xhr.ontimeout = () => {
      reject(new Error('Upload timed out after 120 seconds. Please try again with a faster connection.'));
    };

    const formData = new FormData();
    if (content.trim()) {
      formData.append('content', content.trim());
    }
    if (file) {
      formData.append('attachment', file, file.name);
    }

    xhr.send(formData);
  });
}

export async function markConversationRead(conversationId: number | string): Promise<void> {
  const csrf = getCsrfToken();
  await fetch(`${API_BASE}/api/chat/conversations/${conversationId}/read/`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(csrf ? { 'X-CSRFToken': csrf } : {}),
    },
  }).catch(() => {});
}

export async function fetchUnreadCount(): Promise<number> {
  try {
    const res = await fetch(`${API_BASE}/api/chat/conversations/unread-count/`, {
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    });
    if (!res.ok) return 0;
    const data = await res.json();
    return data.total_unread_count ?? data.unread_count ?? 0;
  } catch {
    return 0;
  }
}

export async function fetchDirectChatRecipients(): Promise<UserMinimal[]> {
  const res = await fetch(`${API_BASE}/api/chat/recipients/`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error('Authentication required. Please sign in to view contacts.');
    }
    const errData = await res.json().catch(() => ({}));
    const message = errData.detail || errData.error || `Failed to load contacts (HTTP ${res.status})`;
    throw new Error(message);
  }
  const data = await res.json();
  return Array.isArray(data) ? data : data.results || [];
}

