import { useState, useEffect } from 'react';
import { Package, CheckCircle2, AlertTriangle } from 'lucide-react';
import api from '@/api';
import type { UnitReadiness } from '@/types';

// ============================================================
// Animated Counter Hook
// ============================================================

function useAnimatedCounter(target: number, duration = 1200) {
    const [count, setCount] = useState(0);

    useEffect(() => {
        if (target === 0) { setCount(0); return; }

        let start = 0;
        const startTime = performance.now();

        const tick = (now: number) => {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1);
            // Ease-out cubic
            const eased = 1 - Math.pow(1 - progress, 3);
            const current = Math.round(eased * target);

            if (current !== start) {
                start = current;
                setCount(current);
            }

            if (progress < 1) {
                requestAnimationFrame(tick);
            }
        };

        requestAnimationFrame(tick);
    }, [target, duration]);

    return count;
}

// ============================================================
// Types
// ============================================================

interface StatsGridProps {
    stats: UnitReadiness;
}

// ============================================================
// Component
// ============================================================

export default function StatsGrid({ stats }: StatsGridProps) {
    const [openTickets, setOpenTickets] = useState(0);

    // Fetch open tickets count
    useEffect(() => {
        api.get('/tickets/?status_filter=Open')
            .then(res => setOpenTickets(Array.isArray(res.data) ? res.data.length : 0))
            .catch(() => setOpenTickets(0));
    }, []);

    // Animated values
    const animatedReadiness = useAnimatedCounter(stats.readiness_percentage);
    const animatedTotal = useAnimatedCounter(stats.total_items);
    const animatedFunctional = useAnimatedCounter(stats.functional_items);
    const animatedTickets = useAnimatedCounter(openTickets);

    // Readiness color
    const readinessColor = stats.readiness_percentage >= 80
        ? 'text-emerald-500 dark:text-emerald-400'
        : stats.readiness_percentage >= 60
            ? 'text-amber-500 dark:text-amber-400'
            : 'text-red-500 dark:text-red-400';

    const readinessRingColor = stats.readiness_percentage >= 80
        ? 'stroke-emerald-500'
        : stats.readiness_percentage >= 60
            ? 'stroke-amber-500'
            : 'stroke-red-500';

    // SVG ring progress
    const circumference = 2 * Math.PI * 40;
    const dashOffset = circumference - (circumference * stats.readiness_percentage / 100);

    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 animate-slide-up">
            {/* ── Readiness Score ── */}
            <div className="glass-card p-6 flex flex-col items-center justify-center relative overflow-hidden group">
                {/* Background glow */}
                <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/5 to-transparent dark:from-emerald-500/10 opacity-0 group-hover:opacity-100 transition-opacity" />

                <div className="relative w-24 h-24 mb-3">
                    <svg className="w-24 h-24 -rotate-90" viewBox="0 0 96 96">
                        <circle cx="48" cy="48" r="40" fill="none"
                            className="stroke-muted/30" strokeWidth="6" />
                        <circle cx="48" cy="48" r="40" fill="none"
                            className={readinessRingColor} strokeWidth="6"
                            strokeLinecap="round"
                            strokeDasharray={circumference}
                            strokeDashoffset={dashOffset}
                            style={{ transition: 'stroke-dashoffset 1.2s ease-out' }} />
                    </svg>
                    <div className="absolute inset-0 flex items-center justify-center">
                        <span className={`text-2xl font-bold ${readinessColor}`}>
                            {animatedReadiness}%
                        </span>
                    </div>
                </div>
                <span className="text-sm font-medium text-muted-foreground">מוכנות יחידה</span>
                <span className="text-xs text-muted-foreground/60 mt-0.5">יעד: 80%</span>
            </div>

            {/* ── Total Inventory ── */}
            <div className="glass-card p-6 flex flex-col justify-between group relative overflow-hidden">
                <div className="absolute inset-0 bg-gradient-to-br from-blue-500/5 to-transparent dark:from-blue-500/10 opacity-0 group-hover:opacity-100 transition-opacity" />
                <div className="flex items-center justify-between mb-4 relative">
                    <span className="text-sm font-medium text-muted-foreground">סה״כ מלאי</span>
                    <div className="p-2 rounded-lg bg-blue-500/10 text-blue-500 dark:text-blue-400">
                        <Package size={18} />
                    </div>
                </div>
                <div className="relative">
                    <div className="text-3xl font-bold text-foreground">{animatedTotal}</div>
                    <span className="text-xs text-muted-foreground/60 mt-1">פריטים רשומים</span>
                </div>
            </div>

            {/* ── Operational ── */}
            <div className="glass-card p-6 flex flex-col justify-between group relative overflow-hidden">
                <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/5 to-transparent dark:from-emerald-500/10 opacity-0 group-hover:opacity-100 transition-opacity" />
                <div className="flex items-center justify-between mb-4 relative">
                    <span className="text-sm font-medium text-muted-foreground">תקינים</span>
                    <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-500 dark:text-emerald-400">
                        <CheckCircle2 size={18} />
                    </div>
                </div>
                <div className="relative">
                    <div className="text-3xl font-bold text-foreground">{animatedFunctional}</div>
                    <span className="text-xs text-muted-foreground/60 mt-1">מוכנים לפריסה</span>
                </div>
            </div>

            {/* ── Open Tickets ── */}
            <div className="glass-card p-6 flex flex-col justify-between group relative overflow-hidden">
                <div className="absolute inset-0 bg-gradient-to-br from-amber-500/5 to-transparent dark:from-amber-500/10 opacity-0 group-hover:opacity-100 transition-opacity" />
                <div className="flex items-center justify-between mb-4 relative">
                    <span className="text-sm font-medium text-muted-foreground">תקלות פתוחות</span>
                    <div className="p-2 rounded-lg bg-amber-500/10 text-amber-500 dark:text-amber-400">
                        <AlertTriangle size={18} />
                    </div>
                </div>
                <div className="relative">
                    <div className={`text-3xl font-bold ${openTickets > 0 ? 'text-amber-500 dark:text-amber-400' : 'text-foreground'}`}>
                        {animatedTickets}
                    </div>
                    <span className="text-xs text-muted-foreground/60 mt-1">כרטיסים ממתינים</span>
                </div>
            </div>
        </div>
    );
}
