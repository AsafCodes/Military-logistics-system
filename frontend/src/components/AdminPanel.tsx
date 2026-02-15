import { useState, useEffect } from 'react';
import api from '../api';

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

export default function AdminPanel({ onClose }: AdminPanelProps) {
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
        setSearchTerm(""); // Clear search to show "Editor Mode" clearly
        setSearchResults([]);
    };

    const handleSaveProfile = async () => {
        if (!selectedUser || !selectedProfileId) return;
        try {
            await api.put(`/users/${selectedUser.id}/profile`, {
                profile_id: parseInt(selectedProfileId)
            });
            alert("User profile updated successfully!");
            setSelectedUser(null); // Return to search
        } catch (err) {
            alert("Failed to update profile.");
        }
    };

    return (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl h-[85vh] flex flex-col overflow-hidden">
                <div className="bg-slate-900 text-white p-6 flex justify-between items-center">
                    <div>
                        <h2 className="text-2xl font-bold">🛡️ Master Admin Panel</h2>
                        <p className="text-slate-400 text-sm">User Role & Profile Management</p>
                    </div>
                    <button onClick={onClose} className="text-slate-400 hover:text-white">✕ Close</button>
                </div>

                <div className="p-8 overflow-auto flex-1 bg-gray-50 flex gap-8">

                    {/* Left Panel: Find User */}
                    <div className="w-1/2 bg-white p-6 rounded-xl shadow border border-gray-100 flex flex-col h-full">
                        <h3 className="text-lg font-bold text-gray-800 mb-4 border-b pb-2">Find User</h3>
                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Search by Name or ID</label>
                                <input
                                    type="text"
                                    value={searchTerm}
                                    onChange={e => setSearchTerm(e.target.value)}
                                    className="w-full border rounded p-2 focus:ring-2 focus:ring-blue-500 outline-none"
                                    placeholder="Start typing..."
                                />
                            </div>

                            <div className="flex-1 overflow-y-auto min-h-[200px] border rounded bg-gray-50 p-2 space-y-2">
                                {isSearching ? (
                                    <div className="text-center text-gray-400 p-4">Searching...</div>
                                ) : searchResults.length > 0 ? (
                                    searchResults.map(u => (
                                        <div
                                            key={u.id}
                                            onClick={() => handleUserSelect(u)}
                                            className="bg-white p-3 rounded border border-gray-100 cursor-pointer hover:border-blue-300 hover:shadow-sm transition-all group"
                                        >
                                            <div className="font-bold text-gray-800 group-hover:text-blue-700">{u.full_name}</div>
                                            <div className="text-xs text-gray-500">
                                                ID: {u.personal_number} • Role: {u.role}
                                            </div>
                                            <div className="text-xs text-indigo-600 mt-1">
                                                Current Profile: {u.profile?.name || "None"}
                                            </div>
                                        </div>
                                    ))
                                ) : (
                                    searchTerm.length > 2 && <div className="text-center text-gray-400 p-4">No users found.</div>
                                )}
                                {!searchTerm && !selectedUser && (
                                    <div className="text-center text-gray-400 p-4 text-sm mt-10">
                                        Use the search bar to find a user to edit.
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>

                    {/* Right Panel: Edit User */}
                    <div className="w-1/2 bg-white p-6 rounded-xl shadow border border-gray-100 flex flex-col h-full">
                        <h3 className="text-lg font-bold text-gray-800 mb-4 border-b pb-2">Edit User Profile</h3>

                        {selectedUser ? (
                            <div className="space-y-6 animate-in fade-in slide-in-from-right-4 duration-300">
                                <div className="bg-blue-50 p-4 rounded-lg border border-blue-100">
                                    <div className="text-sm text-blue-800 font-bold">Selected User</div>
                                    <div className="text-2xl font-bold text-blue-900">{selectedUser.full_name}</div>
                                    <div className="text-blue-700 font-mono text-sm">{selectedUser.personal_number}</div>
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Matrix Profile</label>
                                    <p className="text-xs text-gray-500 mb-2">Determines permissions and hierarchy access.</p>
                                    <select
                                        value={selectedProfileId}
                                        onChange={e => setSelectedProfileId(e.target.value)}
                                        className="w-full border rounded p-2 focus:ring-2 focus:ring-blue-500 outline-none bg-white font-medium"
                                        size={10} // Show list style
                                    >
                                        <option value="" disabled>-- Select a Profile --</option>
                                        {profiles.map(p => (
                                            <option key={p.id} value={p.id} className="py-1">
                                                {p.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                <div className="flex gap-3 mt-auto pt-6">
                                    <button
                                        onClick={() => setSelectedUser(null)}
                                        className="flex-1 px-4 py-2 border rounded hover:bg-gray-50"
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        onClick={handleSaveProfile}
                                        className="flex-1 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 font-bold shadow-md"
                                    >
                                        Save Changes
                                    </button>
                                </div>
                            </div>
                        ) : (
                            <div className="flex-1 flex flex-col items-center justify-center text-center p-8 opacity-50">
                                <div className="text-6xl mb-4">👤</div>
                                <h4 className="text-xl font-medium text-gray-400">No User Selected</h4>
                                <p className="text-sm text-gray-400 mt-2">Select a user from the left panel to edit their profile.</p>
                            </div>
                        )}
                    </div>

                </div>
            </div>
        </div>
    );
}
