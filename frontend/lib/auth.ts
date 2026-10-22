"use client";

import { useEffect, useState } from "react";
import { api, User } from "./api";

export function useMe() {
  const [user, setUser] = useState<User | null>(null);
  useEffect(() => {
    api<User>("/auth/me").then(setUser).catch(() => {});
  }, []);
  return user;
}

export async function logout() {
  await api("/auth/logout", { method: "POST" }).catch(() => {});
  location.href = "/login";
}
