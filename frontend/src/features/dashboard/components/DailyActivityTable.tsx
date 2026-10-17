import { useEffect, useState } from 'react';
import { Clock, ArrowUpDown, Wrench, UserCheck, ShieldCheck, AlertTriangle, ShieldAlert, PackagePlus, ClipboardCheck } from 'lucide-react';
import api from '@/lib/axios';
import type { MovementReportItem } from '@/types';
import { describeMove, NONE } from '../describeMove';

// ============================================================
// Types
// ============================================================

// The row is MovementReportItem in @/types, held to the route by
// tests/test_movement_report.py. This file declared a private copy of it
// until DATA-M8-2, with a reporter_name it never rendered.

interface DailyActivityTableProps {
    /** Max items to show. undefined = show all */
    limit?: number;
    /** Callback when "View All" is clicked */
    onViewAll?: () => void;
}

// ============================================================
// Helpers
// ============================================================

// DATA-H4-1. One table, three switches before this ticket -- and they had
// already fallen out of step with each other: VERIFICATION had a label arm but
// no icon or colour arm, so a verification row rendered with the fallback
// clock. Three switches means three edits per event type and three chances to
// forget one, which is how that gap arrived.
//
// The vocabulary itself was also fiction. These switches knew 'movement',
// 'transfer', 'repair' and 'verify'; `git log -S` over backend/ shows no
// router has ever written any of them. What the backend emits is
// backend/enums.py EventType -- HANDOVER, HANDOVER_LOC, VERIFICATION, FIX and
// now ASSIGN -- so every handover in the system fell through to the default
// arm and printed raw English into an RTL Hebrew table, for as long as the
// feature has existed. The dead arms are deleted rather than kept: there are
// no legacy rows carrying those strings to protect.
//
// tests/test_audit_trail.py asserts every EventType value has an entry here,
// so a backend member added without a label fails the backend suite.

type EventMeta = { icon: typeof Clock; color: string; label: string };

