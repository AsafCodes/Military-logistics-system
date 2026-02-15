import { useState } from 'react';
import api from '@/api';

interface VerificationFormProps {
    equipmentId: number;
    currentStatus: string;
    isOpen: boolean;
    onClose: () => void;
    onSuccess: () => void;
}

export default function VerificationForm({ equipmentId, currentStatus, isOpen, onClose, onSuccess }: VerificationFormProps) {
    const [formData, setFormData] = useState({
        verification_type: 'presence_check',
        reported_status: currentStatus,
        findings: '',
        action_required: false
    });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError(null);

        try {
            await api.post('/verifications/', {
                equipment_id: equipmentId,
                ...formData
            });
            onSuccess();
            onClose();
        } catch (err) {
            setError('שגיאה בשמירת הדיווח');
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg shadow-xl w-full max-w-md">
                <div className="p-4 border-b flex justify-between items-center">
                    <h2 className="text-lg font-semibold">דיווח מצב ציוד #{equipmentId}</h2>
                    <button onClick={onClose} className="text-gray-500 hover:text-gray-700">✕</button>
                </div>

                <form onSubmit={handleSubmit} className="p-4 space-y-4">
                    {error && (
                        <div className="p-3 bg-red-100 text-red-700 rounded">{error}</div>
                    )}

                    <div>
                        <label className="block text-sm font-medium mb-1">סוג דיווח</label>
                        <select
                            value={formData.verification_type}
                            onChange={(e) => setFormData({ ...formData, verification_type: e.target.value })}
                            className="w-full border rounded p-2"
                        >
                            <option value="presence_check">בדיקת נוכחות</option>
                            <option value="condition_report">דיווח מצב</option>
                            <option value="transfer_verification">אימות העברה</option>
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">סטטוס מדווח</label>
                        <select
                            value={formData.reported_status}
                            onChange={(e) => setFormData({ ...formData, reported_status: e.target.value })}
                            className="w-full border rounded p-2"
                        >
                            <option value="Functional">תקין</option>
                            <option value="Malfunctioning">תקול</option>
                            <option value="In Repair">בתיקון</option>
                            <option value="Missing">חסר</option>
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">הערות (אופציונלי)</label>
                        <textarea
                            value={formData.findings}
                            onChange={(e) => setFormData({ ...formData, findings: e.target.value })}
                            className="w-full border rounded p-2 h-24"
                            placeholder="תיאור הממצאים..."
                        />
                    </div>

                    <div className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            id="action_required"
                            checked={formData.action_required}
                            onChange={(e) => setFormData({ ...formData, action_required: e.target.checked })}
                        />
                        <label htmlFor="action_required" className="text-sm">נדרש טיפול נוסף</label>
                    </div>

                    <div className="flex gap-2 pt-2">
                        <button
                            type="submit"
                            disabled={loading}
                            className="flex-1 bg-blue-600 text-white py-2 rounded hover:bg-blue-700 disabled:opacity-50"
                        >
                            {loading ? 'שומר...' : 'שלח דיווח'}
                        </button>
                        <button
                            type="button"
                            onClick={onClose}
                            className="px-4 py-2 border rounded hover:bg-gray-50"
                        >
                            ביטול
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
