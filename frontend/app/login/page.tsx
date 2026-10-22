"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import { Logo } from "@/components/Logo";

export default function LoginPage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/auth/${mode}`, { method: "POST", body: JSON.stringify({ email, password, name }) });
      location.href = "/";
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200">
        <div className="mb-6 flex items-center gap-2">
          <Logo />
          <h1 className="text-xl font-semibold">PDF Insight</h1>
        </div>
        <p className="mb-6 text-sm text-slate-500">
          Chat with your documents, extract every statistic and verify each answer against the source.
          <span className="font-ar mt-1 block" dir="rtl">حاور مستنداتك واستخرج الإحصاءات مع الإسناد للمصدر.</span>
        </p>
        <form onSubmit={submit} className="space-y-3">
          {mode === "register" && (
            <input className="input" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
          )}
          <input className="input" type="email" required autoComplete="email" placeholder="Email"
            value={email} onChange={(e) => setEmail(e.target.value)} />
          <input className="input" type="password" required minLength={8} placeholder="Password (min 8 characters)"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password} onChange={(e) => setPassword(e.target.value)} />
          {error && <p className="text-sm text-rose-600">{error}</p>}
          <button disabled={busy} className="w-full rounded-lg bg-blue-600 py-2.5 font-medium text-white hover:bg-blue-700 disabled:opacity-60">
            {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <button className="mt-4 w-full text-sm text-slate-500 hover:text-slate-800"
          onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
          {mode === "login" ? "No account? Create one" : "Have an account? Sign in"}
        </button>
      </div>
    </main>
  );
}
