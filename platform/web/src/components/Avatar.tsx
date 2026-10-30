import { useSession } from "../lib/session";
import { ApiImage } from "./ApiImage";

export function Avatar({ large = false, round = false }: { large?: boolean; round?: boolean }) {
  const { user, avatarRevision } = useSession();
  // Saving/refetching a profile retries even if identical image bytes keep the hash.
  const path = user?.avatar_version ? `users/me/avatar?v=${encodeURIComponent(user.avatar_version)}${avatarRevision ? `&r=${avatarRevision}` : ""}` : null;
  const name = [user?.first_name, user?.nickname, user?.email].map(value => value?.trim()).find(Boolean) || "?";
  const initial = Array.from(name)[0].toUpperCase();
  return <span className={`avatar${large ? " large" : ""}${round ? " avatar--round" : ""}`}>
    <ApiImage key={user?.id} path={path} fallback={initial} />
  </span>;
}
