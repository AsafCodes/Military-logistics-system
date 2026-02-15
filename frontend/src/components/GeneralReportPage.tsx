
import { useState, useEffect } from 'react';
import api from '../api';

interface GeneralReportItem {
    item_type: string;
    unit_association: string;
    designated_owner: string;
    actual_location: string;
    serial_number: string;
    reporting_status: string; // "Reported" | "Late" | "Missing"
    last_reporter: string;
}

export default function GeneralReportPage() {
    // Filters
    const [battalion, setBattalion] = useState("");
    const [company, setCompany] = useState("");
    const [loading, setLoading] = useState(false);
    const [items, setItems] = useState<GeneralReportItem[]>([]);
    const [lastUpdated, setLastUpdated] = useState<string>("");

    const fetchReport = async () => {
        setLoading(true);
        try {
            // Build query params
            const params = new URLSearchParams();
            if (battalion) params.append("battalion_filter", battalion);
            if (company) params.append("company_filter", company);

            const res = await api.get(`/reports/query?${params.toString()}`);
            setItems(res.data);
            setLastUpdated(new Date().toLocaleString('he-IL'));
        } catch (err) {
            console.error("Failed to load report", err);
        } finally {
            setLoading(false);
        }
    };

    // Initial load
    useEffect(() => {
        fetchReport();
    }, []);

    const handlePrint = () => {
        window.print();
    };

    return (
        <div className="p-6 max-w-7xl mx-auto space-y-6">

            {/* Print Styles */}
            <style>{`
                @media print {
                    @page { size: landscape; margin: 10mm; }
                    body { background: white; -webkit-print-color-adjust: exact; }
                    .no-print { display: none !important; }
                    .print-only { display: block !important; }
                    .print-header { text-align: center; margin-bottom: 20px; border-bottom: 2px solid #000; padding-bottom: 10px; }
                    table { font-size: 10pt; width: 100%; border-collapse: collapse; }
                    th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: right; }
                    th { background-color: #f3f3f3 !important; font-weight: bold; }
                    tr.late-row { background-color: #ffeaea !important; }
                }
                .print-only { display: none; }
            `}</style>

            {/* Print Header (Visible only in print) */}
            <div className="print-only print-header">
                <h1 className="text-2xl font-bold">דוח מצאי יחידתי - {battalion || "כלל היחידה"} / {company || "כלל הפלוגות"}</h1>
                <p className="text-sm">נכון לתאריך: {lastUpdated}</p>
            </div>

            {/* Top Bar (Changes Control) */}
            <div className="bg-white p-4 rounded-xl shadow border border-gray-100 flex flex-wrap gap-4 items-end justify-between no-print" dir="rtl">
                <div className="flex gap-4 items-end flex-wrap">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">גדוד (סינון)</label>
                        <input
                            type="text"
                            value={battalion}
                            onChange={(e) => setBattalion(e.target.value)}
                            className="border rounded p-2 text-sm w-40"
                            placeholder="לדוגמה: 101"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">פעילות/פלוגה</label>
                        <input
                            type="text"
                            value={company}
                            onChange={(e) => setCompany(e.target.value)}
                            className="border rounded p-2 text-sm w-40"
                            placeholder="לדוגמה: מסייעת"
                        />
                    </div>
                    <button
                        onClick={fetchReport}
                        className="bg-blue-600 text-white px-4 py-2 rounded shadow hover:bg-blue-700 font-medium text-sm"
                    >
                        רענן נתונים
                    </button>
                </div>

                <button
                    onClick={handlePrint}
                    className="bg-slate-800 text-white px-6 py-2 rounded shadow hover:bg-slate-900 font-bold flex items-center gap-2"
                >
                    <span>🖨️</span> הדפס דוח
                </button>
            </div>

            {/* Table Content */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden" dir="rtl">
                {loading ? (
                    <div className="p-8 text-center text-gray-500">טוען נתונים...</div>
                ) : items.length === 0 ? (
                    <div className="p-8 text-center text-gray-500">לא נמצאו פריטים לתצוגה.</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-right text-sm">
                            <thead className="bg-gray-100 text-gray-700 font-bold">
                                <tr>
                                    <th className="px-4 py-3 border-b">סוג הציוד</th>
                                    <th className="px-4 py-3 border-b">שיוך פלוגתי</th>
                                    <th className="px-4 py-3 border-b">ייעוד (בעלים)</th>
                                    <th className="px-4 py-3 border-b">מיקום בפועל</th>
                                    <th className="px-4 py-3 border-b">צ'</th>
                                    <th className="px-4 py-3 border-b">סטטוס דיווח</th>
                                    <th className="px-4 py-3 border-b">מי דיווח אחרון</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-100">
                                {items.map((item, idx) => (
                                    <tr key={idx} className={`hover:bg-gray-50 ${item.reporting_status === 'Late' ? 'bg-red-50 late-row' : ''}`}>
                                        <td className="px-4 py-3 font-medium text-gray-900">{item.item_type}</td>
                                        <td className="px-4 py-3 text-gray-600">{item.unit_association}</td>
                                        <td className="px-4 py-3 text-gray-600">{item.designated_owner}</td>
                                        <td className="px-4 py-3 font-medium text-blue-700">{item.actual_location}</td>
                                        <td className="px-4 py-3 font-mono text-xs">{item.serial_number}</td>
                                        <td className="px-4 py-3">
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold border
                                                ${item.reporting_status === 'Reported' ? 'bg-green-100 text-green-800 border-green-200' :
                                                    item.reporting_status === 'Late' ? 'bg-red-100 text-red-800 border-red-200' : 'bg-gray-100 text-gray-600 border-gray-200'}`}>
                                                {item.reporting_status === 'Reported' ? 'תקין' :
                                                    item.reporting_status === 'Late' ? 'איחור' : 'חסר'}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-gray-500 text-xs">{item.last_reporter}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            <div className="text-gray-400 text-xs text-center mt-4 no-print">
                סה"כ פריטים: {items.length}
            </div>
        </div>
    );
}
