import { useState, useEffect } from 'react';
import api from '@/api';
import type { FaultType } from '@/types';

// API-H6. The other end of report_fault's is_pending: a reporter without
// REPORT_STATUS over the item's group mints a fault type that EquipmentPage's
// dropdown hides until someone approves it. Before this page nothing in the
// interface could, so the queue only ever grew.
//
// Approve only, deliberately. Every pending type the shipped UI can mint comes
// from a fault report, and in practice that report opens a ticket under it --
// so DELETE (DATA-H7) refuses it with a 409. (Only in practice: report_fault
// commits the type before the ticket, so a report that fails in between leaves
// a ticketless one behind.) A Delete button here would almost always be
// refused.
//
// The route and nav item are registered only for MANAGE_CATALOG holders
// (App.tsx, AppShell.tsx), but the real gate is the backend's
// authz.require_global on both calls this page makes. A stale capabilities
// snapshot can still land someone here, so a 403 is handled as its own state.

type LoadError = 'forbidden' | 'network';

export default function FaultTypeQueuePage() {
    const [pending, setPending] = useState<FaultType[]>([]);
    // Starts true for the reason AdminPanel's groupsLoading does: the render
    // before the first fetch settles must not claim the queue is empty.
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<LoadError | null>(null);
    // Per row, so approving one type leaves the others pressable, and a second
    // click on the same row cannot send a second PUT while the first is out.
    const [busy, setBusy] = useState<ReadonlySet<number>>(new Set());

    useEffect(() => {
        fetchPending();
    }, []);

    const fetchPending = async () => {
        setLoading(true);
        try {
            const res = await api.get('/setup/fault_types/pending');
            setPending(res.data);
            setError(null);
        } catch (err) {
            console.error('Failed to fetch pending fault types', err);
            const status = (err as { response?: { status?: number } })?.response?.status;
            setError(status === 403 ? 'forbidden' : 'network');
        } finally {
            setLoading(false);
        }
    };

    const setRowBusy = (id: number, on: boolean) => {
        setBusy(prev => {
            const next = new Set(prev);
            if (on) next.add(id); else next.delete(id);
            return next;
        });
    };

    const dropRow = (id: number) => {
        setPending(prev => prev.filter(f => f.id !== id));
    };

    // No busy check in here: the button is disabled while its request is out,
    // and React dispatches no click to a disabled button, so a check would be
    // unreachable. The disabled attribute is the guard.
    const handleApprove = async (fault: FaultType) => {
        setRowBusy(fault.id, true);
        try {
            await api.put(`/setup/fault_types/${fault.id}/approve`);
            dropRow(fault.id);
        } catch (err) {
            const status = (err as { response?: { status?: number } })?.response?.status;
            if (status === 404) {
                // Gone already (deleted). Approve answers 404 for nothing else,
                // so the type is no longer in the queue, and neither is its row.
                dropRow(fault.id);
            } else {
                console.error('Failed to approve fault type', err);
                alert('אישור סוג התקלה נכשל.');
            }
        } finally {
            setRowBusy(fault.id, false);
        }
    };

    return (
        <div className="space-y-6 animate-fade-in" dir="rtl">
            <div className="glass-card p-6">
                <h2 className="text-xl font-bold text-foreground mb-1">📋 אישור סוגי תקלות</h2>
                <p className="text-sm text-muted-foreground">
                    סוגי תקלות חדשים שדווחו וממתינים לאישור. אישור מוסיף את הסוג לרשימת הבחירה בדיווח תקלה.
                </p>
            </div>

            <div className="glass-card overflow-hidden">
                <div className="px-6 py-4 border-b border-border/30">
                    <h3 className="font-bold text-foreground">ממתינים לאישור</h3>
                </div>
                <div className="p-6">
                    {loading ? (
                        <div className="p-3 text-sm text-muted-foreground">טוען...</div>
                    ) : error ? (
                        <div className="p-3 rounded-lg border border-destructive/30 bg-destructive/5 space-y-2">
                            <p className="text-sm text-destructive">
                                {error === 'forbidden'
                                    ? 'אין לך הרשאה לצפות בתור האישורים.'
                                    : 'טעינת התור נכשלה.'}
                            </p>
                            {error === 'network' && (
                                <button
                                    onClick={fetchPending}
                                    className="text-sm text-primary hover:underline"
                                >
                                    נסה שוב
                                </button>
                            )}
                        </div>
                    ) : pending.length === 0 ? (
                        <div className="text-center text-muted-foreground p-4">
                            אין סוגי תקלות הממתינים לאישור.
                        </div>
                    ) : (
                        <ul className="space-y-2">
                            {pending.map(f => (
                                <li
                                    key={f.id}
                                    className="flex items-center justify-between gap-4 p-3 rounded-lg border border-border/30"
                                >
                                    <span className="font-medium text-foreground">{f.name}</span>
                                    <button
                                        onClick={() => handleApprove(f)}
                                        disabled={busy.has(f.id)}
                                        // Every row's button reads the same; the
                                        // name is what tells them apart to a
                                        // screen reader.
                                        aria-label={`אשר את ${f.name}`}
                                        className="px-4 py-1.5 rounded-lg bg-primary text-primary-foreground
                                                   hover:bg-primary/90 font-bold text-sm transition-colors
                                                   disabled:opacity-50 disabled:cursor-not-allowed"
                                    >
                                        אשר
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </div>
        </div>
    );
}
