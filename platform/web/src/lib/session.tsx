import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { advanceSessionRevision, api, ApiError, json } from "./api";
import type { User } from "./types";
const Context = createContext<{
  user: User | null;
  avatarRevision: number;
  loading: boolean;
  error: unknown;
  setUser: (u: User | null) => void;
  updateUser: (update: () => Promise<unknown>) => Promise<void>;
  reload: () => void;
  signOut: () => Promise<void>;
  signingOut: boolean;
}>(null!);
export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<{ user: User | null; generation: number; avatarRevision: number }>({ user: null, generation: 0, avatarRevision: 0 });
  const currentSession = useRef(session);
  const refreshSequence = useRef(0);
  const publishedRefresh = useRef(0);
  const { user, generation, avatarRevision } = session;
  const publishUser = useCallback((next: User | null, expectedGeneration: number) => {
    const current = currentSession.current;
    if (current.generation !== expectedGeneration) return;
    // A confirmed session also supersedes old 401s after re-login by the same user.
    advanceSessionRevision();
    const updated = {
      user: next,
      generation: current.generation + Number(current.user?.id !== next?.id),
      // Never reuse a pending image URL, including after removal, A→B→A or
      // signing back in. Identical bytes alone cannot identify a fresh request.
      avatarRevision: current.avatarRevision + Number(Boolean(next?.avatar_version) && current.user !== next),
    };
    currentSession.current = updated;
    setSession(updated);
  }, []);
  // Async profile/auth actions retain the generation in which they started.
  // Their late responses must not overwrite a signed-out or newer session.
  const setUser = useCallback((next: User | null) => publishUser(next, generation), [publishUser, generation]);
  const updateUser = useCallback(async (update: () => Promise<unknown>) => {
    await update();
    if (currentSession.current.generation !== generation) return;
    // Both writes and their responses can finish out of order. Reconcile with
    // the stored profile rather than publishing a stale mutation response.
    const sequence = ++refreshSequence.current;
    const next = await api<User>("users/me");
    if (currentSession.current.generation !== generation || sequence < publishedRefresh.current) return;
    publishedRefresh.current = sequence;
    publishUser(next, generation);
  }, [generation, publishUser]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [signingOut, setSigningOut] = useState(false);
  const pendingSignOut = useRef<Promise<void> | null>(null);
  const signOut = useCallback(() => {
    if (pendingSignOut.current) return pendingSignOut.current;
    setSigningOut(true);
    pendingSignOut.current = (async () => {
      try {
        try {
          await api("auth/session", json("DELETE"));
        } catch (error) {
          // An already expired session is also a completed sign-out.
          if (!(error instanceof ApiError && error.status === 401)) throw error;
        }
        setUser(null);
      } finally {
        pendingSignOut.current = null;
        setSigningOut(false);
      }
    })();
    return pendingSignOut.current;
  }, [setUser]);
  const reload = useCallback(() => {
    const expectedGeneration = currentSession.current.generation;
    setLoading(true);
    setError(null);
    api<User>("auth/session")
      .then(user => {
        if (currentSession.current.generation !== expectedGeneration) return;
        setLoading(false);
        publishUser(user, expectedGeneration);
      })
      .catch((e) => {
        if (currentSession.current.generation !== expectedGeneration) return;
        if (!(e instanceof ApiError && e.status === 401)) setError(e);
        setLoading(false);
        publishUser(null, expectedGeneration);
      });
  }, [publishUser]);
  useEffect(() => {
    reload();
    const expire = () => publishUser(null, currentSession.current.generation);
    window.addEventListener("session-expired", expire);
    return () => window.removeEventListener("session-expired", expire);
  }, [reload, publishUser]);
  return (
    <Context.Provider value={{ user, avatarRevision, loading, error, setUser, updateUser, reload, signOut, signingOut }}>
      {children}
    </Context.Provider>
  );
}
export const useSession = () => useContext(Context);
