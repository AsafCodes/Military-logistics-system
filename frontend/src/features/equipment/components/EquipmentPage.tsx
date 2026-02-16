import { useState, useEffect, useMemo, useCallback } from 'react';
import { Package, Search, Filter, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react';
import api from '@/api';
import type { Equipment, User, FaultType } from '@/types';
import EquipmentHistory from './EquipmentHistory';
import VerificationForm from './VerificationForm';

// ============================================================
// Sub-Components: Modals
// ============================================================

/* ---------- Report Fault Modal ---------- */
function ReportFaultModal({
    item, faultTypes, onClose, onSuccess
}: {
    item: Equipment;
    faultTypes: FaultType[];
    onClose: () => void;
    onSuccess: () => void;
}) {
    const [selectedFaultId, setSelectedFaultId] = useState('');
    const [customFaultName, setCustomFaultName] = useState('');
    const [description, setDescription] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async () => {
        setLoading(true);
        try {
            const isOther = selectedFaultId === 'other';
            await api.post('/maintenance/report', {
                equipment_id: item.id,
                fault_name: isOther ? customFaultName : faultTypes.find(f => f.id === parseInt(selectedFaultId))?.name,
                description,
            });
            onSuccess();
            onClose();
        } catch (err) {
            console.error(err);
            alert('דיווח התקלה נכשל');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
            <div className="glass-card w-full max-w-md animate-scale-in" dir="rtl">
                <div className="flex items-center justify-between px-6 py-4 border-b border-border/30">
                    <h3 className="font-bold text-foreground">⚠ דיווח תקלה — {item.type}</h3>
                    <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
                </div>
                <div className="p-6 space-y-4">
                    <div className="text-sm text-muted-foreground">
                        צד״ק: <span className="font-mono text-primary">{item.serial_number}</span>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-foreground mb-1">סוג תקלה</label>
                        <select
                            value={selectedFaultId}
                            onChange={e => setSelectedFaultId(e.target.value)}
                            className="w-full px-3 py-2 rounded-lg border border-border/50 bg-background text-foreground
                                       focus:ring-2 focus:ring-primary/50 outline-none transition-colors"
                        >
                            <option value="">-- בחר סוג תקלה --</option>
                            {faultTypes.filter(f => !f.is_pending).map(f => (
                                <option key={f.id} value={f.id}>{f.name}</option>
                            ))}
                            <option value="other">אחר (חדש)</option>
                        </select>
                    </div>
                    {selectedFaultId === 'other' && (
                        <div>
                            <label className="block text-sm font-medium text-foreground mb-1">שם תקלה חדש</label>
                            <input
                                type="text"
                                value={customFaultName}
                                onChange={e => setCustomFaultName(e.target.value)}
                                className="w-full px-3 py-2 rounded-lg border border-border/50 bg-background text-foreground
                                           focus:ring-2 focus:ring-primary/50 outline-none"
                                placeholder="הזן שם תקלה..."
                            />
                        </div>
                    )}
                    <div>
                        <label className="block text-sm font-medium text-foreground mb-1">תיאור</label>
                        <textarea
                            value={description}
                            onChange={e => setDescription(e.target.value)}
                            className="w-full px-3 py-2 rounded-lg border border-border/50 bg-background text-foreground
                                       focus:ring-2 focus:ring-primary/50 outline-none h-20 resize-none"
                            placeholder="תאר את התקלה..."
                        />
                    </div>
                    <div className="flex gap-3 pt-2">
                        <button onClick={onClose}
                            className="flex-1 px-4 py-2 rounded-lg border border-border/50 text-foreground hover:bg-accent transition-colors">
                            ביטול
                        </button>
                        <button onClick={handleSubmit} disabled={loading || (!selectedFaultId || (selectedFaultId === 'other' && !customFaultName))}
                            className="flex-1 px-4 py-2 rounded-lg bg-red-600 text-white hover:bg-red-700
                                       disabled:opacity-50 font-bold transition-colors">
                            {loading ? 'שולח...' : 'דווח תקלה'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}

/* ---------- Transfer Modal ---------- */
function TransferModal({
    item, onClose, onSuccess
}: {
    item: Equipment;
    onClose: () => void;
    onSuccess: () => void;
}) {
    const [mode, setMode] = useState<'person' | 'location'>('person');
    const [searchTerm, setSearchTerm] = useState('');
    const [searchResults, setSearchResults] = useState<User[]>([]);
    const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
    const [locationName, setLocationName] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (mode === 'person' && searchTerm.length > 1) {
            const timer = setTimeout(async () => {
                try {
                    const res = await api.get(`/users?q=${searchTerm}`);
                    setSearchResults(res.data);
                } catch (e) { console.error(e); }
            }, 300);
            return () => clearTimeout(timer);
        } else {
            setSearchResults([]);
        }
    }, [searchTerm, mode]);

    const handleSubmit = async () => {
        setLoading(true);
        try {
            await api.post('/equipment/transfer', {
                equipment_id: item.id,
                ...(mode === 'person' ? { to_holder_id: selectedUserId } : { to_location: locationName }),
            });
            onSuccess();
            onClose();
        } catch (err) {
            console.error(err);
            alert('העברה נכשלה');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
            <div className="glass-card w-full max-w-md animate-scale-in" dir="rtl">
                <div className="flex items-center justify-between px-6 py-4 border-b border-border/30">
                    <h3 className="font-bold text-foreground">🔄 העברת ציוד — {item.type}</h3>
                    <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
                </div>
                <div className="p-6 space-y-4">
                    <div className="text-sm text-muted-foreground">
                        צד״ק: <span className="font-mono text-primary">{item.serial_number}</span>
                    </div>
                    {/* Mode Toggle */}
                    <div className="flex rounded-lg border border-border/50 overflow-hidden">
                        <button
                            onClick={() => setMode('person')}
                            className={`flex-1 px-4 py-2 text-sm font-medium transition-colors
                                ${mode === 'person' ? 'bg-primary text-primary-foreground' : 'bg-background text-muted-foreground hover:bg-accent'}`}>
                            👤 העבר לאדם
                        </button>
                        <button
                            onClick={() => setMode('location')}
                            className={`flex-1 px-4 py-2 text-sm font-medium transition-colors
                                ${mode === 'location' ? 'bg-primary text-primary-foreground' : 'bg-background text-muted-foreground hover:bg-accent'}`}>
                            📍 העבר למיקום
                        </button>
                    </div>

                    {mode === 'person' ? (
                        <div className="space-y-3">
                            <input
                                type="text"
                                value={searchTerm}
                                onChange={e => { setSearchTerm(e.target.value); setSelectedUserId(null); }}
                                className="w-full px-3 py-2 rounded-lg border border-border/50 bg-background text-foreground
                                           focus:ring-2 focus:ring-primary/50 outline-none"
                                placeholder="חפש משתמש..."
                            />
                            {searchResults.length > 0 && (
                                <div className="border border-border/30 rounded-lg max-h-40 overflow-y-auto bg-background">
                                    {searchResults.map(u => (
                                        <button key={u.id}
                                            onClick={() => { setSelectedUserId(u.id); setSearchTerm(u.full_name); setSearchResults([]); }}
                                            className={`w-full text-right px-3 py-2 text-sm hover:bg-accent transition-colors
                                                ${selectedUserId === u.id ? 'bg-primary/10 text-primary' : 'text-foreground'}`}>
                                            {u.full_name} <span className="text-xs text-muted-foreground">({u.personal_number})</span>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    ) : (
                        <input
                            type="text"
                            value={locationName}
                            onChange={e => setLocationName(e.target.value)}
                            className="w-full px-3 py-2 rounded-lg border border-border/50 bg-background text-foreground
                                       focus:ring-2 focus:ring-primary/50 outline-none"
                            placeholder="שם מיקום..."
                        />
                    )}

                    <div className="flex gap-3 pt-2">
                        <button onClick={onClose}
                            className="flex-1 px-4 py-2 rounded-lg border border-border/50 text-foreground hover:bg-accent transition-colors">
                            ביטול
                        </button>
                        <button onClick={handleSubmit}
                            disabled={loading || (mode === 'person' ? !selectedUserId : !locationName)}
                            className="flex-1 px-4 py-2 rounded-lg bg-primary text-primary-foreground
                                       hover:bg-primary/90 disabled:opacity-50 font-bold transition-colors">
                            {loading ? 'מעביר...' : 'אשר העברה'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}

/* ---------- Assign Owner Modal ---------- */
function AssignOwnerModal({
    item, onClose, onSuccess
}: {
    item: Equipment;
    onClose: () => void;
    onSuccess: () => void;
}) {
    const [searchTerm, setSearchTerm] = useState('');
    const [searchResults, setSearchResults] = useState<User[]>([]);
    const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (searchTerm.length > 1) {
            const timer = setTimeout(async () => {
                try {
                    const res = await api.get(`/users?q=${searchTerm}`);
                    setSearchResults(res.data);
                } catch (e) { console.error(e); }
            }, 300);
            return () => clearTimeout(timer);
        } else {
            setSearchResults([]);
        }
    }, [searchTerm]);

    const handleSubmit = async () => {
        if (!selectedUserId) return;
        setLoading(true);
        try {
            await api.post('/equipment/assign_owner/', {
                equipment_id: item.id,
                owner_id: selectedUserId,
            });
            onSuccess();
            onClose();
        } catch (err) {
            console.error(err);
            alert('שיוך נכשל');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
            <div className="glass-card w-full max-w-md animate-scale-in" dir="rtl">
                <div className="flex items-center justify-between px-6 py-4 border-b border-border/30">
                    <h3 className="font-bold text-foreground">👤 שיוך בעלים — {item.type}</h3>
                    <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
                </div>
                <div className="p-6 space-y-4">
                    <div className="text-sm text-muted-foreground">
                        צד״ק: <span className="font-mono text-primary">{item.serial_number}</span>
                    </div>
                    <div className="space-y-3">
                        <input
                            type="text"
                            value={searchTerm}
                            onChange={e => { setSearchTerm(e.target.value); setSelectedUserId(null); }}
                            className="w-full px-3 py-2 rounded-lg border border-border/50 bg-background text-foreground
                                       focus:ring-2 focus:ring-primary/50 outline-none"
                            placeholder="חפש משתמש לשיוך..."
                        />
                        {searchResults.length > 0 && (
                            <div className="border border-border/30 rounded-lg max-h-40 overflow-y-auto bg-background">
                                {searchResults.map(u => (
                                    <button key={u.id}
                                        onClick={() => { setSelectedUserId(u.id); setSearchTerm(u.full_name); setSearchResults([]); }}
                                        className={`w-full text-right px-3 py-2 text-sm hover:bg-accent transition-colors
                                            ${selectedUserId === u.id ? 'bg-primary/10 text-primary' : 'text-foreground'}`}>
                                        {u.full_name} <span className="text-xs text-muted-foreground">({u.personal_number})</span>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                    <div className="flex gap-3 pt-2">
                        <button onClick={onClose}
                            className="flex-1 px-4 py-2 rounded-lg border border-border/50 text-foreground hover:bg-accent transition-colors">
                            ביטול
                        </button>
                        <button onClick={handleSubmit} disabled={loading || !selectedUserId}
                            className="flex-1 px-4 py-2 rounded-lg bg-violet-600 text-white
                                       hover:bg-violet-700 disabled:opacity-50 font-bold transition-colors">
                            {loading ? 'משייך...' : 'שייך בעלים'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}

// ============================================================
// Status/Compliance Helpers
// ============================================================

const getStatusBadge = (status: string) => {
    switch (status) {
        case 'Functional': return { label: 'תקין', cls: 'text-emerald-700 dark:text-emerald-400 bg-emerald-100 dark:bg-emerald-500/10 border-emerald-200 dark:border-emerald-500/20' };
        case 'Malfunctioning': return { label: 'תקול', cls: 'text-red-700 dark:text-red-400 bg-red-100 dark:bg-red-500/10 border-red-200 dark:border-red-500/20' };
        default: return { label: status, cls: 'text-muted-foreground bg-muted border-border/30' };
    }
};

const getComplianceBadge = (level: string) => {
    switch (level) {
        case 'GOOD': return { icon: '✓', label: 'תקין', cls: 'text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10' };
        case 'WARNING': return { icon: '⚠', label: 'אזהרה', cls: 'text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10' };
        case 'SEVERE': return { icon: '✕', label: 'חמור', cls: 'text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-500/10' };
        default: return { icon: '—', label: 'ניטרלי', cls: 'text-muted-foreground bg-muted' };
    }
};

// ============================================================
// Main Equipment Page
// ============================================================

export default function EquipmentPage() {
    // Data State
    const [user, setUser] = useState<User | null>(null);
    const [equipment, setEquipment] = useState<Equipment[]>([]);
    const [faultTypes, setFaultTypes] = useState<FaultType[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Filter State
    const [searchQuery, setSearchQuery] = useState('');
    const [statusFilter, setStatusFilter] = useState<string>('all');
    const [typeFilter, setTypeFilter] = useState<string>('all');

    // Modal State
    const [faultTarget, setFaultTarget] = useState<Equipment | null>(null);
    const [transferTarget, setTransferTarget] = useState<Equipment | null>(null);
    const [assignTarget, setAssignTarget] = useState<Equipment | null>(null);
    const [verifyTarget, setVerifyTarget] = useState<Equipment | null>(null);
    const [historyTargetId, setHistoryTargetId] = useState<number | null>(null);

    // Expanded Row
    const [expandedRowId, setExpandedRowId] = useState<number | null>(null);

    // ── Data Fetching ──
    const fetchData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [userRes, equipRes, faultRes] = await Promise.all([
                api.get('/users/me'),
                api.get('/equipment/accessible'),
                api.get('/setup/fault_types'),
            ]);
            setUser(userRes.data);
            setEquipment(equipRes.data);
            setFaultTypes(faultRes.data);
        } catch (err) {
            console.error(err);
            setError('טעינת נתוני ציוד נכשלה');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { fetchData(); }, [fetchData]);

    // ── Permissions ──
    const canManageAssets = user?.profile?.can_change_assignment_others || user?.role === 'master';
    const userRole = user?.role || 'user';

    // ── Derived Data ──
    const uniqueTypes = useMemo(() => {
        const types = new Set(equipment.map(e => e.type));
        return Array.from(types).sort();
    }, [equipment]);

    const filteredEquipment = useMemo(() => {
        return equipment.filter(item => {
            if (statusFilter !== 'all' && item.status !== statusFilter) return false;
            if (typeFilter !== 'all' && item.type !== typeFilter) return false;
            if (searchQuery) {
                const q = searchQuery.toLowerCase();
                const matchSerial = item.serial_number?.toLowerCase().includes(q);
                const matchType = item.type.toLowerCase().includes(q);
                const matchDesc = item.current_state_description?.toLowerCase().includes(q);
                if (!matchSerial && !matchType && !matchDesc) return false;
            }
            return true;
        });
    }, [equipment, statusFilter, typeFilter, searchQuery]);

    // ── Action Handlers ──
    const handleVerifyPresence = async (id: number) => {
        try {
            await api.post(`/equipment/${id}/verify`);
            fetchData();
        } catch (err) {
            console.error(err);
            alert('דיווח נוכחות נכשל');
        }
    };

    const handleRepair = async (item: Equipment) => {
        try {
            await api.post(`/maintenance/fix/${item.id}`);
            fetchData();
        } catch (err) {
            console.error(err);
            alert('תיקון נכשל');
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

    return (
        <div className="space-y-6 animate-fade-in" dir="rtl">
            {/* ── Header ── */}
            <div className="glass-card p-6">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 rounded-lg bg-primary/10">
                            <Package size={20} className="text-primary" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-foreground">ניהול ציוד</h2>
                            <p className="text-sm text-muted-foreground">{equipment.length} פריטים • {filteredEquipment.length} מוצגים</p>
                        </div>
                    </div>
                    <button
                        onClick={fetchData}
                        className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                                   text-primary hover:bg-primary/10 transition-colors"
                    >
                        <RefreshCw size={16} />
                        <span className="hidden sm:inline">רענן</span>
                    </button>
                </div>
            </div>

            {/* ── Filters ── */}
            <div className="glass-card p-4">
                <div className="flex flex-wrap gap-3 items-center">
                    {/* Search */}
                    <div className="relative flex-1 min-w-[200px]">
                        <Search size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={e => setSearchQuery(e.target.value)}
                            className="w-full pr-10 pl-3 py-2 rounded-lg border border-border/50 bg-background text-foreground
                                       text-sm focus:ring-2 focus:ring-primary/50 outline-none placeholder:text-muted-foreground/50
                                       transition-colors"
                            placeholder="חפש לפי צד״ק, סוג, תיאור..."
                        />
                    </div>

                    {/* Status Filter */}
                    <div className="flex items-center gap-1.5">
                        <Filter size={14} className="text-muted-foreground" />
                        <select
                            value={statusFilter}
                            onChange={e => setStatusFilter(e.target.value)}
                            className="px-3 py-2 rounded-lg border border-border/50 bg-background text-foreground
                                       text-sm focus:ring-2 focus:ring-primary/50 outline-none transition-colors"
                        >
                            <option value="all">כל הסטטוסים</option>
                            <option value="Functional">✅ תקין</option>
                            <option value="Malfunctioning">❌ תקול</option>
                        </select>
                    </div>

                    {/* Type Filter */}
                    <select
                        value={typeFilter}
                        onChange={e => setTypeFilter(e.target.value)}
                        className="px-3 py-2 rounded-lg border border-border/50 bg-background text-foreground
                                   text-sm focus:ring-2 focus:ring-primary/50 outline-none transition-colors"
                    >
                        <option value="all">כל הסוגים</option>
                        {uniqueTypes.map(t => (
                            <option key={t} value={t}>{t}</option>
                        ))}
                    </select>
                </div>
            </div>

            {/* ── Equipment Table ── */}
            <div className="glass-card overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-right">
                        <thead>
                            <tr className="text-xs uppercase font-semibold border-b border-border/40 text-muted-foreground bg-card">
                                {userRole === 'master' && <th className="px-4 py-3">#</th>}
                                <th className="px-4 py-3">סוג</th>
                                <th className="px-4 py-3">צד״ק</th>
                                <th className="px-4 py-3">סטטוס</th>
                                <th className="px-4 py-3">תיאור</th>
                                <th className="px-4 py-3">ציות</th>
                                <th className="px-4 py-3">פעולות</th>
                                <th className="px-4 py-3 w-10"></th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-border/20 text-sm">
                            {filteredEquipment.length > 0 ? (
                                filteredEquipment.map(item => {
                                    const status = getStatusBadge(item.status);
                                    const compliance = getComplianceBadge(item.compliance_level);
                                    const isExpanded = expandedRowId === item.id;

                                    return (
                                        <EquipmentRow
                                            key={item.id}
                                            item={item}
                                            user={user}
                                            userRole={userRole}
                                            canManageAssets={!!canManageAssets}
                                            status={status}
                                            compliance={compliance}
                                            isExpanded={isExpanded}
                                            onToggleExpand={() => setExpandedRowId(isExpanded ? null : item.id)}
                                            onVerifyPresence={() => handleVerifyPresence(item.id)}
                                            onRepair={() => handleRepair(item)}
                                            onReportFault={() => setFaultTarget(item)}
                                            onTransfer={() => setTransferTarget(item)}
                                            onAssign={() => setAssignTarget(item)}
                                            onViewHistory={() => setHistoryTargetId(item.id)}
                                            onVerifyForm={() => setVerifyTarget(item)}
                                        />
                                    );
                                })
                            ) : (
                                <tr>
                                    <td colSpan={8} className="px-6 py-12 text-center text-muted-foreground">
                                        {searchQuery || statusFilter !== 'all' || typeFilter !== 'all'
                                            ? 'אין ציוד התואם את הסינון'
                                            : 'אין ציוד להצגה'}
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* ── Modals ── */}
            {faultTarget && (
                <ReportFaultModal
                    item={faultTarget}
                    faultTypes={faultTypes}
                    onClose={() => setFaultTarget(null)}
                    onSuccess={fetchData}
                />
            )}
            {transferTarget && (
                <TransferModal
                    item={transferTarget}
                    onClose={() => setTransferTarget(null)}
                    onSuccess={fetchData}
                />
            )}
            {assignTarget && (
                <AssignOwnerModal
                    item={assignTarget}
                    onClose={() => setAssignTarget(null)}
                    onSuccess={fetchData}
                />
            )}
            {historyTargetId && (
                <EquipmentHistory
                    equipmentId={historyTargetId}
                    isOpen={true}
                    onClose={() => setHistoryTargetId(null)}
                />
            )}
            {verifyTarget && (
                <VerificationForm
                    equipmentId={verifyTarget.id}
                    currentStatus={verifyTarget.status}
                    isOpen={true}
                    onClose={() => setVerifyTarget(null)}
                    onSuccess={fetchData}
                />
            )}
        </div>
    );
}

// ============================================================
// Equipment Row (extracted for clarity)
// ============================================================

function EquipmentRow({
    item, user, userRole, canManageAssets, status, compliance, isExpanded,
    onToggleExpand, onVerifyPresence, onRepair, onReportFault, onTransfer,
    onAssign, onViewHistory, onVerifyForm
}: {
    item: Equipment;
    user: User | null;
    userRole: string;
    canManageAssets: boolean;
    status: { label: string; cls: string };
    compliance: { icon: string; label: string; cls: string };
    isExpanded: boolean;
    onToggleExpand: () => void;
    onVerifyPresence: () => void;
    onRepair: () => void;
    onReportFault: () => void;
    onTransfer: () => void;
    onAssign: () => void;
    onViewHistory: () => void;
    onVerifyForm: () => void;
}) {
    return (
        <>
            <tr className="hover:bg-accent/40 transition-colors group">
                {userRole === 'master' && (
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">#{item.id}</td>
                )}
                <td className="px-4 py-3 font-medium text-foreground">{item.type}</td>
                <td className="px-4 py-3 font-mono text-sm text-primary whitespace-nowrap">
                    {item.serial_number || '—'}
                </td>
                <td className="px-4 py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold border ${status.cls}`}>
                        {status.label}
                    </span>
                </td>
                <td className="px-4 py-3 text-muted-foreground text-sm max-w-[200px] truncate">
                    {item.holder_user_id === user?.id && item.holder_user_id !== undefined
                        ? <span className="text-primary font-medium">משויך אליך</span>
                        : item.current_state_description
                    }
                </td>
                <td className="px-4 py-3">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold ${compliance.cls}`}>
                        {compliance.icon} {compliance.label}
                    </span>
                    {item.compliance_level !== 'NEUTRAL' && item.compliance_check && (
                        <div className="text-[10px] text-muted-foreground mt-0.5">{item.compliance_check}</div>
                    )}
                </td>
                <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1.5">
                        {/* Verify presence */}
                        {item.holder_user_id === user?.id && (item.compliance_level === 'WARNING' || item.compliance_level === 'SEVERE') && (
                            <ActionBtn color="indigo" onClick={onVerifyPresence}>דווח נוכחות</ActionBtn>
                        )}
                        {/* Repair */}
                        {item.status === 'Malfunctioning' && user?.profile?.can_change_maintenance_status && (
                            <ActionBtn color="emerald" onClick={onRepair}>תקן</ActionBtn>
                        )}
                        {/* Report Fault */}
                        {item.status === 'Functional' && (
                            <ActionBtn color="red" onClick={onReportFault}>דווח תקלה</ActionBtn>
                        )}
                        {/* Transfer & Assign */}
                        {canManageAssets && (
                            <>
                                <ActionBtn color="blue" onClick={onTransfer}>העבר</ActionBtn>
                                <ActionBtn color="violet" onClick={onAssign}>שייך</ActionBtn>
                            </>
                        )}
                        {/* Verify form */}
                        <ActionBtn color="gray" onClick={onVerifyForm}>🔍</ActionBtn>
                        {/* History */}
                        <ActionBtn color="gray" onClick={onViewHistory}>📜</ActionBtn>
                    </div>
                </td>
                <td className="px-4 py-3 text-center">
                    <button
                        onClick={onToggleExpand}
                        className="p-1 rounded hover:bg-accent transition-colors text-muted-foreground"
                    >
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>
                </td>
            </tr>
            {/* Expanded Row: Inline History */}
            {isExpanded && (
                <tr>
                    <td colSpan={8} className="bg-accent/20 dark:bg-accent/10 px-6 py-4 border-b border-border/30">
                        <InlineHistory equipmentId={item.id} />
                    </td>
                </tr>
            )}
        </>
    );
}

// ============================================================
// Action Button Component
// ============================================================

function ActionBtn({ children, onClick, color }: { children: React.ReactNode; onClick: () => void; color: string }) {
    const colorMap: Record<string, string> = {
        indigo: 'text-indigo-700 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-500/10 border-indigo-200 dark:border-indigo-500/20 hover:bg-indigo-100 dark:hover:bg-indigo-500/20',
        emerald: 'text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 border-emerald-200 dark:border-emerald-500/20 hover:bg-emerald-100 dark:hover:bg-emerald-500/20',
        red: 'text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-500/10 border-red-200 dark:border-red-500/20 hover:bg-red-100 dark:hover:bg-red-500/20',
        blue: 'text-blue-700 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10 border-blue-200 dark:border-blue-500/20 hover:bg-blue-100 dark:hover:bg-blue-500/20',
        violet: 'text-violet-700 dark:text-violet-400 bg-violet-50 dark:bg-violet-500/10 border-violet-200 dark:border-violet-500/20 hover:bg-violet-100 dark:hover:bg-violet-500/20',
        gray: 'text-muted-foreground bg-accent/50 border-border/30 hover:bg-accent',
    };

    return (
        <button
            onClick={onClick}
            className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-bold border
                        transition-colors whitespace-nowrap ${colorMap[color] || colorMap.gray}`}
        >
            {children}
        </button>
    );
}

// ============================================================
// Inline History (lightweight version for expanded row)
// ============================================================

function InlineHistory({ equipmentId }: { equipmentId: number }) {
    const [history, setHistory] = useState<Array<{
        id: number; old_status: string; new_status: string;
        change_reason: string; created_date: string; user_name: string | null;
    }>>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        api.get(`/equipment/${equipmentId}/history`)
            .then(res => setHistory(res.data))
            .catch(err => console.error(err))
            .finally(() => setLoading(false));
    }, [equipmentId]);

    const getReasonLabel = (reason: string) => {
        switch (reason) {
            case 'verification': return '✅ אימות';
            case 'fault_report': return '⚠️ תקלה';
            case 'repair': return '🔧 תיקון';
            case 'transfer': return '🔄 העברה';
            default: return '📝 ' + reason;
        }
    };

    if (loading) return <div className="text-sm text-muted-foreground py-2">טוען היסטוריה...</div>;

    if (history.length === 0) return (
        <div className="text-sm text-muted-foreground py-2">אין רשומות היסטוריה.</div>
    );

    return (
        <div className="space-y-2">
            <div className="text-xs font-bold text-muted-foreground uppercase mb-2">היסטוריית שינויים</div>
            {history.slice(0, 5).map(h => (
                <div key={h.id} className="flex items-center gap-3 text-xs text-muted-foreground">
                    <span className="font-medium whitespace-nowrap">{getReasonLabel(h.change_reason)}</span>
                    <span className="text-emerald-600 dark:text-emerald-400">{h.old_status}</span>
                    <span>→</span>
                    <span className="text-primary">{h.new_status}</span>
                    <span className="mr-auto">
                        {h.created_date ? new Date(h.created_date).toLocaleString('he-IL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}
                    </span>
                    {h.user_name && <span className="text-foreground/60">{h.user_name}</span>}
                </div>
            ))}
            {history.length > 5 && (
                <div className="text-xs text-primary">עוד {history.length - 5} רשומות...</div>
            )}
        </div>
    );
}
