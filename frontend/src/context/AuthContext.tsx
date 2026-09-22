"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";

export interface User {
  id: number;
  username: string;
  email: string;
  phone_number?: string | null;
  first_name?: string;
  last_name?: string;
  is_student?: boolean;
  is_teacher?: boolean;
  is_mentor?: boolean;
  is_superuser?: boolean;
  is_staff?: boolean;
  is_admin?: boolean;
  is_onboarded?: boolean;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  isAuthenticated: boolean;
  isTeacher: boolean;
  isAdmin: boolean;
  refreshUser: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchUser = useCallback(async () => {
    try {
      // GET /api/users/me/ is decorated with @ensure_csrf_cookie on the backend,
      // guaranteeing that authenticated web clients receive a valid csrftoken cookie.
      const res = await fetch(`${API_BASE}/api/users/me/`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setUser(data);
      } else {
        setUser(null);
      }
    } catch (err) {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  const logout = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/auth/logout/`, {
        method: "POST",
        credentials: "include",
      });
    } catch (err) {
      console.error("Logout request failed:", err);
    } finally {
      setUser(null);
      window.location.href = "/login";
    }
  }, []);

  const isTeacher = !!(user?.is_teacher || user?.is_mentor);
  const isAdmin = !!(user?.is_superuser || user?.is_staff || user?.is_admin);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isAuthenticated: !!user,
        isTeacher,
        isAdmin,
        refreshUser: fetchUser,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
