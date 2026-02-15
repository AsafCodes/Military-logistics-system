import { useState, useEffect, useMemo } from 'react';
import api from '@/api';
import { SearchableMultiSelect } from '@/components/ui/SearchableMultiSelect';
import { AutocompleteInput } from '@/components/ui/AutocompleteInput';

// ==========================================
// Interfaces
// ==========================================
interface GeneralReportItem {
    id?: number;
    item_type: string;
    unit_association: string;
    designated_owner: string;
    actual_location: string;
    serial_number: string;
    reporting_status: string; // "Reported" | "Late" | "Missing"
    last_reporter: string;
    last_verified_at?: string; // ISO Date String
}

interface FilterState {
    types: string[]; // Multi-select
    unit: string;
    owner: string;
    location: string;
    serial: string;
    status: string; // 'All' | 'Reported' | 'Issues'
    reporter: string;
}

// ==========================================
// Main Component
// ==========================================

export default function GeneralReportPage() {
    const [items, setItems] = useState<GeneralReportItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [lastUpdated, setLastUpdated] = useState<string>("");

    // State: Filters
    const [filters, setFilters] = useState<FilterState>({
        types: [],
        unit: "",
        owner: "",
        location: "",
        serial: "",
        status: "All",
        reporter: ""
    });

    // 1. Fetch Data
    const fetchReport = async () => {
        setLoading(true);
        try {
            // Fetch everything and filter client-side for "Excel-style" speed
            // Assuming endpoint supports returning all if no params, or widely scoped
            const res = await api.get(`/reports/query`);
            setItems(res.data);
            setLastUpdated(new Date().toLocaleString('he-IL'));
        } catch (err) {
            console.error("Failed to load report", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchReport();
    }, []);

    // 2. Derived Data for Dropdowns (Unique Values)
    const uniqueTypes = useMemo(() => {
        const types = new Set(items.map(i => i.item_type));
        return Array.from(types).sort();
    }, [items]);

    const uniqueUnits = useMemo(() => {
        const units = new Set(items.filter(i => i.unit_association).map(i => i.unit_association));
        return Array.from(units).sort();
    }, [items]);

    // 3. Filtering Logic
    const filteredItems = useMemo(() => {
        return items.filter(item => {
            // Type Filter (OR Logic)
            if (filters.types.length > 0 && !filters.types.includes(item.item_type)) {
                return false;
            }

            // Unit (Substring)
            if (filters.unit && !item.unit_association.toLowerCase().includes(filters.unit.toLowerCase())) {
                return false;
            }

            // Owner (Substring)
            if (filters.owner && !item.designated_owner.toLowerCase().includes(filters.owner.toLowerCase())) {
                return false;
            }

            // Location (Substring)
            if (filters.location && !item.actual_location?.toLowerCase().includes(filters.location.toLowerCase())) {
                return false;
            }

            // Serial (Substring)
            if (filters.serial && !item.serial_number.toLowerCase().includes(filters.serial.toLowerCase())) {
                return false;
            }

            // Reporter (Substring)
            if (filters.reporter && !item.last_reporter.toLowerCase().includes(filters.reporter.toLowerCase())) {
                return false;
            }

            // Status Logic
            if (filters.status !== "All") {
                const isReported = item.reporting_status === 'Reported';
                if (filters.status === "Reported" && !isReported) return false;
                if (filters.status === "Issues" && isReported) return false;
            }

            return true;
        });
    }, [items, filters]);

    // Helpers
    const calculateDelay = (lastVerified?: string) => {
        if (!lastVerified) return "No record";
        const diffMs = new Date().getTime() - new Date(lastVerified).getTime();
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
        const diffHours = Math.floor((diffMs % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));

        if (diffDays > 0) return `${diffDays}d ago`;
        if (diffHours > 0) return `${diffHours}h ago`;
        return "Just now";
    };

    // --- New: Export & Print Features ---
    const handleExportExcel = () => {
        if (filteredItems.length === 0) {
            alert("No items to export.");
            return;
        }

        const headers = ["ID", "Type", "Unit", "Owner", "Location", "Status", "Serial"];
        const rows = filteredItems.map(item => [
            item.id || "",
            item.item_type,
            item.unit_association,
            item.designated_owner,
            item.actual_location,
            item.reporting_status,
            item.serial_number
        ]);

        // Escape CSV content (simple version)
        const csvContent = [headers, ...rows]
            .map(e => e.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(","))
            .join("\n");

        // Add BOM for Hebrew support in Excel
        const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.setAttribute("download", `inventory_report_${new Date().toISOString().slice(0, 10)}.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    };

    const handlePrint = () => {
        window.print();
    };

    // Render
    return (
        <div className="flex flex-col h-full bg-white rounded-lg shadow-sm">
            {/* Header / Meta / Actions */}
            <div className="p-4 border-b border-gray-100 bg-gray-50/50 print:hidden">
                <div className="flex justify-between items-center mb-4">
                    <div>
                        <h2 className="text-xl font-bold text-gray-800">Inventory Status Report</h2>
                        <p className="text-xs text-gray-500">Updated: {lastUpdated}</p>
                    </div>
                    <div className="flex gap-2">
                        <button
                            onClick={handleExportExcel}
                            className="bg-green-600 hover:bg-green-700 text-white px-3 py-1.5 rounded text-sm font-medium flex items-center gap-1 transition-colors"
                        >
                            📊 Export Excel
                        </button>
                        <button
                            onClick={handlePrint}
                            className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm font-medium flex items-center gap-1 transition-colors"
                        >
                            🖨️ Print
                        </button>
                    </div>
                </div>

                {/* Counter & Refresh */}
                <div className="flex justify-between items-center text-sm">
                    <span className="font-semibold text-gray-600 bg-gray-200 px-2 py-1 rounded">
                        Showing {filteredItems.length} of {items.length} items
                    </span>
                    <button
                        onClick={fetchReport}
                        className="text-gray-500 hover:text-indigo-600 transition-colors text-xs"
                    >
                        🔄 Refresh Data
                    </button>
                </div>
            </div>

            {/* Table Container */}
            <div className="flex-1 overflow-auto print:overflow-visible">
                {loading ? (
                    <div className="flex justify-center items-center h-64">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                    </div>
                ) : (
                    <table className="w-full text-left border-collapse">
                        <thead className="sticky top-0 z-20 bg-gray-50 text-gray-500 shadow-sm print:static">
                            {/* Row 1: Labels */}
                            <tr className="text-xs uppercase font-semibold border-b border-gray-200">
                                <th className="px-4 py-3 min-w-[120px]">Type</th>
                                <th className="px-4 py-3 min-w-[100px]">Serial</th>
                                <th className="px-4 py-3 min-w-[180px]">Unit</th>
                                <th className="px-4 py-3 min-w-[140px]">Owner</th>
                                <th className="px-4 py-3 min-w-[140px]">Location</th>
                                <th className="px-4 py-3 min-w-[120px]">Status</th>
                                <th className="px-4 py-3 min-w-[140px]">Verified By</th>
                            </tr>
                            {/* Row 2: Filters (Hidden on Print) */}
                            <tr className="bg-white border-b border-gray-200 print:hidden">
                                <td className="px-2 py-2">
                                    <SearchableMultiSelect
                                        options={uniqueTypes}
                                        selected={filters.types}
                                        onChange={(newTypes) => setFilters(prev => ({ ...prev, types: newTypes }))}
                                        placeholder="Filter Types..."
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <input
                                        type="text"
                                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded focus:ring-1 focus:ring-indigo-500 outline-none h-8"
                                        placeholder="Search SN..."
                                        value={filters.serial}
                                        onChange={e => setFilters(prev => ({ ...prev, serial: e.target.value }))}
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <AutocompleteInput
                                        options={uniqueUnits}
                                        value={filters.unit}
                                        onChange={(val) => setFilters(prev => ({ ...prev, unit: val }))}
                                        placeholder="Filter Unit..."
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <input
                                        type="text"
                                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded focus:ring-1 focus:ring-indigo-500 outline-none h-8"
                                        placeholder="Filter Owner..."
                                        value={filters.owner}
                                        onChange={e => setFilters(prev => ({ ...prev, owner: e.target.value }))}
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <input
                                        type="text"
                                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded focus:ring-1 focus:ring-indigo-500 outline-none h-8"
                                        placeholder="Filter Loc..."
                                        value={filters.location}
                                        onChange={e => setFilters(prev => ({ ...prev, location: e.target.value }))}
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <select
                                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded focus:ring-1 focus:ring-indigo-500 outline-none h-8 bg-white"
                                        value={filters.status}
                                        onChange={e => setFilters(prev => ({ ...prev, status: e.target.value }))}
                                    >
                                        <option value="All">All Statuses</option>
                                        <option value="Reported">✅ reported</option>
                                        <option value="Issues">⚠ Issues</option>
                                    </select>
                                </td>
                                <td className="px-2 py-2">
                                    <input
                                        type="text"
                                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded focus:ring-1 focus:ring-indigo-500 outline-none h-8"
                                        placeholder="Filter Reporter..."
                                        value={filters.reporter}
                                        onChange={e => setFilters(prev => ({ ...prev, reporter: e.target.value }))}
                                    />
                                </td>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100 text-sm">
                            {filteredItems.length > 0 ? (
                                filteredItems.map((item, idx) => {
                                    const isReported = item.reporting_status === 'Reported';
                                    const delayTime = calculateDelay(item.last_verified_at);

                                    return (
                                        <tr key={idx} className="hover:bg-gray-50 transition-colors">
                                            <td className="px-4 py-3 font-medium text-gray-800">{item.item_type}</td>
                                            <td className="px-4 py-3 font-mono text-xs text-gray-600">{item.serial_number}</td>
                                            <td className="px-4 py-3 text-gray-600 truncate max-w-[180px]" title={item.unit_association}>
                                                {item.unit_association}
                                            </td>
                                            <td className="px-4 py-3 text-gray-800">{item.designated_owner}</td>
                                            <td className="px-4 py-3 text-gray-500 text-xs">{item.actual_location || "-"}</td>
                                            <td className="px-4 py-3">
                                                {isReported ? (
                                                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-green-100 text-green-800 border border-green-200">
                                                        ✓ Reported
                                                    </span>
                                                ) : (
                                                    <div className="flex flex-col items-start gap-1">
                                                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-800 border border-red-200">
                                                            ⚠ {item.reporting_status}
                                                        </span>
                                                        <span className="text-[10px] text-red-600 font-medium pl-1">
                                                            {delayTime}
                                                        </span>
                                                    </div>
                                                )}
                                            </td>
                                            <td className="px-4 py-3 text-gray-600 text-xs">
                                                {item.last_reporter || "-"}
                                            </td>
                                        </tr>
                                    );
                                })
                            ) : (
                                <tr>
                                    <td colSpan={7} className="px-6 py-12 text-center text-gray-400">
                                        No items match the current filters.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                )}
            </div>

            <div className="p-2 border-t border-gray-100 bg-gray-50 text-[10px] text-gray-400 text-center print:hidden">
                Displaying {filteredItems.length} records
            </div>
        </div>
    );
}
