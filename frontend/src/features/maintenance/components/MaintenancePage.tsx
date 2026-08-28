import { useState, useEffect, useMemo, useCallback } from 'react';
import { Wrench, RefreshCw, AlertTriangle, CheckCircle, Clock, Inbox } from 'lucide-react';
import api from '@/api';

// ============================================================
// Types
// ============================================================

interface Ticket {
    id: number;
    equipment_id: number;
    equipment_name: string;
    fault_type: string;
    description: string;
    status: string;
    opened_at: string;
    closed_at?: string | null;
}

type FilterTab = 'all' | 'Open' | 'In Progress' | 'Closed';

// ============================================================
// Helpers
// ============================================================

const getStatusMeta = (status: string) => {
    switch (status) {
        case 'Open':
            return {
                label: 'פתוח',
                icon: <AlertTriangle size={14} />,
                cls: 'text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-500/10 border-amber-200 dark:border-amber-500/20',
            };
        case 'In Progress':
            return {
                label: 'בטיפול',
                icon: <Clock size={14} />,
                cls: 'text-blue-700 dark:text-blue-400 bg-blue-100 dark:bg-blue-500/10 border-blue-200 dark:border-blue-500/20',
            };
        case 'Closed':
            return {
                label: 'סגור',
                icon: <CheckCircle size={14} />,
                cls: 'text-emerald-700 dark:text-emerald-400 bg-emerald-100 dark:bg-emerald-500/10 border-emerald-200 dark:border-emerald-500/20',
            };
        default:
            return {
                label: status,
                icon: null,
                cls: 'text-muted-foreground bg-muted border-border/30',
            };
    }
};

const formatDate = (dateStr: string | null | undefined) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleString('he-IL', {
        day: '2-digit', month: '2-digit', year: '2-digit',
        hour: '2-digit', minute: '2-digit',
    });
};

// ============================================================
// Component
// ============================================================

