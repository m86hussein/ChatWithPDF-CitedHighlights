"use client";

import { useEffect, useMemo, useState } from "react";
import { api, Stat } from "@/lib/api";

type SortKey = "page" | "metric" | "numeric_value" | "category";
const PAGE_SIZE = 100;

export default function StatsTable({ docId, activeId, onSelect }: { docId: string; activeId: string | null; onSelect: (s: Stat) => void }) {
  const [rows, setRows] = useState<Stat[] | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [source, setSource] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "page", dir: 1 });
  const [page, setPage] = useState(0);

  useEffect(() => {
    api<Stat[]>(`/documents/${docId}/statistics`).then(setRows);
  }, [docId]);

  const categories = useMemo(() => [...new Set((rows ?? []).map((r) => r.category).filter(Boolean))].sort(), [rows]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = (rows ?? []).filter(
      (r) =>
        (!category || r.category === category) &&
        (!source || r.source === source) &&
        (!q || `${r.metric} ${r.value} ${r.context} ${r.unit}`.toLowerCase().includes(q)),
    );
    const { key, dir } = sort;
    out.sort((a, b) => {
      const x = a[key], y = b[key];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
    });
    return out;
  }, [rows, query, category, source, sort]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const view = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const header = (key: SortKey, label: string, cls = "") => (
    <th className={`cursor-pointer select-none px-2 py-2 font-medium hover:text-slate-900 ${cls}`}
      onClick={() => { setPage(0); setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : 1 })); }}>
      {label}{sort.key === key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
    </th>
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white p-3">
        <input className="input max-w-[14rem] flex-1 !py-1.5" placeholder="Search… / بحث" dir="auto" value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} />
        <select className="input !w-auto !py-1.5" value={category} onChange={(e) => { setCategory(e.target.value); setPage(0); }}>
          <option value="">All categories</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className="input !w-auto !py-1.5" value={source} onChange={(e) => { setSource(e.target.value); setPage(0); }}>
          <option value="">All sources</option>
          <option value="text">Text</option>
          <option value="table">Table</option>
          <option value="chart">Chart</option>
        </select>
        <div className="ml-auto flex gap-1">
          <a href={`/api/documents/${docId}/statistics/export?format=xlsx`} className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700">Excel</a>
          <a href={`/api/documents/${docId}/statistics/export?format=csv`} className="rounded-md bg-white px-3 py-1.5 text-xs font-medium ring-1 ring-slate-300 hover:bg-slate-50">CSV</a>
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {rows === null ? (
          <p className="p-4 text-sm text-slate-400">Loading…</p>
        ) : filtered.length === 0 ? (
          <p className="p-4 text-sm text-slate-400">No statistics match.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-slate-50 text-left text-xs text-slate-500 shadow-[0_1px_0_#e2e8f0]">
              <tr>
                {header("metric", "Metric")}
                {header("numeric_value", "Value", "text-right")}
                {header("category", "Category")}
                {header("page", "Page", "text-right")}
              </tr>
            </thead>
            <tbody>
              {view.map((r) => (
                <tr key={r.id} onClick={() => onSelect(r)} title={r.context}
                  className={`cursor-pointer border-b border-slate-100 align-top hover:bg-yellow-50 ${activeId === r.id ? "bg-yellow-100" : ""}`}>
                  <td className="px-2 py-1.5" dir="auto">
                    <div className="line-clamp-2">{r.metric || <span className="text-slate-400">{r.context}</span>}</div>
                    {r.source !== "text" && <span className="text-[10px] uppercase text-slate-400">{r.source}</span>}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right font-medium tabular-nums" dir="auto">
                    {r.value}{r.unit && !r.value.includes(r.unit) ? ` ${r.unit}` : ""}
                  </td>
                  <td className="px-2 py-1.5 text-xs text-slate-500">{r.category}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{r.page}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-500">
        <span>{filtered.length} of {rows?.length ?? 0} values</span>
        {pages > 1 && (
          <div className="flex items-center gap-2">
            <button disabled={page === 0} onClick={() => setPage(page - 1)} className="rounded px-2 py-0.5 hover:bg-slate-100 disabled:opacity-40">‹</button>
            {page + 1} / {pages}
            <button disabled={page >= pages - 1} onClick={() => setPage(page + 1)} className="rounded px-2 py-0.5 hover:bg-slate-100 disabled:opacity-40">›</button>
          </div>
        )}
      </div>
    </div>
  );
}
