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
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4 backdrop-blur-sm">
            <div className="glass-card w-full max-w-md animate-scale-in" dir="rtl">
                <div className="flex items-center justify-between px-6 py-4 border-b border-border/30">
                    <h2 className="text-lg font-bold text-foreground">🔍 דיווח מצב ציוד #{equipmentId}</h2>
                    <button
                        onClick={onClose}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                        ✕
                    </button>
                </div>

                <form onSubmit={handleSubmit} className="p-6 space-y-4">
                    {error && (
                        <div className="p-3 rounded-lg bg-red-100 dark:bg-red-500/10 text-red-700 dark:text-red-400 text-sm">
                            {error}
                        </div>
                    )}

                    <div>
                        <label className="block text-sm font-medium text-foreground mb-1">סוג דיווח</label>
                        <select
                            value={formData.verification_type}
                            onChange={(e) => setFormData({ ...formData, verification_type: e.target.value })}
                            className="w-full px-3 py-2 rounded-lg border border-border/50
                                       bg-background text-foreground
                                       focus:ring-2 focus:ring-primary/50 outline-none transition-colors"
                        >
                            <option value="presence_check">בדיקת נוכחות</option>
                            <option value="condition_report">דיווח מצב</option>
                            <option value="transfer_verification">אימות העברה</option>
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-foreground mb-1">סטטוס מדווח</label>
                        <select
                            value={formData.reported_status}
                            onChange={(e) => setFormData({ ...formData, reported_status: e.target.value })}
                            className="w-full px-3 py-2 rounded-lg border border-border/50
                                       bg-background text-foreground
                                       focus:ring-2 focus:ring-primary/50 outline-none transition-colors"
                        >
                            <option value="Functional">תקין</option>
                            <option value="Malfunctioning">תקול</option>
                            <option value="In Repair">בתיקון</option>
                            <option value="Missing">חסר</option>
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-foreground mb-1">הערות (אופציונלי)</label>
                        <textarea
                            value={formData.findings}
                            onChange={(e) => setFormData({ ...formData, findings: e.target.value })}
                            className="w-full px-3 py-2 rounded-lg border border-border/50
                                       bg-background text-foreground
                                       focus:ring-2 focus:ring-primary/50 outline-none
                                       h-24 resize-none transition-colors
                                       placeholder:text-muted-foreground/50"
                            placeholder="תיאור הממצאים..."
                        />
                    </div>

                    <div className="flex items-center gap-2">
                        <input
                            type="checkbox"
                            id="action_required"
                            checked={formData.action_required}
                            onChange={(e) => setFormData({ ...formData, action_required: e.target.checked })}
                            className="rounded border-border/50 text-primary
                                       focus:ring-primary/50"
                        />
                        <label htmlFor="action_required" className="text-sm text-foreground">
                            נדרש טיפול נוסף
                        </label>
                    </div>

                    <div className="flex gap-3 pt-2">
                        <button
                            type="submit"
                            disabled={loading}
                            className="flex-1 py-2 rounded-lg bg-primary text-primary-foreground
                                       hover:bg-primary/90 disabled:opacity-50 font-bold
                                       transition-colors"
                        >
                            {loading ? 'שומר...' : 'שלח דיווח'}
                        </button>
                        <button
                            type="button"
                            onClick={onClose}
                            className="px-4 py-2 rounded-lg border border-border/50
                                       text-foreground hover:bg-accent transition-colors"
                        >
                            ביטול
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
