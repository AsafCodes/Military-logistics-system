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
            case 'functional': return 'bg-green-100 text-green-800';
            case 'malfunctioning': return 'bg-red-100 text-red-800';
            default: return 'bg-gray-100 text-gray-800';
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

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[80vh] overflow-hidden">
                <div className="p-4 border-b flex justify-between items-center">
                    <h2 className="text-lg font-semibold">היסטוריית סטטוס ציוד #{equipmentId}</h2>
                    <button onClick={onClose} className="text-gray-500 hover:text-gray-700">✕</button>
                </div>

                <div className="p-4 overflow-y-auto max-h-[60vh]">
                    {loading ? (
                        <div className="text-center py-8">טוען...</div>
                    ) : history.length === 0 ? (
                        <div className="text-center py-8 text-gray-500">אין היסטוריה זמינה</div>
                    ) : (
                        <div className="space-y-4">
                            {history.map((item) => (
                                <div key={item.id} className="border-r-4 border-blue-500 pr-4 py-2">
                                    <div className="flex items-center gap-2 text-sm text-gray-500">
                                        <span>{getReasonIcon(item.change_reason)}</span>
                                        <span>{new Date(item.created_date).toLocaleString('he-IL')}</span>
                                        {item.user_name && <span>• {item.user_name}</span>}
                                    </div>
                                    <div className="flex items-center gap-2 mt-1">
                                        <span className={`px-2 py-0.5 rounded text-xs ${getStatusColor(item.old_status)}`}>
                                            {item.old_status}
                                        </span>
                                        <span>→</span>
                                        <span className={`px-2 py-0.5 rounded text-xs ${getStatusColor(item.new_status)}`}>
                                            {item.new_status}
                                        </span>
                                    </div>
                                    {item.notes && (
                                        <p className="text-sm text-gray-600 mt-1">{item.notes}</p>
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
