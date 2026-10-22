"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import type { Links, Rect } from "@/lib/api";

export type Highlight = { id: string; page: number; rects: Rect[]; label?: string; title?: string; active?: boolean };
export type Focus = { page: number; rect?: Rect; nonce: number };

type Props = {
  url: string;
  pages: { w: number; h: number }[];
  highlights: Highlight[];
  focus: Focus | null;
  links?: Links;
  onHighlightClick?: (id: string) => void;
  onNavigate: (page: number, rects: Rect[], label: string) => void;
};

const GAP = 12;

export default function PdfViewer({ url, pages, highlights, focus, links, onHighlightClick, onNavigate }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const pageEls = useRef<(HTMLDivElement | null)[]>([]);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [visible, setVisible] = useState<Set<number>>(new Set([0]));
  const [current, setCurrent] = useState(1);

  useEffect(() => {
    let cancelled = false;
    let task: { destroy: () => Promise<void> } | null = null;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.mjs";
        const loading = pdfjs.getDocument({
          url, withCredentials: true, disableRange: true,
          cMapUrl: "/pdfjs/cmaps/", cMapPacked: true, standardFontDataUrl: "/pdfjs/standard_fonts/",
          wasmUrl: "/pdfjs/wasm/", iccUrl: "/pdfjs/iccs/",
        });
        task = loading;
        if (cancelled) return void loading.destroy();
        const doc = await loading.promise;
        if (!cancelled) setPdf(doc);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
      task?.destroy();
    };
  }, [url]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const maxW = useMemo(() => Math.max(1, ...pages.map((p) => p.w)), [pages]);
  const fitWidth = Math.max(200, width - 2 * GAP - 4);
  const sizeOf = useCallback(
    (i: number) => {
      const p = pages[i] ?? pages[0];
      const w = fitWidth * zoom * (p.w / maxW);
      return { w, h: (w * p.h) / p.w };
    },
    [pages, fitWidth, zoom, maxW],
  );

  useEffect(() => {
    const root = scroller.current;
    if (!root || !width) return;
    const io = new IntersectionObserver(
      (entries) => {
        setVisible((prev) => {
          const next = new Set(prev);
          for (const e of entries) {
            const i = Number((e.target as HTMLElement).dataset.index);
            if (e.isIntersecting) next.add(i);
            else next.delete(i);
          }
          return next;
        });
      },
      { root, rootMargin: "600px 0px" },
    );
    pageEls.current.forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, [pages.length, width]);

  const onScroll = useCallback(() => {
    const root = scroller.current;
    if (!root) return;
    const mid = root.scrollTop + root.clientHeight / 3;
    let page = 1;
    pageEls.current.forEach((el, i) => {
      if (el && el.offsetTop <= mid) page = i + 1;
    });
    setCurrent(page);
  }, []);

  const scrollToPage = useCallback((page: number, rect?: Rect) => {
    const root = scroller.current;
    const el = pageEls.current[page - 1];
    if (!root || !el) return;
    const y = rect ? rect[1] * el.clientHeight : 0;
    root.scrollTo({ top: Math.max(0, el.offsetTop + y - 120), behavior: "smooth" });
  }, []);

  useEffect(() => {
    if (focus && width) scrollToPage(focus.page, focus.rect);
  }, [focus, width, scrollToPage]);

  const anchorByKey = useMemo(() => new Map((links?.anchors ?? []).map((a) => [a.key, a])), [links]);

  return (
    <div className="flex h-full flex-col bg-slate-200/70">
      <div className="flex items-center justify-between gap-2 border-b border-slate-300 bg-white px-3 py-1.5 text-sm">
        <div className="flex items-center gap-1">
          <span className="text-slate-500">Page</span>
          <input
            className="w-12 rounded border border-slate-300 px-1 py-0.5 text-center"
            value={current}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (n >= 1 && n <= pages.length) {
                setCurrent(n);
                scrollToPage(n);
              }
            }}
          />
          <span className="text-slate-500">/ {pages.length}</span>
        </div>
        <div className="flex items-center gap-1">
          <button className="rounded px-2 py-0.5 hover:bg-slate-100" onClick={() => setZoom((z) => Math.max(0.4, z - 0.15))} aria-label="Zoom out">−</button>
          <button className="rounded px-2 py-0.5 text-xs hover:bg-slate-100" onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
          <button className="rounded px-2 py-0.5 hover:bg-slate-100" onClick={() => setZoom((z) => Math.min(3, z + 0.15))} aria-label="Zoom in">+</button>
        </div>
      </div>
      <div ref={scroller} onScroll={onScroll} className="relative flex-1 overflow-auto" style={{ padding: GAP }}>
        {error && <p className="p-4 text-sm text-rose-600">Could not load PDF: {error}</p>}
        {width > 0 &&
          pages.map((_, i) => {
            const { w, h } = sizeOf(i);
            const pageNo = i + 1;
            return (
              <div
                key={i}
                data-index={i}
                ref={(el) => { pageEls.current[i] = el; }}
                className="relative mx-auto bg-white shadow-md"
                style={{ width: w, height: h, marginBottom: GAP }}
              >
                {pdf && visible.has(i) && <PageCanvas pdf={pdf} pageNo={pageNo} width={w} height={h} />}
                {links?.xrefs
                  .filter((x) => x.page === pageNo)
                  .map((x, k) => {
                    const a = anchorByKey.get(x.key);
                    if (!a) return null;
                    return x.rects.map((r, j) => (
                      <div
                        key={`x${k}-${j}`}
                        className="pdf-xref"
                        title={`Go to ${a.label} (p. ${a.page})`}
                        style={box(r)}
                        onClick={() => onNavigate(a.page, a.rects, a.label)}
                      />
                    ));
                  })}
                {highlights
                  .filter((hl) => hl.page === pageNo)
                  .map((hl) =>
                    hl.rects.map((r, j) => (
                      <div key={`${hl.id}-${j}`} className={`pdf-hl ${hl.active ? "active" : ""}`} style={box(r)}
                        title={hl.title ?? hl.label} onClick={() => onHighlightClick?.(hl.id)}>
                        {j === 0 && hl.label && <span className="pdf-badge" style={{ left: 0, top: 0 }}>{hl.label}</span>}
                      </div>
                    )),
                  )}
                <span className="absolute -left-1 top-1 -translate-x-full text-[10px] text-slate-500">{pageNo}</span>
              </div>
            );
          })}
      </div>
    </div>
  );
}

function box(r: Rect): React.CSSProperties {
  const pad = 0.002;
  return {
    left: `${(r[0] - pad) * 100}%`,
    top: `${(r[1] - pad) * 100}%`,
    width: `${(r[2] - r[0] + 2 * pad) * 100}%`,
    height: `${(r[3] - r[1] + 2 * pad) * 100}%`,
  };
}

const PageCanvas = memo(function PageCanvas({ pdf, pageNo, width, height }: { pdf: PDFDocumentProxy; pageNo: number; width: number; height: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let task: RenderTask | null = null;
    let cancelled = false;
    const timer = setTimeout(async () => {
      const page = await pdf.getPage(pageNo);
      if (cancelled || !canvas.current) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: (width / base.width) * dpr });
      const c = canvas.current;
      c.width = Math.floor(viewport.width);
      c.height = Math.floor(viewport.height);
      task = page.render({ canvas: c, viewport });
      task.promise.catch(() => {});
    }, 80);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      task?.cancel();
    };
  }, [pdf, pageNo, width]);

  return <canvas ref={canvas} className="absolute inset-0" style={{ width, height }} />;
});
