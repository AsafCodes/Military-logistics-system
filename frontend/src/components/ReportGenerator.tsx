import { useState } from 'react';
import api from '../api';

interface ReportItem {
    id: number;
    type: string;
    status: string;
    location: string;
    holder_name: string | null;
    holder_personal_number: string | null;
    last_verified_at: string | null;
}



interface ReportGeneratorProps {
    onClose: () => void;
}

export default function ReportGenerator({ onClose }: ReportGeneratorProps) {
    // Filters


    const PREDEFINED_TYPES = ["מכשיר קשר 710", "מכשיר קשר 624", "מכלול", "משקפת", "אפוד קרמי"];

    const [filterType, setFilterType] = useState("");
    const [filterLocation, setFilterLocation] = useState("");
    const [filterStatus, setFilterStatus] = useState("");
    const [filterHolder, setFilterHolder] = useState("");

    const [results, setResults] = useState<ReportItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [searched, setSearched] = useState(false);

    const handleSearch = async () => {
        setLoading(true);
        try {
            const params = new URLSearchParams();
            if (filterType) params.append('equipment_type', filterType);
            if (filterLocation) params.append('location_query', filterLocation);
            if (filterStatus) params.append('status', filterStatus);
            if (filterHolder) params.append('holder_name', filterHolder);

            const res = await api.get(`/reports/query?${params.toString()}`);
            setResults(res.data);
            setSearched(true);
        } catch (err) {
            console.error("Search failed", err);
            alert("Failed to generate report");
        } finally {
            setLoading(false);
        }
    };

    const downloadCSV = () => {
        if (results.length === 0) return;

        const headers = ["ID", "Type", "Status", "Location", "Holder Name", "Holder ID", "Last Verified"];
        const rows = results.map(item => [
            item.id,
            item.type,
            item.status,
            item.location,
            item.holder_name || "N/A",
            item.holder_personal_number || "N/A",
            item.last_verified_at || "Never"
        ]);

        const csvContent = [
            headers.join(","),
            ...rows.map(row => row.map(cell => `"${cell}"`).join(","))
        ].join("\n");

        const blob = new Blob(["\uFEFF" + csvContent], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.setAttribute('href', url);
        link.setAttribute('download', `inventory_report_${new Date().toISOString().slice(0, 10)}.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    };

    return (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden">
                {/* Header */}
                <div className="bg-slate-800 text-white p-6 flex justify-between items-center">
                    <div>
                        <h2 className="text-2xl font-bold flex items-center gap-2">
                            📊 Advanced Inventory Report
                        </h2>
                        <p className="text-slate-400 text-sm mt-1">Generate specific data cuts and export to Excel/CSV</p>
                    </div>
                    <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors">
                        ✕ Close
                    </button>
                </div>

                {/* Filters Bar */}
                <div className="bg-slate-50 p-6 border-b border-gray-200">
                    <div className="grid grid-cols-1 md:grid-cols-5 gap-4 items-end">
                        <div>
                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Equipment Type</label>
                            <select
                                value={filterType}
                                onChange={e => setFilterType(e.target.value)}
                                className="w-full h-10 px-3 rounded-lg border-gray-300 border focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 outline-none"
                            >
                                <option value="">All Types</option>
                                {PREDEFINED_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                            </select>
                        </div>

                        <div>
                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Status</label>
                            <select
                                value={filterStatus}
                                onChange={e => setFilterStatus(e.target.value)}
                                className="w-full h-10 px-3 rounded-lg border-gray-300 border focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 outline-none"
                            >
                                <option value="">Any Status</option>
                                <option value="Functional">Functional</option>
                                <option value="Malfunctioning">Malfunctioning</option>
                                <option value="Missing">Missing</option>
                            </select>
                        </div>

                        <div>
                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Location / Unit</label>
                            <input
                                type="text"
                                placeholder="e.g. 51, Base A..."
                                value={filterLocation}
                                onChange={e => setFilterLocation(e.target.value)}
                                className="w-full h-10 px-3 rounded-lg border-gray-300 border focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 outline-none"
                            />
                        </div>

                        <div>
                            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Holder Name/ID</label>
                            <input
                                type="text"
                                placeholder="Search person..."
                                value={filterHolder}
                                onChange={e => setFilterHolder(e.target.value)}
                                className="w-full h-10 px-3 rounded-lg border-gray-300 border focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 outline-none"
                            />
                        </div>

                        <button
                            onClick={handleSearch}
                            disabled={loading}
                            className="h-10 bg-indigo-600 hover:bg-indigo-700 text-white font-medium rounded-lg shadow-sm transition-colors flex items-center justify-center gap-2"
                        >
                            {loading ? 'Searching...' : '🔍 Generate Report'}
                        </button>
                    </div>
                </div>

                {/* Results Table */}
                <div className="flex-1 overflow-auto p-6 bg-gray-50">
                    {results.length > 0 ? (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                            <table className="w-full text-left">
                                <thead className="bg-gray-100 text-gray-600 text-xs uppercase font-bold tracking-wider">
                                    <tr>
                                        <th className="px-6 py-4">ID</th>
                                        <th className="px-6 py-4">Type</th>
                                        <th className="px-6 py-4">Status</th>
                                        <th className="px-6 py-4">Location</th>
                                        <th className="px-6 py-4">Holder</th>
                                        <th className="px-6 py-4">Verified</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100 text-sm">
                                    {results.map((item) => (
                                        <tr key={item.id} className="hover:bg-slate-50 transition-colors">
                                            <td className="px-6 py-4 font-mono text-gray-500">#{item.id}</td>
                                            <td className="px-6 py-4 font-medium text-gray-900">{item.type}</td>
                                            <td className="px-6 py-4">
                                                <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold
                                                    ${item.status === 'Functional' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
                                                    {item.status}
                                                </span>
                                            </td>
                                            <td className="px-6 py-4 text-gray-600 truncate max-w-xs" title={item.location}>
                                                {item.location}
                                            </td>
                                            <td className="px-6 py-4 text-gray-600">
                                                {item.holder_name ? (
                                                    <div className="flex flex-col">
                                                        <span className="font-medium text-gray-800">{item.holder_name}</span>
                                                        <span className="text-xs text-gray-400">{item.holder_personal_number}</span>
                                                    </div>
                                                ) : <span className="text-gray-400 italic">--</span>}
                                            </td>
                                            <td className="px-6 py-4 text-gray-500 text-xs">
                                                {item.last_verified_at ? new Date(item.last_verified_at).toLocaleDateString() : 'Never'}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        <div className="h-full flex flex-col items-center justify-center text-gray-400">
                            <div className="text-6xl mb-4">📄</div>
                            <p className="text-lg font-medium">{searched ? "No matching records found." : "Adjust filters and click Generate."}</p>
                        </div>
                    )}
                </div>

                {/* Footer Actions */}
                <div className="bg-white p-4 border-t border-gray-200 flex justify-between items-center">
                    <div className="text-sm text-gray-500">
                        Showing <b>{results.length}</b> records
                    </div>
                    {results.length > 0 && (
                        <button
                            onClick={downloadCSV}
                            className="bg-green-600 hover:bg-green-700 text-white px-6 py-2 rounded-lg font-medium transition-all shadow-sm flex items-center gap-2"
                        >
                            <span>📥</span> Download CSV
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
