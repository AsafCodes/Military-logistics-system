
import { useState } from 'react';
import api from '@/api';

export default function ExportControls() {
    const [loading, setLoading] = useState<string | null>(null);

    const handleExport = async (format: 'pdf' | 'excel') => {
        setLoading(format);
        try {
            const response = await api.get('/reports/export/daily_activity', {
                params: { file_format: format },
                responseType: 'blob', // Critical for binary data
            });

            // Create blob URL
            const blob = new Blob([response.data], {
                type: format === 'pdf' ? 'application/pdf' : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            });
            const url = window.URL.createObjectURL(blob);

            // Extract filename from header or default
            let filename = `Operational_Report.${format === 'pdf' ? 'pdf' : 'xlsx'}`;
            const disposition = response.headers['content-disposition'];
            if (disposition && disposition.indexOf('attachment') !== -1) {
                const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
                const matches = filenameRegex.exec(disposition);
                if (matches != null && matches[1]) {
                    filename = matches[1].replace(/['"]/g, '');
                }
            }

            // Trigger download
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', filename);
            document.body.appendChild(link);
            link.click();

            // Cleanup
            link.remove();
            window.URL.revokeObjectURL(url);

        } catch (error) {
            console.error("Export failed:", error);
            alert("Failed to export report. Please try again.");
        } finally {
            setLoading(null);
        }
    };

    return (
        <div className="flex gap-2">
            <button
                onClick={() => handleExport('pdf')}
                disabled={!!loading}
                className="text-sm bg-red-50 text-red-700 hover:bg-red-100 px-3 py-1.5 rounded border border-red-200 font-medium flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                title="Download Official Report"
            >
                {loading === 'pdf' ? (
                    <span className="animate-spin h-4 w-4 border-2 border-red-600 border-t-transparent rounded-full px-1"></span>
                ) : (
                    <span>📄</span>
                )}
                <span>PDF Report</span>
            </button>
            <button
                onClick={() => handleExport('excel')}
                disabled={!!loading}
                className="text-sm bg-green-50 text-green-700 hover:bg-green-100 px-3 py-1.5 rounded border border-green-200 font-medium flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                title="Download Raw Data"
            >
                {loading === 'excel' ? (
                    <span className="animate-spin h-4 w-4 border-2 border-green-600 border-t-transparent rounded-full"></span>
                ) : (
                    <span>📊</span>
                )}
                <span>Excel Data</span>
            </button>
        </div>
    );
}
