import { Download, FileSpreadsheet, FileText } from "lucide-react";
import { exportUrl } from "@/services/api";

type Props = { statementId: string };

export default function ExportButtons({ statementId }: Props) {
  return (
    <section className="flex flex-wrap gap-3">
      <a href={exportUrl(statementId, "excel")} download className="inline-flex items-center gap-2 rounded-lg bg-blue-700 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-800">
        <FileSpreadsheet size={17} /> Download Excel <Download size={15} />
      </a>
      <a href={exportUrl(statementId, "csv")} download className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50">
        <FileText size={17} /> Download CSV <Download size={15} />
      </a>
    </section>
  );
}
