const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Transaction = {
  date?: string | null;
  description?: string | null;
  debit_amount?: number | string | null;
  credit_amount?: number | string | null;
  balance?: number | string | null;
  category?: string | null;
  classification_method?: string | null;
};

export type StatementResults = {
  id: string;
  filename: string;
  pdf_type: "text" | "image";
  classifier_mode: string;
  account_details: {
    account_holder_name?: string | null;
    account_number?: string | null;
    ifsc_code?: string | null;
  };
  transactions: Transaction[];
};

export async function uploadStatement(file: File): Promise<{ id: string; status: string; message: string }> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_URL}/api/statements/upload?classifier_mode=hybrid`, {
    method: "POST",
    body: form
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Upload or processing failed.");
  return data;
}

export async function getResults(id: string): Promise<StatementResults> {
  const response = await fetch(`${API_URL}/api/statements/${id}/results`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Could not load results.");
  return data;
}

export function exportUrl(id: string, format: "csv" | "excel"): string {
  return `${API_URL}/api/statements/${id}/export/${format}`;
}
