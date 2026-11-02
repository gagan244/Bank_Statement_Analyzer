"use client";

import { LoaderCircle, CheckCircle2, AlertCircle } from "lucide-react";

type Props = { status: "idle" | "processing" | "completed" | "error"; message: string; filename?: string };

export default function ProcessingStatus({ status, message, filename }: Props) {
  if (status === "idle") return null;
  const Icon = status === "processing" ? LoaderCircle : status === "completed" ? CheckCircle2 : AlertCircle;
  const color = status === "error" ? "text-red-600" : status === "completed" ? "text-green-700" : "text-blue-700";
  return (
    <section className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-4" aria-live="polite">
      <Icon className={`mt-0.5 ${color}`} size={20} />
      <div>
        <p className={`text-sm font-medium ${color}`}>{message}</p>
        {filename && <p className="mt-1 break-all text-xs text-slate-500">{filename}</p>}
      </div>
    </section>
  );
}
