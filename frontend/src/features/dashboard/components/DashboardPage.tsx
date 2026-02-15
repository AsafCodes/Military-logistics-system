import { useEffect, useState } from 'react';
import { RefreshCw, Package, ArrowLeftCircle } from 'lucide-react';
import api from '@/api';

// Components
import StatsGrid from './StatsGrid';
import DailyActivityTable from './DailyActivityTable';

// Hooks
import { useDashboardData } from '../hooks/useDashboardData';

// Types
import type { User } from '@/types';

// ============================================================
// Props
// ============================================================

interface DashboardProps {
    onLogout: () => void;
}

// ============================================================
// Component
// ============================================================

export default function Dashboard({ onLogout: _onLogout }: DashboardProps) {
    // ── State ──
    const [user, setUser] = useState<User | null>(null);
    const [initLoading, setInitLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Data via hook
    const { stats, equipment, error: dataError, fetchData, refreshData } = useDashboardData();

    // ── Init ──
    useEffect(() => {
        initDashboard();
    }, []);

    const initDashboard = async () => {
        try {
            const userRes = await api.get('/users/me');
            const currentUser = userRes.data;
            setUser(currentUser);
            await fetchData(currentUser);
        } catch (err) {
            console.error("Failed to init", err);
            setError("טעינת לוח הבקרה נכשלה. נסה לרענן.");
        } finally {
            setInitLoading(false);
        }
    };

    // ── Helpers ──
    const getRoleLabel = (role: string) => {
        switch (role) {
            case 'master': return 'מנהל ראשי';
            case 'manager': return 'מפקד';
            case 'technician_manager': return 'קצין טכני';
            case 'user': return 'חייל';
            default: return role;
        }
    };

    const getGreeting = () => {
        const hour = new Date().getHours();
        if (hour < 12) return 'בוקר טוב';
        if (hour < 17) return 'צהריים טובים';
        return 'ערב טוב';
    };

    const getComplianceColor = (level: string) => {
        switch (level) {
            case 'GOOD': return 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10';
            case 'WARNING': return 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10';
            case 'SEVERE': return 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-500/10';
            default: return 'text-muted-foreground bg-muted';
        }
    };

    const getStatusBadge = (status: string) => {
        switch (status) {
            case 'Functional': return 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10';
            case 'Malfunctioning': return 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-500/10';
            default: return 'text-muted-foreground bg-muted';
        }
    };

    // ── Loading State ──
    if (initLoading) return (
        <div className="flex h-full items-center justify-center">
            <div className="animate-spin rounded-full h-10 w-10 border-2 border-primary border-t-transparent" />
        </div>
    );

    // ── Error State ──
    if (error || dataError) return (
        <div className="glass-card p-6 text-center text-destructive animate-fade-in">
            {error || dataError}
        </div>
    );

    return (
        <div className="space-y-6 animate-fade-in" dir="rtl">
            {/* ── Welcome Card ── */}
            <div className="glass-card p-6 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-64 h-64 bg-gradient-to-br from-blue-500/10 to-violet-500/10 dark:from-blue-500/20 dark:to-violet-500/20 rounded-full -translate-x-1/2 -translate-y-1/2 blur-3xl" />
                <div className="relative flex items-center justify-between">
                    <div>
                        <h2 className="text-xl font-bold text-foreground mb-1">
                            {getGreeting()}, {user?.full_name || 'משתמש'} 👋
                        </h2>
                        <p className="text-sm text-muted-foreground">
                            {getRoleLabel(user?.role || 'user')} • {user?.battalion || ''} {user?.company ? `/ ${user.company}` : ''}
                        </p>
                    </div>
                    <button
                        onClick={() => refreshData(user)}
                        className="flex items-center gap-2 px-4 py-2 rounded-lg
                                   text-sm font-medium text-primary hover:bg-primary/10
                                   transition-colors"
                        title="רענן נתונים"
                    >
                        <RefreshCw size={16} />
                        <span className="hidden sm:inline">רענן</span>
                    </button>
                </div>
            </div>

            {/* ── Stats Grid ── */}
            {stats && <StatsGrid stats={stats} />}

            {/* ── Two-Column Layout: Equipment Preview + Activity Feed ── */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Equipment Preview */}
                <div className="glass-card overflow-hidden animate-slide-up" style={{ animationDelay: '100ms' }}>
                    <div className="flex items-center justify-between px-6 py-4 border-b border-border/30">
                        <div className="flex items-center gap-2">
                            <Package size={18} className="text-primary" />
                            <h3 className="font-bold text-foreground">
                                {user?.profile?.can_view_company_realtime ? 'ציוד יחידה' : 'הציוד שלי'}
                            </h3>
                        </div>
                        <span className="text-xs text-muted-foreground">
                            {equipment.length} פריטים
                        </span>
                    </div>
                    <div className="divide-y divide-border/20">
                        {equipment.length === 0 ? (
                            <div className="p-6 text-center text-muted-foreground text-sm">
                                אין ציוד להצגה
                            </div>
                        ) : (
                            equipment.slice(0, 5).map(item => (
                                <div key={item.id} className="flex items-center justify-between px-6 py-3 hover:bg-accent/40 transition-colors">
                                    <div className="flex-1 min-w-0">
                                        <div className="text-sm font-medium text-foreground truncate">{item.type}</div>
                                        {item.serial_number && (
                                            <div className="text-xs font-mono text-muted-foreground">{item.serial_number}</div>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-2 mr-4">
                                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${getStatusBadge(item.status)}`}>
                                            {item.status === 'Functional' ? 'תקין' : item.status === 'Malfunctioning' ? 'תקול' : item.status}
                                        </span>
                                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${getComplianceColor(item.compliance_level)}`}>
                                            {item.compliance_level === 'GOOD' ? '✓' : item.compliance_level === 'WARNING' ? '!' : item.compliance_level === 'SEVERE' ? '✕' : '—'}
                                        </span>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                    {equipment.length > 5 && (
                        <div className="px-6 py-3 border-t border-border/30 text-center">
                            <span className="text-sm text-primary font-medium flex items-center justify-center gap-1">
                                <ArrowLeftCircle size={14} />
                                עוד {equipment.length - 5} פריטים בעמוד ציוד
                            </span>
                        </div>
                    )}
                </div>

                {/* Activity Feed */}
                <div className="glass-card overflow-hidden animate-slide-up" style={{ animationDelay: '200ms' }}>
                    <div className="flex items-center justify-between px-6 py-4 border-b border-border/30">
                        <h3 className="font-bold text-foreground">פעילות אחרונה (24 שעות)</h3>
                    </div>
                    <DailyActivityTable limit={8} />
                </div>
            </div>
        </div>
    );
}
