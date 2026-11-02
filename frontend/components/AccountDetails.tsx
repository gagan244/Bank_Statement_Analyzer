import { StatementResults } from "@/services/api";

type Props = { details: StatementResults["account_details"]; pdfType: string };

export default function AccountDetails({ details, pdfType }: Props) {
  const fields = [
    ["Account holder", details.account_holder_name],
    ["Account number", details.account_number],
    ["IFSC code", details.ifsc_code],
    ["PDF type", pdfType === "text" ? "Text-based" : "Scanned / image-based"]
  ];
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <h2 className="mb-4 text-base font-semibold text-slate-900">Extracted account details</h2>
      <dl className="grid gap-4 sm:grid-cols-2">
        {fields.map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
            <dd className="mt-1 break-words text-sm text-slate-900">{value || "Not detected"}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
