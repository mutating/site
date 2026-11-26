import type { User } from "./types";

export interface UserMenuIdentity {
  name: string;
  initial: string | null;
}

/** First available display name plus the uppercase first letter of the surname. */
export function userMenuIdentity(user: Pick<User, "first_name" | "last_name" | "nickname" | "email">): UserMenuIdentity {
  const trim = (value: string | null | undefined) => value?.trim() ?? "";
  const lastName = trim(user.last_name);
  return {
    name: trim(user.first_name) || trim(user.nickname) || trim(user.email) || "?",
    initial: lastName ? `${Array.from(lastName)[0].toUpperCase()}.` : null,
  };
}
