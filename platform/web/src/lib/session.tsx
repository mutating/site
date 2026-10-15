import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError } from "./api";
import type { User } from "./types";
const Context = createContext<{
  user: User | null;
  loading: boolean;
  error: unknown;
  setUser: (u: User | null) => void;
  reload: () => void;
}>(null!);
export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    api<User>("auth/session")
      .then(setUser)
      .catch((e) => {
        if (!(e instanceof ApiError && e.status === 401)) setError(e);
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    reload();
    const expire = () => setUser(null);
    window.addEventListener("session-expired", expire);
    return () => window.removeEventListener("session-expired", expire);
  }, [reload]);
  return (
    <Context.Provider value={{ user, loading, error, setUser, reload }}>
      {children}
    </Context.Provider>
  );
}
export const useSession = () => useContext(Context);