const EVENT_META: Record<string, EventMeta> = {
    handover: { icon: ArrowUpDown, color: 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10', label: 'העברה' },
    handover_loc: { icon: ArrowUpDown, color: 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10', label: 'העברה למיקום' },
    assign: { icon: UserCheck, color: 'text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-500/10', label: 'שינוי בעלות' },
    verification: { icon: ShieldCheck, color: 'text-sky-600 dark:text-sky-400 bg-sky-50 dark:bg-sky-500/10', label: 'אימות' },
    fix: { icon: Wrench, color: 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10', label: 'תיקון' },
    // DATA-H4-2. Amber against fix's emerald: a fault and its repair are the
    // two ends of one story and the eye should separate them at a glance.
    fault: { icon: AlertTriangle, color: 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10', label: 'דיווח תקלה' },
    // DATA-H4-3. Rose is the loudest swatch in this map and the only one that
    // is loud on purpose: a classification decision is the rarest event here
    // and the one an auditor scans for.
    reclassify: { icon: ShieldAlert, color: 'text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-500/10', label: 'שינוי סיווג' },
    // Slate, deliberately quiet. An item entering the inventory is the most
    // routine line the report carries.
    create: { icon: PackagePlus, color: 'text-slate-600 dark:text-slate-400 bg-slate-50 dark:bg-slate-500/10', label: 'יצירת פריט' },
    // Indigo against verification's sky, and adjacent on purpose -- the daily
    // presence check and the condition report are the same family and
    // different acts, which is exactly what the backend refuses to spell with
    // one EventType. Same reasoning as fault's amber against fix's emerald.
    condition_report: { icon: ClipboardCheck, color: 'text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-500/10', label: 'דיווח מצב' },
};

// Derived, not restated. This was a byte-for-byte copy of handover's colour,
// which is the drift this map was created to end -- retouch the blue in one
// place and the fallback silently stops matching the swatch it is meant to be.
const FALLBACK_COLOR = EVENT_META.handover.color;

function eventMeta(eventType: string): EventMeta | undefined {
    return EVENT_META[eventType?.toLowerCase()];
}

function getEventIcon(eventType: string) {
    const Icon = eventMeta(eventType)?.icon ?? Clock;
    return <Icon size={14} />;
}

function getEventColor(eventType: string) {
    return eventMeta(eventType)?.color ?? FALLBACK_COLOR;
}

function getEventLabel(eventType: string) {
    return eventMeta(eventType)?.label ?? (eventType || 'אירוע');
}

// The API returns a flat array, not { items: [...] }; the second shape is
// still accepted.
function activityList(data: unknown): MovementReportItem[] {
    if (Array.isArray(data)) return data;
    return (data as { items?: MovementReportItem[] } | null)?.items || [];
}

// Each end in its own <bdi>. The table is RTL, and two Latin names around a
// bare arrow would form one left-to-right run, putting `from` on the left
// with the arrow pointing at it.
//
// The arrow is held to the destination. At a common laptop width this cell
// is about 100px, and a free break after the arrow left it dangling at the
// end of the `from` line, pointing at nothing; held, the cell breaks into
// `from` over `← to`.
function Ends({ from, to }: { from: string; to: string }) {
    return <><bdi>{from}</bdi> <span className="whitespace-nowrap">← <bdi>{to}</bdi></span></>;
}

// ============================================================
// Component
// ============================================================

export default function DailyActivityTable({ limit, onViewAll }: DailyActivityTableProps) {
    const [activities, setActivities] = useState<MovementReportItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // FE-H5: unmounting aborts the request, and an aborted request is not
    // reported as a failure.
    useEffect(() => {
        const controller = new AbortController();
        const { signal } = controller;
        const fetchActivity = async () => {
            try {
                const res = await api.get('/reports/daily_movement', { signal });
                if (signal.aborted) return;
                setActivities(activityList(res.data));
            } catch (err) {
                if (signal.aborted) return;
                console.error("Failed to fetch daily activity", err);
                setError("טעינת יומן הפעילות נכשלה");
            }
            // An aborted request has returned above, so this runs only for
            // one that was answered or failed.
            setLoading(false);
        };

        fetchActivity();
        return () => controller.abort();
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
                            <th className="px-4 py-3 text-xs font-semibold text-muted-foreground">מדווח</th>
                            <th className="px-4 py-3 text-xs font-semibold text-muted-foreground">מ ← אל</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-border/30">
                        {displayItems.map((item, index) => {
                            const move = describeMove(item);
                            return (
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
                                {/* nowrap: the fifth column (DATA-M8-2) narrowed this one until a
                                    serial broke at its hyphen, CO- over A-3. */}
                                <td className="px-4 py-3 font-mono text-sm font-bold text-primary whitespace-nowrap">
                                    {item.serial_number || '—'}
                                </td>
                                <td className="px-4 py-3">
                                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${getEventColor(item.event_type)}`}>
                                        {getEventIcon(item.event_type)}
                                        {getEventLabel(item.event_type)}
                                    </span>
                                </td>
                                <td className="px-4 py-3 text-xs text-foreground" data-testid="reporter">
                                    {item.reporter_name ?? NONE}
                                </td>
                                <td className="px-4 py-3 text-xs" data-testid="move">
                                    <div className="text-foreground">
                                        {move ? <Ends from={move.from} to={move.to} /> : (item.location || NONE)}
                                    </div>
                                    {move?.unit && (
                                        <div className="text-muted-foreground" data-testid="unit">
                                            <Ends from={move.unit.from} to={move.unit.to} />
                                        </div>
                                    )}
                                    {item.reason && (
                                        <div className="text-muted-foreground" data-testid="reason">
                                            סיבה: {item.reason}
                                        </div>
                                    )}
                                </td>
                            </tr>
                            );
                        })}
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
