import { useState, useEffect } from 'react';
import api from '@/api';

interface User {
    id: number;
    full_name: string;
    personal_number: string;
    group?: {
        id: number;
        name: string;
    };
}

interface GroupSummary {
    id: number;
    name: string;
}

interface AdminPanelProps {
    onClose: () => void;
}

export default function AdminPanel({ onClose: _onClose }: AdminPanelProps) {
    const [groups, setGroups] = useState<GroupSummary[]>([]);
    // SEC-H10. The route guard in App.tsx is a client-side convenience, not
    // the real gate -- MANAGE_PERSONNEL is (list_groups gates on it via
    // authz.require_global, backend/routers/setup.py). Someone reaching this
    // panel without it (a stale capabilities snapshot, a demotion mid-session)
    // must see a refusal on the group dropdown, not a silent empty one.
    //
    // Scoped to the ONE thing that actually failed, not the whole panel: user
    // search (below) fetches independently and does not depend on /groups, so
    // a /groups-only failure must not take it down too. And 'forbidden' is
    // asserted only on a real 403 -- a transient network blip is a DIFFERENT
    // fact from "you may not belong here" and must not be reported as one.
    const [groupsError, setGroupsError] = useState<'forbidden' | 'network' | null>(null);
    // Starts true, not false: without it, the render before fetchGroups()'s
    // promise settles shows the group selector as though it were simply
    // empty rather than not-yet-known -- the same brief, misleading window
    // this whole refusal state exists to close, just moved one render
    // earlier. Enforcement stays server-side either way (PUT .../group is
    // separately gated on MANAGE_PERSONNEL); this is about what the UI
    // implies during that window, not what it can get away with.
    const [groupsLoading, setGroupsLoading] = useState(true);

    // Search State
    const [searchTerm, setSearchTerm] = useState("");
    const [searchResults, setSearchResults] = useState<User[]>([]);
    const [isSearching, setIsSearching] = useState(false);

    // Selection & Edit State
    const [selectedUser, setSelectedUser] = useState<User | null>(null);
    const [selectedGroupId, setSelectedGroupId] = useState("");

    useEffect(() => {
        fetchGroups();
    }, []);

    // Search Users Effect
    useEffect(() => {
        const delayDebounceFn = setTimeout(async () => {
            if (searchTerm.length > 1) {
                setIsSearching(true);
                try {
                    const res = await api.get(`/users?q=${searchTerm}`);
                    setSearchResults(res.data);
                } catch (e) { console.error(e); }
                setIsSearching(false);
            } else {
                setSearchResults([]);
            }
        }, 300);

        return () => clearTimeout(delayDebounceFn);
    }, [searchTerm]);

    const fetchGroups = async () => {
        setGroupsLoading(true);
        try {
            const res = await api.get('/groups');
            setGroups(res.data);
            setGroupsError(null);
        } catch (err) {
            console.error("Failed to fetch groups", err);
            const status = (err as { response?: { status?: number } })?.response?.status;
            setGroupsError(status === 403 ? 'forbidden' : 'network');
        } finally {
            setGroupsLoading(false);
        }
    };

    const handleUserSelect = (user: User) => {
        setSelectedUser(user);
        setSelectedGroupId(user.group?.id?.toString() || "");
        setSearchTerm("");
        setSearchResults([]);
    };

    const handleSaveGroup = async () => {
        if (!selectedUser || !selectedGroupId) return;
        try {
            await api.put(`/users/${selectedUser.id}/group`, {
                group_id: parseInt(selectedGroupId)
            });
            alert("קבוצה עודכנה בהצלחה!");
            setSelectedUser(null);
        } catch (err) {
            alert("עדכון הקבוצה נכשל.");
        }
    };

    return (
        <div className="space-y-6 animate-fade-in" dir="rtl">
            {/* Header */}
            <div className="glass-card p-6">
                <h2 className="text-xl font-bold text-foreground mb-1">🛡️ ניהול מערכת</h2>
                <p className="text-sm text-muted-foreground">שיוך משתמשים לקבוצות</p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Left Panel: Find User */}
                <div className="glass-card overflow-hidden">
                    <div className="px-6 py-4 border-b border-border/30">
                        <h3 className="font-bold text-foreground">חיפוש משתמש</h3>
                    </div>
                    <div className="p-6 space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-muted-foreground mb-1">
                                חיפוש לפי שם או מספר אישי
                            </label>
                            <input
                                type="text"
                                value={searchTerm}
                                onChange={e => setSearchTerm(e.target.value)}
                                className="w-full px-3 py-2 rounded-lg border border-border/50
                                           bg-background text-foreground
                                           focus:ring-2 focus:ring-primary/50 outline-none
                                           placeholder:text-muted-foreground/50
                                           transition-colors"
                                placeholder="התחל להקליד..."
                            />
                        </div>

                        <div className="min-h-[200px] border border-border/30 rounded-lg bg-background/50 p-2 space-y-2 overflow-y-auto max-h-[300px]">
                            {isSearching ? (
                                <div className="text-center text-muted-foreground p-4">מחפש...</div>
                            ) : searchResults.length > 0 ? (
                                searchResults.map(u => (
                                    <div
                                        key={u.id}
                                        onClick={() => handleUserSelect(u)}
                                        className="p-3 rounded-lg border border-border/30 cursor-pointer
                                                   hover:border-primary/50 hover:bg-accent/40
                                                   transition-all group"
                                    >
                                        <div className="font-bold text-foreground group-hover:text-primary">
                                            {u.full_name}
                                        </div>
                                        <div className="text-xs text-muted-foreground">
                                            מ.א: {u.personal_number}
                                        </div>
                                        <div className="text-xs text-primary mt-1">
                                            קבוצה נוכחית: {u.group?.name || "ללא"}
                                        </div>
                                    </div>
                                ))
                            ) : (
                                searchTerm.length > 2 && (
                                    <div className="text-center text-muted-foreground p-4">לא נמצאו משתמשים.</div>
                                )
                            )}
                            {!searchTerm && !selectedUser && (
                                <div className="text-center text-muted-foreground p-4 text-sm mt-10">
                                    השתמש בחיפוש למעלה למציאת משתמש לעריכה.
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                {/* Right Panel: Edit User */}
                <div className="glass-card overflow-hidden">
                    <div className="px-6 py-4 border-b border-border/30">
                        <h3 className="font-bold text-foreground">שיוך לקבוצה</h3>
                    </div>

                    {selectedUser ? (
                        <div className="p-6 space-y-6 animate-fade-in">
                            <div className="p-4 rounded-lg bg-primary/5 dark:bg-primary/10 border border-primary/20">
                                <div className="text-sm text-primary font-bold">משתמש נבחר</div>
                                <div className="text-2xl font-bold text-foreground">{selectedUser.full_name}</div>
                                <div className="text-sm font-mono text-muted-foreground">{selectedUser.personal_number}</div>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-muted-foreground mb-1">
                                    קבוצה
                                </label>
                                <p className="text-xs text-muted-foreground/70 mb-2">
                                    קובעת היכן המשתמש נמצא בעץ הארגוני, ומה נראה לו כתוצאה מכך.
                                </p>
                                {groupsLoading ? (
                                    <div className="p-3 text-sm text-muted-foreground">טוען קבוצות...</div>
                                ) : groupsError ? (
                                    <div className="p-3 rounded-lg border border-destructive/30 bg-destructive/5 space-y-2">
                                        <p className="text-sm text-destructive">
                                            {groupsError === 'forbidden'
                                                ? 'אין לך הרשאה לצפות ברשימת הקבוצות.'
                                                : 'טעינת רשימת הקבוצות נכשלה.'}
                                        </p>
                                        {groupsError === 'network' && (
                                            <button
                                                onClick={fetchGroups}
                                                className="text-sm text-primary hover:underline"
                                            >
                                                נסה שוב
                                            </button>
                                        )}
                                    </div>
                                ) : (
                                    <select
                                        value={selectedGroupId}
                                        onChange={e => setSelectedGroupId(e.target.value)}
                                        className="w-full px-3 py-2 rounded-lg border border-border/50
                                               bg-background text-foreground
                                               focus:ring-2 focus:ring-primary/50 outline-none
                                               transition-colors"
                                        size={Math.min(groups.length + 1, 8)}
                                    >
                                        <option value="" disabled>-- בחר קבוצה --</option>
                                        {groups.map(g => (
                                            <option key={g.id} value={g.id} className="py-1">
                                                {g.name}
                                            </option>
                                        ))}
                                    </select>
                                )}
                            </div>

                            <div className="flex gap-3 pt-4">
                                <button
                                    onClick={() => setSelectedUser(null)}
                                    className="flex-1 px-4 py-2 rounded-lg border border-border/50
                                               text-foreground hover:bg-accent transition-colors"
                                >
                                    ביטול
                                </button>
                                <button
                                    onClick={handleSaveGroup}
                                    className="flex-1 px-4 py-2 rounded-lg
                                               bg-primary text-primary-foreground
                                               hover:bg-primary/90 font-bold shadow-md
                                               transition-colors"
                                >
                                    שמור שינויים
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div className="flex-1 flex flex-col items-center justify-center text-center p-8 opacity-50">
                            <div className="text-6xl mb-4">👤</div>
                            <h4 className="text-xl font-medium text-muted-foreground">לא נבחר משתמש</h4>
                            <p className="text-sm text-muted-foreground mt-2">
                                בחר משתמש מהפאנל השמאלי לעריכת הפרופיל שלו.
                            </p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