export default function MaintenancePage() {
    const [tickets, setTickets] = useState<Ticket[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<FilterTab>('all');
    const [fixingId, setFixingId] = useState<number | null>(null);

    const fetchTickets = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const ticketRes = await api.get('/tickets/');
            setTickets(ticketRes.data);
        } catch (err) {
            console.error(err);
            setError('טעינת כרטיסי תחזוקה נכשלה');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { fetchTickets(); }, [fetchTickets]);

    // ── Counts ──
    const counts = useMemo(() => ({
        all: tickets.length,
        Open: tickets.filter(t => t.status === 'Open').length,
        'In Progress': tickets.filter(t => t.status === 'In Progress').length,
        Closed: tickets.filter(t => t.status === 'Closed').length,
    }), [tickets]);

    // ── Filtered ──
    const filtered = useMemo(() => {
        if (activeTab === 'all') return tickets;
        return tickets.filter(t => t.status === activeTab);
    }, [tickets, activeTab]);

    // ── Close Ticket ──
    const handleCloseTicket = async (ticket: Ticket) => {
        setFixingId(ticket.id);
        try {
            await api.post(`/maintenance/fix/${ticket.equipment_id}`);
            fetchTickets();
        } catch (err) {
            console.error(err);
            alert('סגירת הכרטיס נכשלה');
        } finally {
            setFixingId(null);
        }
    };

    // ── Loading ──
    if (loading) return (
        <div className="flex h-full items-center justify-center">
            <div className="animate-spin rounded-full h-10 w-10 border-2 border-primary border-t-transparent" />
        </div>
    );

    if (error) return (
        <div className="glass-card p-6 text-center text-destructive animate-fade-in">{error}</div>
    );

    const tabs: { key: FilterTab; label: string }[] = [
        { key: 'all', label: 'הכל' },
        { key: 'Open', label: 'פתוח' },
        { key: 'In Progress', label: 'בטיפול' },
        { key: 'Closed', label: 'סגור' },
    ];

    return (
        <div className="space-y-6 animate-fade-in" dir="rtl">
            {/* ── Header ── */}
            <div className="glass-card p-6">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 rounded-lg bg-amber-500/10">
                            <Wrench size={20} className="text-amber-600 dark:text-amber-400" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-foreground">תחזוקה וכרטיסים</h2>
                            <p className="text-sm text-muted-foreground">
                                {counts.all} כרטיסים • {counts.Open} פתוחים
                            </p>
                        </div>
                    </div>
                    <button
                        onClick={fetchTickets}
                        className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                                   text-primary hover:bg-primary/10 transition-colors"
                    >
                        <RefreshCw size={16} />
                        <span className="hidden sm:inline">רענן</span>
                    </button>
                </div>
            </div>

            {/* ── Summary Stat Cards ── */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard label="סה״כ כרטיסים" count={counts.all} icon={<Inbox size={18} />} accent="text-primary" />
                <StatCard label="פתוחים" count={counts.Open} icon={<AlertTriangle size={18} />} accent="text-amber-600 dark:text-amber-400" />
                <StatCard label="בטיפול" count={counts['In Progress']} icon={<Clock size={18} />} accent="text-blue-600 dark:text-blue-400" />
                <StatCard label="סגורים" count={counts.Closed} icon={<CheckCircle size={18} />} accent="text-emerald-600 dark:text-emerald-400" />
            </div>

            {/* ── Filter Tabs ── */}
            <div className="glass-card p-2 flex gap-1">
                {tabs.map(tab => (
                    <button
                        key={tab.key}
                        onClick={() => setActiveTab(tab.key)}
                        className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors
                            ${activeTab === tab.key
                                ? 'bg-primary text-primary-foreground shadow-sm'
                                : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
                            }`}
                    >
                        {tab.label} ({counts[tab.key]})
                    </button>
                ))}
            </div>

            {/* ── Ticket Cards ── */}
            {filtered.length === 0 ? (
                <div className="glass-card p-12 text-center">
                    <Inbox size={40} className="text-muted-foreground/30 mx-auto mb-3" />
                    <p className="text-muted-foreground">
                        {activeTab === 'all' ? 'אין כרטיסי תחזוקה' : `אין כרטיסים בסטטוס "${tabs.find(t => t.key === activeTab)?.label}"`}
                    </p>
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {filtered.map((ticket, idx) => {
                        const statusMeta = getStatusMeta(ticket.status);
                        return (
                            <div
                                key={ticket.id}
                                className="glass-card overflow-hidden animate-slide-up hover:shadow-lg transition-shadow"
                                style={{ animationDelay: `${Math.min(idx * 50, 300)}ms` }}
                            >
                                {/* Card Header */}
                                <div className="flex items-center justify-between px-5 py-3 border-b border-border/30">
                                    <div className="flex items-center gap-2">
                                        <span className="text-xs font-mono text-muted-foreground">#{ticket.id}</span>
                                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold border ${statusMeta.cls}`}>
                                            {statusMeta.icon} {statusMeta.label}
                                        </span>
                                    </div>
                                    <span className="text-xs text-muted-foreground">
                                        {formatDate(ticket.opened_at)}
                                    </span>
                                </div>

                                {/* Card Body */}
                                <div className="px-5 py-4 space-y-3">
                                    <div className="flex items-start justify-between">
                                        <div>
                                            <h4 className="font-bold text-foreground">{ticket.equipment_name}</h4>
                                            <div className="flex items-center gap-2 mt-1">
                                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium
                                                                 bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400">
                                                    ⚠ {ticket.fault_type}
                                                </span>
                                            </div>
                                        </div>
                                        {/* Equipment ID badge */}
                                        <span className="text-xs font-mono text-muted-foreground bg-accent/50 px-2 py-1 rounded">
                                            ציוד #{ticket.equipment_id}
                                        </span>
                                    </div>

                                    {ticket.description && (
                                        <p className="text-sm text-muted-foreground leading-relaxed">
                                            {ticket.description}
                                        </p>
                                    )}

                                    {/* Dates */}
                                    <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                                        <span>📅 נפתח: {formatDate(ticket.opened_at)}</span>
                                        {ticket.closed_at && (
                                            <span>✅ נסגר: {formatDate(ticket.closed_at)}</span>
                                        )}
                                    </div>
                                </div>

                                {/* Card Footer — Manager Actions. The backend's RESOLVE_FAULT
                                    gate is what actually decides this; nothing here does. */}
                                {ticket.status !== 'Closed' && (
                                    <div className="px-5 py-3 border-t border-border/30 bg-accent/20">
                                        <button
                                            onClick={() => handleCloseTicket(ticket)}
                                            disabled={fixingId === ticket.id}
                                            className="w-full py-2 rounded-lg text-sm font-bold transition-colors
                                                       bg-emerald-600 text-white hover:bg-emerald-700
                                                       disabled:opacity-50 disabled:cursor-wait
                                                       flex items-center justify-center gap-2"
                                        >
                                            {fixingId === ticket.id ? (
                                                <>
                                                    <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
                                                    סוגר...
                                                </>
                                            ) : (
                                                <>
                                                    <CheckCircle size={14} />
                                                    סגור כרטיס ותקן ציוד
                                                </>
                                            )}
                                        </button>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

// ============================================================
// Stat Card
// ============================================================

function StatCard({ label, count, icon, accent }: {
    label: string; count: number; icon: React.ReactNode; accent: string;
}) {
    return (
        <div className="glass-card p-4 flex items-center gap-3">
            <div className={`p-2 rounded-lg bg-accent/50 ${accent}`}>
                {icon}
            </div>
            <div>
                <div className="text-2xl font-bold text-foreground">{count}</div>
                <div className="text-xs text-muted-foreground">{label}</div>
            </div>
        </div>
    );
}
