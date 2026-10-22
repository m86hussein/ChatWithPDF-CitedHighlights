export type Rect = [number, number, number, number];

export type User = { id: string; email: string; name: string };

export type Doc = {
  id: string;
  filename: string;
  size_bytes: number;
  page_count: number;
  status: "queued" | "processing" | "ready" | "failed";
  stage: string;
  progress: number;
  error: string;
  language: string;
  ocr_pages: number;
  created_at: string;
  pages?: { w: number; h: number; ocr: boolean }[];
};

export type Level = "high" | "medium" | "low" | "none";

export type XRefTarget = { key: string; label: string; page: number; rects: Rect[] };

export type Citation = {
  n: number;
  chunk_id: string;
  page: number;
  kind: "text" | "chart";
  quote: string;
  context: string;
  rects: Rect[];
  fidelity: number;
  support?: number;
  score: number;
  level: Level;
  numbers_missing?: string[];
  xrefs: XRefTarget[];
};

export type Statement = { text: string; citations: number[]; score?: number; level?: Level };

export type AnswerPayload = { answer_found: boolean; statements: Statement[]; citations: Citation[] };

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  payload: AnswerPayload | Record<string, never>;
  created_at: string;
};

export type Stat = {
  id: string;
  page: number;
  value: string;
  numeric_value: number | null;
  unit: string;
  metric: string;
  category: string;
  context: string;
  source: "text" | "table" | "chart";
  rects: Rect[];
};

export type Links = {
  anchors: { key: string; label: string; page: number; rects: Rect[] }[];
  xrefs: { key: string; text: string; page: number; rects: Rect[] }[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api${path}`, { ...init, headers, credentials: "same-origin" });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {}
    if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
      location.href = "/login";
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export async function uploadFiles(files: File[], onProgress: (pct: number) => void): Promise<Doc[]> {
  // One request per file keeps each body under the per-file limit and isolates failures.
  const total = files.reduce((s, f) => s + f.size, 0) || 1;
  let done = 0;
  const out: Doc[] = [];
  for (const f of files) {
    out.push(...(await uploadOne(f, (loaded) => onProgress((done + loaded) / total))));
    done += f.size;
  }
  return out;
}

function uploadOne(file: File, onLoaded: (bytes: number) => void): Promise<Doc[]> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("files", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/documents");
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => e.lengthComputable && onLoaded((e.loaded / e.total) * file.size);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
      else {
        let msg = xhr.statusText;
        try {
          msg = JSON.parse(xhr.responseText).detail ?? msg;
        } catch {}
        reject(new ApiError(xhr.status, `${file.name}: ${msg}`));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error"));
    xhr.send(form);
  });
}

export const levelStyle: Record<Level, { chip: string; label: string; labelAr: string }> = {
  high: { chip: "bg-emerald-100 text-emerald-800 ring-emerald-300", label: "High", labelAr: "عالية" },
  medium: { chip: "bg-amber-100 text-amber-800 ring-amber-300", label: "Medium", labelAr: "متوسطة" },
  low: { chip: "bg-rose-100 text-rose-800 ring-rose-300", label: "Low", labelAr: "منخفضة" },
  none: { chip: "bg-slate-100 text-slate-600 ring-slate-300", label: "—", labelAr: "—" },
};

export function formatBytes(n: number) {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
