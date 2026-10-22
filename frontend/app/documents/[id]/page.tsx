"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { api, AnswerPayload, Citation, Doc, Links, Message, Rect, Stat } from "@/lib/api";
import PdfViewer, { Focus, Highlight } from "@/components/PdfViewer";
import ChatPanel from "@/components/ChatPanel";
import StatsTable from "@/components/StatsTable";
import { Logo } from "@/components/Logo";

function useNarrow(px: number) {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${px}px)`);
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [px]);
  return narrow;
}

export default function DocumentWorkspace() {
  const { id } = useParams<{ id: string }>();
  const [doc, setDoc] = useState<Doc | null>(null);
  const [links, setLinks] = useState<Links>();
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"chat" | "stats">("chat");
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [activeMessage, setActiveMessage] = useState<string | null>(null);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const [activeStat, setActiveStat] = useState<string | null>(null);
  const narrow = useNarrow(900);

  useEffect(() => {
    api<Doc>(`/documents/${id}`).then(setDoc).catch((e) => setError(e.message));
    api<Links>(`/documents/${id}/links`).then(setLinks).catch(() => {});
  }, [id]);

  const goTo = useCallback((page: number, rect?: Rect) => setFocus({ page, rect, nonce: Date.now() }), []);

  const citationHighlights = (msg: Message, active: number | null): Highlight[] =>
    ((msg.payload as AnswerPayload).citations ?? []).map((c) => ({
      id: `c${c.n}`, page: c.page, rects: c.rects, label: String(c.n), active: c.n === active,
    }));

  const showAnswer = useCallback((msg: Message) => {
    const cites = (msg.payload as AnswerPayload).citations ?? [];
    setActiveMessage(msg.id);
    setActiveCitation(null);
    setActiveStat(null);
    setHighlights(citationHighlights(msg, null));
    if (cites[0]) goTo(cites[0].page, cites[0].rects[0]);
  }, [goTo]);

  const showCitation = useCallback((msg: Message, c: Citation) => {
    setActiveMessage(msg.id);
    setActiveCitation(c.n);
    setActiveStat(null);
    setHighlights(citationHighlights(msg, c.n));
    goTo(c.page, c.rects[0]);
  }, [goTo]);

  const navigate = useCallback((page: number, rects: Rect[], label: string) => {
    setHighlights((h) => [...h.filter((x) => x.id !== "nav").map((x) => ({ ...x, active: false })),
      { id: "nav", page, rects, label: "↪", title: label, active: true }]);
    goTo(page, rects[0]);
  }, [goTo]);

  const selectStat = useCallback((s: Stat) => {
    setActiveStat(s.id);
    setActiveMessage(null);
    setHighlights([{ id: s.id, page: s.page, rects: s.rects, active: true }]);
    goTo(s.page, s.rects[0]);
  }, [goTo]);

  const clickHighlight = useCallback((hid: string) => {
    if (hid.startsWith("c")) {
      const n = Number(hid.slice(1));
      setActiveCitation(n);
      setTab("chat");
      setHighlights((h) => h.map((x) => ({ ...x, active: x.id === hid })));
    }
  }, []);

  if (error) {
    return (
      <div className="p-8 text-sm">
        <p className="text-rose-600">{error}</p>
        <Link href="/" className="text-blue-600 hover:underline">Back to documents</Link>
      </div>
    );
  }
  if (!doc) return <p className="p-8 text-sm text-slate-400">Loading…</p>;

  const left = (
    <div className="flex h-full flex-col bg-slate-50">
      <div className="flex border-b border-slate-200 bg-white text-sm">
        {(["chat", "stats"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`flex-1 border-b-2 px-4 py-2 font-medium ${tab === t ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
            {t === "chat" ? "Chat · محادثة" : "Statistics · إحصاءات"}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        <div className={tab === "chat" ? "h-full" : "hidden"}>
          <ChatPanel docId={doc.id} language={doc.language} activeMessage={activeMessage} activeCitation={activeCitation}
            onShowAnswer={showAnswer} onCitation={showCitation} onNavigate={navigate} />
        </div>
        <div className={tab === "stats" ? "h-full" : "hidden"}>
          <StatsTable docId={doc.id} activeId={activeStat} onSelect={selectStat} />
        </div>
      </div>
    </div>
  );

  const viewer = (
    <PdfViewer url={`/api/documents/${doc.id}/file`} pages={doc.pages ?? []} highlights={highlights} focus={focus}
      links={links} onHighlightClick={clickHighlight} onNavigate={navigate} />
  );

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-2">
        <Link href="/" className="flex items-center gap-2 text-sm text-slate-500 hover:text-slate-900" aria-label="Back to documents">
          <Logo />
          <span className="hidden sm:inline">Documents</span>
        </Link>
        <span className="text-slate-300">/</span>
        <h1 className="min-w-0 flex-1 truncate text-sm font-medium" dir="auto">{doc.filename}</h1>
        <span className="hidden text-xs text-slate-500 md:inline">
          {doc.page_count} pages{doc.ocr_pages ? ` · ${doc.ocr_pages} OCR` : ""} · {doc.language === "ar" ? "Arabic" : "English"}
        </span>
      </header>
      <div className="min-h-0 flex-1">
        <Group key={narrow ? "v" : "h"} orientation={narrow ? "vertical" : "horizontal"} className="h-full">
          <Panel defaultSize={narrow ? "50" : "42"} minSize="25">{narrow ? viewer : left}</Panel>
          <Separator className={`split-handle ${narrow ? "vertical" : ""}`} />
          <Panel minSize="25">{narrow ? left : viewer}</Panel>
        </Group>
      </div>
    </div>
  );
}
