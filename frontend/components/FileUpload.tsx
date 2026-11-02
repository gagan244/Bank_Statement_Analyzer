"use client";

import { ChangeEvent, useRef, useState } from "react";
import { FileUp } from "lucide-react";

type Props = { disabled: boolean; onUpload: (file: File) => void };

export default function FileUpload({ disabled, onUpload }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");

  function selectFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setError("");
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf") || (file.type && file.type !== "application/pdf")) {
      setError("Please select a PDF file.");
      event.target.value = "";
      return;
    }
    if (file.size > 15 * 1024 * 1024) {
      setError("File size must be 15 MB or less.");
      event.target.value = "";
      return;
    }
    onUpload(file);
    event.target.value = "";
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <h2 className="mb-3 text-base font-semibold text-slate-900">Bank statement upload</h2>
      <input ref={inputRef} type="file" accept="application/pdf,.pdf" onChange={selectFile} className="hidden" />
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-slate-300 px-4 py-8 text-sm font-medium text-slate-700 hover:border-blue-500 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <FileUp size={19} /> Select PDF statement (maximum 15 MB)
      </button>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </section>
  );
}
