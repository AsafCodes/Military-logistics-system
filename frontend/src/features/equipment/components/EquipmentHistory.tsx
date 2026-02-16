import { useState, useEffect } from 'react';
import api from '@/api';

interface StatusHistoryItem {
    id: number;
    equipment_id: number;
    old_status: string;
    new_status: string;
    change_reason: string;
    verification_id: number | null;
    notes: string | null;
    created_date: string;
    created_by: number;
    user_name: string | null;
}

interface EquipmentHistoryProps {
    equipmentId: number;
    isOpen: boolean;
    onClose: () => void;
}

export default function EquipmentHistory({ equipmentId, isOpen, onClose }: EquipmentHistoryProps) {
    const [history, setHistory] = useState<StatusHistoryItem[]>([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (isOpen && equipmentId) {
            fetchHistory();
        }
    }, [isOpen, equipmentId]);

    const fetchHistory = async () => {
        setLoading(true);
        try {
            const response = await api.get(`/equipment/${equipmentId}/history`);
            setHistory(response.data);
        } catch (error) {
            console.error('Failed to fetch history:', error);
        } finally {
            setLoading(false);
        }
    };

    if (!isOpen) return null;

    const getStatusColor = (status: string) => {
        switch (status.toLowerCase()) {
            case 'functional': return 'text-emerald-700 dark:text-emerald-400 bg-emerald-100 dark:bg-emerald-500/10';
            case 'malfunctioning': return 'text-red-700 dark:text-red-400 bg-red-100 dark:bg-red-500/10';
            default: return 'text-muted-foreground bg-muted';
        }
    };

    const getReasonIcon = (reason: string) => {
        switch (reason) {
            case 'verification': return '✅';
            case 'fault_report': return '⚠️';
            case 'repair': return '🔧';
            case 'transfer': return '🔄';
            default: return '📝';
        }
    };

    const getReasonLabel = (reason: string) => {
        switch (reason) {
            case 'verification': return 'אימות';
            case 'fault_report': return 'דיווח תקלה';
            case 'repair': return 'תיקון';
            case 'transfer': return 'העברה';
            default: return reason;
        }
    };

    return (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4 backdrop-blur-sm">
            <div className="glass-card w-full max-w-2xl max-h-[80vh] overflow-hidden animate-scale-in" dir="rtl">
                <div className="flex items-center justify-between px-6 py-4 border-b border-border/30">
                    <h2 className="text-lg font-bold text-foreground">📜 היסטוריית ציוד #{equipmentId}</h2>
                    <button
                        onClick={onClose}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                        ✕
                    </button>
                </div>

                <div className="p-6 overflow-y-auto max-h-[60vh]">
                    {loading ? (
                        <div className="text-center py-8">
                            <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent mx-auto" />
                            <p className="text-sm text-muted-foreground mt-2">טוען...</p>
                        </div>
                    ) : history.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">אין היסטוריה זמינה</div>
                    ) : (
                        <div className="space-y-3">
                            {history.map((item) => (
                                <div
                                    key={item.id}
                                    className="border-r-4 border-primary/50 pr-4 py-3 rounded-lg
                                               bg-accent/30 hover:bg-accent/50 transition-colors"
                                >
                                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                        <span>{getReasonIcon(item.change_reason)}</span>
                                        <span className="font-medium text-foreground/80">
                                            {getReasonLabel(item.change_reason)}
                                        </span>
                                        <span>•</span>
                                        <span>
                                            {new Date(item.created_date).toLocaleString('he-IL')}
                                        </span>
                                        {item.user_name && (
                                            <>
                                                <span>•</span>
                                                <span className="text-foreground/60">{item.user_name}</span>
                                            </>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-2 mt-2">
                                        <span className={`px-2 py-0.5 rounded text-xs font-bold ${getStatusColor(item.old_status)}`}>
                                            {item.old_status}
                                        </span>
                                        <span className="text-muted-foreground">→</span>
                                        <span className={`px-2 py-0.5 rounded text-xs font-bold ${getStatusColor(item.new_status)}`}>
                                            {item.new_status}
                                        </span>
                                    </div>
                                    {item.notes && (
                                        <p className="text-sm text-muted-foreground mt-1.5 pr-1">{item.notes}</p>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
