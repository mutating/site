import { useState } from "react";
import { useSession } from "../lib/session";
import { apiURL } from "../lib/api";
import type { User } from "../lib/types";

export function Avatar({ large = false, round = false }: { large?: boolean; round?: boolean }) {
  const { user, avatarRevision } = useSession();
  // Saving/refetching a profile retries even if identical image bytes keep the hash.
  const [failedUser, setFailedUser] = useState<User | null>(null);
  const src = user?.avatar_version ? apiURL(`users/me/avatar?v=${encodeURIComponent(user.avatar_version)}${avatarRevision ? `&r=${avatarRevision}` : ""}`) : null;
  const name = [user?.first_name, user?.nickname, user?.email].map(value => value?.trim()).find(Boolean) || "?";
  const initial = Array.from(name)[0].toUpperCase();
  return <span className={`avatar${large ? " large" : ""}${round ? " avatar--round" : ""}`}>
    {src && failedUser !== user ? <img key={src} src={src} alt="" onError={() => setFailedUser(user)} /> : initial}
  </span>;
}
