"use client";

import { useMemo, useState } from "react";
import { Transaction } from "@/services/api";

type Props = { transactions: Transaction[] };

function display(value: string | number | null | undefined) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

export default function TransactionTable({ transactions }: Props) {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const categories = useMemo(() => Array.from(new Set(transactions.map(t => t.category || "Others"))).sort(), [transactions]);
  const filtered = useMemo(() => transactions.filter(t => {
    const text = `${t.date ?? ""} ${t.description ?? ""} ${t.category ?? ""}`.toLowerCase();
    return text.includes(search.toLowerCase()) && (category === "all" || (t.category || "Others") === category);
  }), [transactions, search, category]);

  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="flex flex-col gap-3 border-b border-slate-200 p-5 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-base font-semibold text-slate-900">Transaction results ({transactions.length})</h2>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search transactions" className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500" />
          <select value={category} onChange={e => setCategory(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500">
            <option value="all">All categories</option>
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      </div>
      {filtered.length === 0 ? (
        <p className="p-6 text-sm text-slate-500">No transactions match the current filter.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-[850px] w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>{["Date", "Description", "Debit", "Credit", "Balance", "Category"].map(h => <th key={h} className="px-4 py-3 font-semibold">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filtered.map((t, i) => (
                <tr key={`${t.date}-${i}`} className="hover:bg-slate-50">
                  <td className="whitespace-nowrap px-4 py-3 text-slate-600">{display(t.date)}</td>
                  <td className="max-w-xs px-4 py-3 text-slate-900">{display(t.description)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-700">{display(t.debit_amount)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-700">{display(t.credit_amount)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-700">{display(t.balance)}</td>
                  <td className="whitespace-nowrap px-4 py-3"><span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700">{display(t.category)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
