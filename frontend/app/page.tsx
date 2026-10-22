"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, Doc, formatBytes, uploadFiles } from "@/lib/api";
import { logout, useMe } from "@/lib/auth";
import { Logo } from "@/components/Logo";

export default function Home() {
  const me = useMe();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [drag, setDrag] = useState(false);
  const [upload, setUpload] = useState<{ pct: number; names: string } | null>(null);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => api<Doc[]>("/documents").then(setDocs).catch((e) => setError(e.message)), []);

  useEffect(() => {
    load();
  }, [load]);

  const busy = docs?.some((d) => d.status === "queued" || d.status === "processing");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [busy, load]);

  async function send(list: FileList | File[]) {
    const files = Array.from(list).filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    if (!files.length) return setError("Please choose PDF files.");
    setError("");
    setUpload({ pct: 0, names: files.map((f) => f.name).join(", ") });
    try {
      await uploadFiles(files, (pct) => setUpload((u) => (u ? { ...u, pct } : u)));
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUpload(null);
    }
  }

  async function remove(d: Doc) {
    if (!confirm(`Delete "${d.filename}" and its chat history?`)) return;
    await api(`/documents/${d.id}`, { method: "DELETE" }).catch((e) => setError(e.message));
    load();
  }

  async function reprocess(d: Doc) {
    await api(`/documents/${d.id}/reprocess`, { method: "POST" }).catch((e) => setError(e.message));
    load();
  }

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <div className="flex items-center gap-2">
          <Logo />
          <span className="font-semibold">PDF Insight</span>
        </div>
        <div className="flex items-center gap-4 text-sm text-slate-600">
          <span className="hidden sm:inline">{me?.email}</span>
          <button onClick={logout} className="rounded-md px-3 py-1.5 hover:bg-slate-100">Sign out</button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 space-y-6 p-4 sm:p-8">
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files); }}
          onClick={() => input.current?.click()}
          className={`cursor-pointer rounded-2xl border-2 border-dashed p-10 text-center transition ${
            drag ? "border-blue-500 bg-blue-50" : "border-slate-300 bg-white hover:border-blue-400"}`}
        >
          <input ref={input} type="file" accept="application/pdf,.pdf" multiple hidden
            onChange={(e) => { if (e.target.files) send(e.target.files); e.target.value = ""; }} />
          <svg viewBox="0 0 24 24" className="mx-auto mb-3 h-10 w-10 text-blue-500" fill="none" stroke="currentColor" strokeWidth="1.6">
            <path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
          </svg>
          <p className="font-medium">Drag & drop PDFs here, or click to browse</p>
          <p className="font-ar mt-1 text-sm text-slate-500" dir="rtl">اسحب ملفات PDF وأفلتها هنا</p>
          <p className="mt-2 text-xs text-slate-400">Multiple files · long & scanned documents (OCR) · Arabic and English</p>
          {upload && (
            <div className="mx-auto mt-4 max-w-md text-left" onClick={(e) => e.stopPropagation()}>
              <p className="truncate text-xs text-slate-500">Uploading {upload.names}</p>
              <div className="mt-1 h-2 rounded bg-slate-100">
                <div className="h-2 rounded bg-blue-500 transition-all" style={{ width: `${upload.pct * 100}%` }} />
              </div>
            </div>
          )}
        </div>

        {error && <p className="rounded-lg bg-rose-50 px-4 py-2 text-sm text-rose-700">{error}</p>}

        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Your documents</h2>
          {docs === null ? (
            <p className="text-sm text-slate-400">Loading…</p>
          ) : docs.length === 0 ? (
            <p className="text-sm text-slate-400">No documents yet.</p>
          ) : (
            <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl bg-white ring-1 ring-slate-200">
              {docs.map((d) => (
                <li key={d.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center">
                  <div className="min-w-0 flex-1">
                    {d.status === "ready" ? (
                      <Link href={`/documents/${d.id}`} className="block truncate font-medium text-blue-700 hover:underline" dir="auto">
                        {d.filename}
                      </Link>
                    ) : (
                      <span className="block truncate font-medium" dir="auto">{d.filename}</span>
                    )}
                    <p className="text-xs text-slate-500">
                      {formatBytes(d.size_bytes)}
                      {d.page_count ? ` · ${d.page_count} pages` : ""}
                      {d.ocr_pages ? ` · ${d.ocr_pages} OCR` : ""}
                      {d.language ? ` · ${d.language === "ar" ? "Arabic" : "English"}` : ""}
                      {` · ${new Date(d.created_at + (d.created_at.endsWith("Z") || d.created_at.includes("+") ? "" : "Z")).toLocaleString()}`}
                    </p>
                    {(d.status === "processing" || d.status === "queued") && (
                      <div className="mt-2">
                        <div className="h-1.5 rounded bg-slate-100">
                          <div className="h-1.5 rounded bg-blue-500 transition-all" style={{ width: `${Math.max(3, d.progress * 100)}%` }} />
                        </div>
                        <p className="mt-1 text-xs text-slate-500">{d.stage || "Queued"}</p>
                      </div>
                    )}
                    {d.status === "failed" && <p className="mt-1 text-xs text-rose-600">Failed: {d.error}</p>}
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusPill status={d.status} />
                    {d.status === "ready" && (
                      <Link href={`/documents/${d.id}`} className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">Open</Link>
                    )}
                    {d.status === "failed" && (
                      <button onClick={() => reprocess(d)} className="rounded-md px-3 py-1.5 text-sm hover:bg-slate-100">Retry</button>
                    )}
                    {d.status !== "processing" && (
                      <button onClick={() => remove(d)} className="rounded-md px-3 py-1.5 text-sm text-rose-600 hover:bg-rose-50">Delete</button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  );
}

function StatusPill({ status }: { status: Doc["status"] }) {
  const s = {
    ready: "bg-emerald-50 text-emerald-700",
    processing: "bg-blue-50 text-blue-700",
    queued: "bg-slate-100 text-slate-600",
    failed: "bg-rose-50 text-rose-700",
  }[status];
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${s}`}>{status}</span>;
}
