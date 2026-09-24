import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fetchAccessStatus, signIn, signOut } from "./http";

export default function AccessGate({ children }) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState(null);
  const [authenticated, setAuthenticated] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    fetchAccessStatus().then((result) => {
      if (!active) return;
      setMode(result.mode);
      setAuthenticated(result.mode === "local");
    }).catch((err) => { if (active) setError(err.message); });
    function clearSession() {
      setAuthenticated(false);
      setPassword("");
      queryClient.clear();
    }
    window.addEventListener("celeste:signout", clearSession);
    return () => {
      active = false;
      window.removeEventListener("celeste:signout", clearSession);
    };
  }, [queryClient]);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(username, password);
      setPassword("");
      setAuthenticated(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (authenticated) return children(mode === "basic" ? signOut : null);

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-6 text-foreground-strong">
      <section className="w-full max-w-sm rounded-2xl border border-border bg-surface p-6">
        <h1 className="text-xl font-semibold">Celeste Intelligence</h1>
        {mode === "basic" ? (
          <form onSubmit={submit} className="mt-5 space-y-4">
            <p className="text-sm text-foreground-subtle">Sign in to view your business.</p>
            <label className="block text-sm">
              Username
              <input required autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)}
                className="mt-1 w-full rounded-lg border border-border bg-background p-2" />
            </label>
            <label className="block text-sm">
              Password
              <input required type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded-lg border border-border bg-background p-2" />
            </label>
            <button disabled={busy} className="w-full rounded-lg bg-accent px-4 py-2 font-medium text-background disabled:opacity-50">
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        ) : !error && <p className="mt-4 text-sm">Connecting…</p>}
        {error && <p role="alert" className="mt-4 text-sm text-critical">{error}</p>}
        {!mode && error && <button className="mt-4 text-sm text-accent" onClick={() => window.location.reload()}>Try again</button>}
      </section>
    </main>
  );
}
