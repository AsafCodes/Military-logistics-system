import { useState, useEffect } from 'react';
import api from '@/api';

interface User {
    id: number;
    full_name: string;
    personal_number: string;
    role: string;
    profile?: {
        id: number;
        name: string;
    };
    unit_path?: string;
}

interface ProfileSummary {
    id: number;
    name: string;
}

interface AdminPanelProps {
    onClose: () => void;
}

export default function AdminPanel({ onClose: _onClose }: AdminPanelProps) {
    const [profiles, setProfiles] = useState<ProfileSummary[]>([]);

    // Search State
    const [searchTerm, setSearchTerm] = useState("");
    const [searchResults, setSearchResults] = useState<User[]>([]);
    const [isSearching, setIsSearching] = useState(false);

    // Selection & Edit State
    const [selectedUser, setSelectedUser] = useState<User | null>(null);
    const [selectedProfileId, setSelectedProfileId] = useState("");

    useEffect(() => {
        fetchProfiles();
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

    const fetchProfiles = async () => {
        try {
            const res = await api.get('/profiles');
            setProfiles(res.data);
        } catch (err) {
            console.error("Failed to fetch profiles", err);
        }
    };

    const handleUserSelect = (user: User) => {
        setSelectedUser(user);
        setSelectedProfileId(user.profile?.id.toString() || "");
        setSearchTerm("");
        setSearchResults([]);
    };

    const handleSaveProfile = async () => {
        if (!selectedUser || !selectedProfileId) return;
        try {
            await api.put(`/users/${selectedUser.id}/profile`, {
                profile_id: parseInt(selectedProfileId)
            });
            alert("פרופיל עודכן בהצלחה!");
            setSelectedUser(null);
        } catch (err) {
            alert("עדכון הפרופיל נכשל.");
        }
    };

    return (
        <div className="space-y-6 animate-fade-in" dir="rtl">
            {/* Header */}
            <div className="glass-card p-6">
                <h2 className="text-xl font-bold text-foreground mb-1">🛡️ ניהול מערכת</h2>
                <p className="text-sm text-muted-foreground">ניהול תפקידים ופרופילים</p>
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
                                            מ.א: {u.personal_number} • תפקיד: {u.role}
                                        </div>
                                        <div className="text-xs text-primary mt-1">
                                            פרופיל נוכחי: {u.profile?.name || "ללא"}
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
                        <h3 className="font-bold text-foreground">עריכת פרופיל</h3>
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
                                    פרופיל מטריצה
                                </label>
                                <p className="text-xs text-muted-foreground/70 mb-2">
                                    קובע הרשאות וגישה לנתוני היררכיה.
                                </p>
                                <select
                                    value={selectedProfileId}
                                    onChange={e => setSelectedProfileId(e.target.value)}
                                    className="w-full px-3 py-2 rounded-lg border border-border/50
                                               bg-background text-foreground
                                               focus:ring-2 focus:ring-primary/50 outline-none
                                               transition-colors"
                                    size={Math.min(profiles.length + 1, 8)}
                                >
                                    <option value="" disabled>-- בחר פרופיל --</option>
                                    {profiles.map(p => (
                                        <option key={p.id} value={p.id} className="py-1">
                                            {p.name}
                                        </option>
                                    ))}
                                </select>
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
                                    onClick={handleSaveProfile}
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
