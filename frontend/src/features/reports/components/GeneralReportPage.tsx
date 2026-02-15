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
            if (filters.types.length > 0 && !filters.types.includes(item.item_type)) return false;
            if (filters.unit && !item.unit_association.toLowerCase().includes(filters.unit.toLowerCase())) return false;
            if (filters.owner && !item.designated_owner.toLowerCase().includes(filters.owner.toLowerCase())) return false;
            if (filters.location && !item.actual_location?.toLowerCase().includes(filters.location.toLowerCase())) return false;
            if (filters.serial && !item.serial_number.toLowerCase().includes(filters.serial.toLowerCase())) return false;
            if (filters.reporter && !item.last_reporter.toLowerCase().includes(filters.reporter.toLowerCase())) return false;
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
        if (!lastVerified) return "אין רשומה";
        const diffMs = new Date().getTime() - new Date(lastVerified).getTime();
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
        const diffHours = Math.floor((diffMs % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
        if (diffDays > 0) return `לפני ${diffDays} ימים`;
        if (diffHours > 0) return `לפני ${diffHours} שעות`;
        return "הרגע";
    };

    const handleExportExcel = () => {
        if (filteredItems.length === 0) {
            alert("אין פריטים לייצוא.");
            return;
        }
        const headers = ["ID", "סוג", "יחידה", "בעלים", "מיקום", "סטטוס", "צד״ק"];
        const rows = filteredItems.map(item => [
            item.id || "",
            item.item_type,
            item.unit_association,
            item.designated_owner,
            item.actual_location,
            item.reporting_status,
            item.serial_number
        ]);
        const csvContent = [headers, ...rows]
            .map(e => e.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(","))
            .join("\n");
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

    return (
        <div className="flex flex-col h-full glass-card overflow-hidden animate-fade-in" dir="rtl">
            {/* Header / Meta / Actions */}
            <div className="p-4 border-b border-border/30 print:hidden">
                <div className="flex justify-between items-center mb-4">
                    <div>
                        <h2 className="text-xl font-bold text-foreground">דוח מצב מלאי</h2>
                        <p className="text-xs text-muted-foreground">עודכן: {lastUpdated}</p>
                    </div>
                    <div className="flex gap-2">
                        <button
                            onClick={handleExportExcel}
                            className="bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1 transition-colors"
                        >
                            📊 ייצוא Excel
                        </button>
                        <button
                            onClick={handlePrint}
                            className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1 transition-colors"
                        >
                            🖨️ הדפסה
                        </button>
                    </div>
                </div>

                {/* Counter & Refresh */}
                <div className="flex justify-between items-center text-sm">
                    <span className="font-semibold text-muted-foreground bg-accent px-2 py-1 rounded">
                        מציג {filteredItems.length} מתוך {items.length} פריטים
                    </span>
                    <button
                        onClick={fetchReport}
                        className="text-muted-foreground hover:text-primary transition-colors text-xs"
                    >
                        🔄 רענן נתונים
                    </button>
                </div>
            </div>

            {/* Table Container */}
            <div className="flex-1 overflow-auto print:overflow-visible">
                {loading ? (
                    <div className="flex justify-center items-center h-64">
                        <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent" />
                    </div>
                ) : (
                    <table className="w-full text-right border-collapse">
                        <thead className="sticky top-0 z-20 bg-card shadow-sm print:static">
                            {/* Row 1: Labels */}
                            <tr className="text-xs uppercase font-semibold border-b border-border/40 text-muted-foreground">
                                <th className="px-4 py-3 min-w-[120px]">סוג</th>
                                <th className="px-4 py-3 min-w-[100px]">צד״ק</th>
                                <th className="px-4 py-3 min-w-[180px]">יחידה</th>
                                <th className="px-4 py-3 min-w-[140px]">בעלים</th>
                                <th className="px-4 py-3 min-w-[140px]">מיקום</th>
                                <th className="px-4 py-3 min-w-[120px]">סטטוס</th>
                                <th className="px-4 py-3 min-w-[140px]">מאמת</th>
                            </tr>
                            {/* Row 2: Filters (Hidden on Print) */}
                            <tr className="border-b border-border/30 print:hidden">
                                <td className="px-2 py-2">
                                    <SearchableMultiSelect
                                        options={uniqueTypes}
                                        selected={filters.types}
                                        onChange={(newTypes) => setFilters(prev => ({ ...prev, types: newTypes }))}
                                        placeholder="סנן סוגים..."
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <input
                                        type="text"
                                        className="w-full px-2 py-1 text-xs border border-border/50 rounded
                                                   bg-background text-foreground
                                                   focus:ring-1 focus:ring-primary/50 outline-none h-8
                                                   placeholder:text-muted-foreground/50"
                                        placeholder="חפש צד״ק..."
                                        value={filters.serial}
                                        onChange={e => setFilters(prev => ({ ...prev, serial: e.target.value }))}
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <AutocompleteInput
                                        options={uniqueUnits}
                                        value={filters.unit}
                                        onChange={(val) => setFilters(prev => ({ ...prev, unit: val }))}
                                        placeholder="סנן יחידה..."
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <input
                                        type="text"
                                        className="w-full px-2 py-1 text-xs border border-border/50 rounded
                                                   bg-background text-foreground
                                                   focus:ring-1 focus:ring-primary/50 outline-none h-8
                                                   placeholder:text-muted-foreground/50"
                                        placeholder="סנן בעלים..."
                                        value={filters.owner}
                                        onChange={e => setFilters(prev => ({ ...prev, owner: e.target.value }))}
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <input
                                        type="text"
                                        className="w-full px-2 py-1 text-xs border border-border/50 rounded
                                                   bg-background text-foreground
                                                   focus:ring-1 focus:ring-primary/50 outline-none h-8
                                                   placeholder:text-muted-foreground/50"
                                        placeholder="סנן מיקום..."
                                        value={filters.location}
                                        onChange={e => setFilters(prev => ({ ...prev, location: e.target.value }))}
                                    />
                                </td>
                                <td className="px-2 py-2">
                                    <select
                                        className="w-full px-2 py-1 text-xs border border-border/50 rounded
                                                   bg-background text-foreground
                                                   focus:ring-1 focus:ring-primary/50 outline-none h-8"
                                        value={filters.status}
                                        onChange={e => setFilters(prev => ({ ...prev, status: e.target.value }))}
                                    >
                                        <option value="All">כל הסטטוסים</option>
                                        <option value="Reported">✅ דווח</option>
                                        <option value="Issues">⚠ בעיות</option>
                                    </select>
                                </td>
                                <td className="px-2 py-2">
                                    <input
                                        type="text"
                                        className="w-full px-2 py-1 text-xs border border-border/50 rounded
                                                   bg-background text-foreground
                                                   focus:ring-1 focus:ring-primary/50 outline-none h-8
                                                   placeholder:text-muted-foreground/50"
                                        placeholder="סנן מאמת..."
                                        value={filters.reporter}
                                        onChange={e => setFilters(prev => ({ ...prev, reporter: e.target.value }))}
                                    />
                                </td>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-border/20 text-sm">
                            {filteredItems.length > 0 ? (
                                filteredItems.map((item, idx) => {
                                    const isReported = item.reporting_status === 'Reported';
                                    const delayTime = calculateDelay(item.last_verified_at);

                                    return (
                                        <tr key={idx} className="hover:bg-accent/40 transition-colors">
                                            <td className="px-4 py-3 font-medium text-foreground">{item.item_type}</td>
                                            <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{item.serial_number}</td>
                                            <td className="px-4 py-3 text-muted-foreground truncate max-w-[180px]" title={item.unit_association}>
                                                {item.unit_association}
                                            </td>
                                            <td className="px-4 py-3 text-foreground">{item.designated_owner}</td>
                                            <td className="px-4 py-3 text-muted-foreground text-xs">{item.actual_location || "—"}</td>
                                            <td className="px-4 py-3">
                                                {isReported ? (
                                                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold
                                                                     text-emerald-700 dark:text-emerald-400
                                                                     bg-emerald-100 dark:bg-emerald-500/10
                                                                     border border-emerald-200 dark:border-emerald-500/20">
                                                        ✓ דווח
                                                    </span>
                                                ) : (
                                                    <div className="flex flex-col items-start gap-1">
                                                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold
                                                                         text-red-700 dark:text-red-400
                                                                         bg-red-100 dark:bg-red-500/10
                                                                         border border-red-200 dark:border-red-500/20">
                                                            ⚠ {item.reporting_status}
                                                        </span>
                                                        <span className="text-[10px] text-red-600 dark:text-red-400 font-medium pr-1">
                                                            {delayTime}
                                                        </span>
                                                    </div>
                                                )}
                                            </td>
                                            <td className="px-4 py-3 text-muted-foreground text-xs">
                                                {item.last_reporter || "—"}
                                            </td>
                                        </tr>
                                    );
                                })
                            ) : (
                                <tr>
                                    <td colSpan={7} className="px-6 py-12 text-center text-muted-foreground">
                                        אין פריטים התואמים את הסינון הנוכחי.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                )}
            </div>

            <div className="p-2 border-t border-border/30 text-[10px] text-muted-foreground text-center print:hidden">
                מציג {filteredItems.length} רשומות
            </div>
        </div>
    );
}
