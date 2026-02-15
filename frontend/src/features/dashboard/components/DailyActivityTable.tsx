import { useEffect, useState } from 'react';
import { Clock, ArrowUpDown, AlertCircle, Wrench } from 'lucide-react';
import api from '@/api';

// ============================================================
// Types — matches the actual API response from /reports/daily_movement
// ============================================================

interface DailyActivityItem {
    id: number;
    timestamp: string | null;
    event_type: string;
    serial_number: string | null;
    reporter_name: string | null;
    location: string | null;
}

interface DailyActivityTableProps {
    /** Max items to show. undefined = show all */
    limit?: number;
    /** Callback when "View All" is clicked */
    onViewAll?: () => void;
}

// ============================================================
// Helpers
// ============================================================

function getEventIcon(eventType: string) {
    switch (eventType?.toLowerCase()) {
        case 'movement':
        case 'transfer': return <ArrowUpDown size={14} />;
        case 'fault':
        case 'report_fault': return <AlertCircle size={14} />;
        case 'fix':
        case 'repair': return <Wrench size={14} />;
        default: return <Clock size={14} />;
    }
}

function getEventColor(eventType: string) {
    switch (eventType?.toLowerCase()) {
        case 'fault':
        case 'report_fault': return 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10';
        case 'fix':
        case 'repair': return 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10';
        default: return 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10';
    }
}

function getEventLabel(eventType: string) {
    switch (eventType?.toLowerCase()) {
        case 'movement':
        case 'transfer': return 'העברה';
        case 'fault':
        case 'report_fault': return 'דיווח תקלה';
        case 'fix':
        case 'repair': return 'תיקון';
        case 'verify':
        case 'verification': return 'אימות';
        default: return eventType || 'אירוע';
    }
}

// ============================================================
// Component
// ============================================================

export default function DailyActivityTable({ limit, onViewAll }: DailyActivityTableProps) {
    const [activities, setActivities] = useState<DailyActivityItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchActivity = async () => {
            try {
                const res = await api.get('/reports/daily_movement');
                // API returns a flat array, not { items: [...] }
                const data = Array.isArray(res.data) ? res.data : (res.data?.items || []);
                setActivities(data);
            } catch (err) {
                console.error("Failed to fetch daily activity", err);
                setError("טעינת יומן הפעילות נכשלה");
            } finally {
                setLoading(false);
            }
        };

        fetchActivity();
    }, []);

    if (loading) return (
        <div className="p-6 text-center text-muted-foreground">
            <div className="animate-spin rounded-full h-6 w-6 border-2 border-primary border-t-transparent mx-auto mb-2" />
            <span className="text-sm">טוען פעילות...</span>
        </div>
    );

    if (error) return (
        <div className="p-4 text-center text-destructive text-sm">{error}</div>
    );

    if (activities.length === 0) return (
        <div className="p-6 text-center text-muted-foreground text-sm">
            אין פעילות מתועדת ב-24 השעות האחרונות
        </div>
    );

    const displayItems = limit ? activities.slice(0, limit) : activities;

    return (
        <div>
            <div className="overflow-x-auto">
                <table className="w-full text-right">
                    <thead>
                        <tr className="border-b border-border/40">
                            <th className="px-4 py-3 text-xs font-semibold text-muted-foreground">שעה</th>
                            <th className="px-4 py-3 text-xs font-semibold text-muted-foreground">צד״ק</th>
                            <th className="px-4 py-3 text-xs font-semibold text-muted-foreground">אירוע</th>
                            <th className="px-4 py-3 text-xs font-semibold text-muted-foreground">מיקום</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-border/30">
                        {displayItems.map((item, index) => (
                            <tr
                                key={item.id || index}
                                className="hover:bg-accent/40 transition-colors"
                            >
                                <td className="px-4 py-3 text-xs font-mono text-muted-foreground">
                                    {item.timestamp
                                        ? new Date(item.timestamp).toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' })
                                        : '—'
                                    }
                                </td>
                                <td className="px-4 py-3 font-mono text-sm font-bold text-primary">
                                    {item.serial_number || '—'}
                                </td>
                                <td className="px-4 py-3">
                                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${getEventColor(item.event_type)}`}>
                                        {getEventIcon(item.event_type)}
                                        {getEventLabel(item.event_type)}
                                    </span>
                                </td>
                                <td className="px-4 py-3 text-xs text-muted-foreground">
                                    {item.location || '—'}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* View All link */}
            {limit && activities.length > limit && onViewAll && (
                <div className="px-4 py-3 border-t border-border/30 text-center">
                    <button
                        onClick={onViewAll}
                        className="text-sm text-primary hover:text-primary/80 font-medium transition-colors"
                    >
                        הצג את כל {activities.length} האירועים ←
                    </button>
                </div>
            )}
        </div>
    );
}
