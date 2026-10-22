"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { AnswerPayload, api, Citation, levelStyle, Message, Rect } from "@/lib/api";

type Props = {
  docId: string;
  language: string;
  activeMessage: string | null;
  activeCitation: number | null;
  onShowAnswer: (msg: Message) => void;
  onCitation: (msg: Message, c: Citation) => void;
  onNavigate: (page: number, rects: Rect[], label: string) => void;
};

const SUGGESTIONS = {
  en: ["Summarise the key figures in this document", "What are the main requirements?", "What deadlines or durations are mentioned?"],
  ar: ["لخص أهم الأرقام في هذا المستند", "ما هي المتطلبات الرئيسية؟", "ما هي المدد والمواعيد المذكورة؟"],
};

export default function ChatPanel({ docId, language, activeMessage, activeCitation, onShowAnswer, onCitation, onNavigate }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<Message[]>(`/documents/${docId}/messages`).then((m) => {
      setMessages(m);
      const last = [...m].reverse().find((x) => x.role === "assistant");
      if (last) onShowAnswer(last);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, busy]);

  async function ask(question: string) {
    question = question.trim();
    if (!question || busy) return;
    setQ("");
    setError("");
    setBusy(true);
    const temp: Message = { id: `tmp-${messages.length}`, role: "user", content: question, payload: {}, created_at: "" };
    setMessages((m) => [...m, temp]);
    try {
      const reply = await api<Message>(`/documents/${docId}/chat`, { method: "POST", body: JSON.stringify({ question }) });
      setMessages((m) => [...m, reply]);
      onShowAnswer(reply);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    if (!confirm("Clear the chat history for this document?")) return;
    await api(`/documents/${docId}/messages`, { method: "DELETE" });
    setMessages([]);
  }

  const submit = (e: FormEvent) => {
    e.preventDefault();
    ask(q);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && !busy && (
          <div className="mt-6 space-y-3 text-center">
            <p className="text-sm text-slate-500">Ask anything about this document. Answers come only from the PDF, with clickable sources.</p>
            <div className="flex flex-wrap justify-center gap-2">
              {[...SUGGESTIONS[language === "ar" ? "ar" : "en"], ...SUGGESTIONS[language === "ar" ? "en" : "ar"].slice(0, 1)].map((s) => (
                <button key={s} dir="auto" onClick={() => ask(s)}
                  className="rounded-full bg-white px-3 py-1.5 text-sm text-slate-700 ring-1 ring-slate-200 hover:ring-blue-400">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <div dir="auto" className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-blue-600 px-4 py-2 text-sm text-white">
                {m.content}
              </div>
            </div>
          ) : (
            <Answer key={m.id} msg={m} active={activeMessage === m.id} activeCitation={activeMessage === m.id ? activeCitation : null}
              onShow={() => onShowAnswer(m)} onCitation={(c) => onCitation(m, c)} onNavigate={onNavigate} />
          ),
        )}
        {busy && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <span className="h-2 w-2 animate-ping rounded-full bg-blue-500" />
            Searching the document and verifying sources…
          </div>
        )}
        {error && <p className="rounded bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
        <div ref={end} />
      </div>
      <form onSubmit={submit} className="border-t border-slate-200 bg-white p-3">
        <div className="flex items-end gap-2">
          <textarea
            dir="auto"
            rows={2}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                ask(q);
              }
            }}
            placeholder="Ask a question in English or Arabic… / اكتب سؤالك"
            className="input resize-none"
          />
          <button disabled={busy || !q.trim()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            Ask
          </button>
        </div>
        {messages.length > 0 && (
          <button type="button" onClick={clear} className="mt-1 text-xs text-slate-400 hover:text-slate-600">Clear history</button>
        )}
      </form>
    </div>
  );
}

function Answer({ msg, active, activeCitation, onShow, onCitation, onNavigate }: {
  msg: Message; active: boolean; activeCitation: number | null;
  onShow: () => void; onCitation: (c: Citation) => void; onNavigate: Props["onNavigate"];
}) {
  const p = msg.payload as AnswerPayload;
  if (!p?.statements) return null;
  const byN = new Map(p.citations.map((c) => [c.n, c]));
  const rtl = /[\u0600-\u06FF]/.test(msg.content.slice(0, 200));
  const ar = rtl;

  return (
    <div className={`rounded-2xl rounded-bl-sm bg-white p-4 text-sm shadow-sm ring-1 ${active ? "ring-blue-300" : "ring-slate-200"}`}>
      <div dir={rtl ? "rtl" : "ltr"} className="space-y-2 leading-relaxed">
        {p.statements.map((s, i) => (
          <p key={i}>
            {s.text}{" "}
            {s.citations.map((n) => {
              const c = byN.get(n);
              if (!c) return null;
              return (
                <button key={n} onClick={() => onCitation(c)} title={`p. ${c.page} · ${Math.round(c.score * 100)}% match`}
                  className={`mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded px-1 align-baseline text-[11px] font-semibold ring-1 ${levelStyle[c.level].chip} ${
                    activeCitation === n ? "outline-2 outline-blue-500" : ""}`}>
                  {n}
                </button>
              );
            })}
          </p>
        ))}
        {!p.answer_found && (
          <p className="text-xs text-slate-400">{ar ? "لم يتم العثور على إجابة في المستند." : "No supporting passage found in the document."}</p>
        )}
      </div>

      {p.citations.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">{ar ? "المصادر" : "Sources"}</span>
            {!active && <button onClick={onShow} className="text-xs text-blue-600 hover:underline">Highlight all in PDF</button>}
          </div>
          <ol className="space-y-2">
            {p.citations.map((c) => (
              <li key={c.n}>
                <button onClick={() => onCitation(c)}
                  className={`w-full rounded-lg p-2 text-left ring-1 transition hover:bg-yellow-50 ${activeCitation === c.n ? "bg-yellow-50 ring-yellow-300" : "ring-slate-100"}`}>
                  <div className="mb-1 flex items-center gap-2 text-xs">
                    <span className={`inline-flex h-5 min-w-5 items-center justify-center rounded px-1 font-semibold ring-1 ${levelStyle[c.level].chip}`}>{c.n}</span>
                    <span className="text-slate-500">{ar ? "صفحة" : "Page"} {c.page}{c.kind === "chart" ? (ar ? " · شكل" : " · figure") : ""}</span>
                    <span className={`ml-auto rounded-full px-2 py-0.5 font-medium ring-1 ${levelStyle[c.level].chip}`}>
                      {Math.round(c.score * 100)}% · {ar ? levelStyle[c.level].labelAr : levelStyle[c.level].label}
                    </span>
                  </div>
                  <p dir="auto" className="line-clamp-3 text-xs text-slate-600">
                    <mark className="bg-yellow-200/70 px-0.5">{c.quote}</mark>
                  </p>
                  {c.numbers_missing?.length ? (
                    <p className="mt-1 text-[11px] text-rose-600">Number(s) not found in source: {c.numbers_missing.join(", ")}</p>
                  ) : null}
                </button>
                {c.xrefs.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1 pl-2">
                    {c.xrefs.map((x) => (
                      <button key={x.key} dir="auto" onClick={() => onNavigate(x.page, x.rects, x.label)}
                        className="rounded-full bg-blue-50 px-2 py-0.5 text-[11px] text-blue-700 ring-1 ring-blue-200 hover:bg-blue-100">
                        ↪ {x.label.length > 50 ? x.label.slice(0, 50) + "…" : x.label} · p.{x.page}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
