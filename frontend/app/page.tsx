"use client";

import { useState } from "react";
import { Landmark } from "lucide-react";
import FileUpload from "@/components/FileUpload";
import ProcessingStatus from "@/components/ProcessingStatus";
import AccountDetails from "@/components/AccountDetails";
import TransactionTable from "@/components/TransactionTable";
import ExportButtons from "@/components/ExportButtons";
import { getResults, StatementResults, uploadStatement } from "@/services/api";

type Status = "idle" | "processing" | "completed" | "error";

export default function HomePage() {
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");
  const [filename, setFilename] = useState("");
  const [results, setResults] = useState<StatementResults | null>(null);

  async function handleUpload(file: File) {
    setFilename(file.name);
    setResults(null);
    setStatus("processing");
    setMessage("Uploading PDF and processing statement. This may take a little while.");
    try {
      const uploaded = await uploadStatement(file);
      const actualResults = await getResults(uploaded.id);
      setResults(actualResults);
      setStatus("completed");
      setMessage(uploaded.message);
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "An unexpected processing error occurred.");
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-6xl px-4 py-8 sm:px-6">
      <header className="mb-7 flex items-center gap-3">
        <div className="rounded-xl bg-blue-700 p-2.5 text-white"><Landmark size={23} /></div>
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-950">Bank Statement Analyzer</h1>
          <p className="mt-1 text-sm text-slate-500">Extract and classify statement transactions</p>
        </div>
      </header>

      <div className="space-y-5">
        <FileUpload disabled={status === "processing"} onUpload={handleUpload} />
        <ProcessingStatus status={status} message={message} filename={filename} />

        {results && (
          <>
            <AccountDetails details={results.account_details} pdfType={results.pdf_type} />
            <TransactionTable transactions={results.transactions} />
            <ExportButtons statementId={results.id} />
          </>
        )}
      </div>
      <footer className="mt-10 border-t border-slate-200 pt-4 text-xs text-slate-500">
        Statement files are processed by the local Python backend. Extracted details may be incomplete when source PDFs are unclear.
      </footer>
    </main>
  );
}
