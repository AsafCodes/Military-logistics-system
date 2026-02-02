import { useEffect, useState } from 'react';
import api from '../api';
// import ReportGenerator from './ReportGenerator'; // Replaced by GeneralReportPage
import AdminPanel from './AdminPanel';
import DailyActivityTable from './DailyActivityTable';
import GeneralReportPage from './GeneralReportPage';

// Interfaces
interface UnitReadiness {
    total_items: number;
    functional_items: number;
    readiness_score: number;
}

interface Equipment {
    id: number;
    type: string;
    status: string;
    smart_description: string;
    compliance_check: string;
    compliance_level?: string; // "GOOD" | "WARNING" | "SEVERE"
    serial_number?: string;
}

interface FaultType {
    id: number;
    name: string;
    is_pending: boolean;
}

interface Profile {
    name: string;
    can_view_company_realtime: boolean;
    can_change_maintenance_status: boolean;
    can_change_assignment_others: boolean;
}

interface User {
    id: number;
    personal_number: string;
    full_name: string;
    role: string;
    profile?: Profile;
    battalion?: string;
    company?: string;
}

interface DashboardProps {
    onLogout: () => void;
}

export default function Dashboard({ onLogout }: DashboardProps) {
    const [stats, setStats] = useState<UnitReadiness | null>(null);
    const [equipment, setEquipment] = useState<Equipment[]>([]);

    // NEW: Toggle for Daily Log
    const [showDailyLog, setShowDailyLog] = useState(false);

    // Fault Management State
    const [faultTypes, setFaultTypes] = useState<FaultType[]>([]);
    const [pendingFaults, setPendingFaults] = useState<FaultType[]>([]);
    const [isManager, setIsManager] = useState(false);

    // UI State
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Modal State
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [selectedItem, setSelectedItem] = useState<Equipment | null>(null);
    const [selectedFaultName, setSelectedFaultName] = useState("");
    const [description, setDescription] = useState("");

    // Transfer State
    const [isTransferModalOpen, setIsTransferModalOpen] = useState(false);
    const [transferTargetId, setTransferTargetId] = useState("");

    // Async Search State
    const [searchTerm, setSearchTerm] = useState("");
    const [searchResults, setSearchResults] = useState<User[]>([]);
    const [isSearching, setIsSearching] = useState(false);

    // Request New Fault State
    const [isRequestingNew, setIsRequestingNew] = useState(false);
    const [newFaultName, setNewFaultName] = useState("");

    // Report Generator State
    const [isReportOpen, setIsReportOpen] = useState(false);

    // Admin Panel State
    const [isAdminOpen, setIsAdminOpen] = useState(false);
    // Role state
    const [userRole, setUserRole] = useState("user");
    const [user, setUser] = useState<User | null>(null);

    useEffect(() => {
        initDashboard();
    }, []);

    const initDashboard = async () => {
        try {
            // 1. Fetch User Profile
            const userRes = await api.get('/users/me');
            const currentUser = userRes.data;
            setUser(currentUser);
            setUserRole(currentUser.role);

            // Set Manager flag
            if (['manager', 'technician_manager', 'master'].includes(currentUser.role)) {
                setIsManager(true);
            }

            // 2. Fetch Data
            await fetchDashboardData(currentUser);
            fetchFaultTypes();

        } catch (err) {
            console.error("Failed to init", err);
            setError("Failed to load user profile.");
            setLoading(false);
        }
    };

    const fetchDashboardData = async (currentUser?: User) => {
        const activeUser = currentUser || user;
        if (!activeUser) return;

        try {
            let equipmentEndpoint = '/users/me/equipment';
            if (activeUser.profile?.can_view_company_realtime) {
                equipmentEndpoint = '/equipment/accessible';
            }

            const [readinessRes, equipmentRes] = await Promise.all([
                api.get('/analytics/unit_readiness'),
                api.get(equipmentEndpoint)
            ]);

            setStats(readinessRes.data);
            setEquipment(equipmentRes.data);
        } catch (err: any) {
            console.error('Failed to fetch dashboard data:', err);
            setError('Failed to load system data.');
        } finally {
            setLoading(false);
        }
    };

    const fetchFaultTypes = async () => {
        try {
            const res = await api.get('/setup/fault_types');
            setFaultTypes(res.data);
            if (res.data.length > 0) setSelectedFaultName(res.data[0].name);

            // Try fetching pending (Manager check)
            try {
                const pendingRes = await api.get('/setup/fault_types/pending');
                setPendingFaults(pendingRes.data);
                setIsManager(true);
            } catch (e) {
                // Not authorized to fetch pending
                setIsManager(false);
            }

        } catch (err) {
            console.error("Failed to fetch fault types", err);
        }
    };

    const openReportModal = (item: Equipment) => {
        setSelectedItem(item);
        if (faultTypes.length > 0) setSelectedFaultName(faultTypes[0].name);
        setDescription("");
        setIsRequestingNew(false);
        setNewFaultName("");
        setIsModalOpen(true);
    };

    const closeReportModal = () => {
        setIsModalOpen(false);
        setSelectedItem(null);
    };

    const handleRequestNewFault = async () => {
        if (!newFaultName.trim()) return;
        try {
            await api.post('/setup/fault_types', { name: newFaultName });
            alert("Request sent successfully!");
            setIsRequestingNew(false);
            // Refresh lists
            fetchFaultTypes();
            // Auto select the new one if approved immediately (Master)
            setSelectedFaultName(newFaultName);
        } catch (err) {
            alert("Failed to request new fault type.");
        }
    };

    const submitFaultReport = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!selectedItem) return;

        const finalDescription = `${selectedFaultName} - ${description}`;

        try {
            await api.post('/maintenance/report', {
                equipment_id: selectedItem.id,
                fault_name: selectedFaultName,
                description: finalDescription
            });

            await fetchDashboardData();
            alert("Fault reported successfully.");

            // Clean up and close
            closeReportModal();
            setIsRequestingNew(false);
            setNewFaultName("");
            setDescription("");

        } catch (err: any) {
            console.error("Failed to report fault:", err);
            alert("Failed to report fault. Please try again.");
        }
    };

    // Manager Actions
    const approveFault = async (id: number) => {
        try {
            await api.put(`/setup/fault_types/${id}/approve`);
            fetchFaultTypes(); // Refresh both lists
        } catch (err) {
            alert("Failed to approve.");
        }
    };

    const rejectFault = async (id: number) => {
        try {
            await api.delete(`/setup/fault_types/${id}`);
            fetchFaultTypes();
        } catch (err) {
            alert("Failed to reject.");
        }
    };

    const openTransferModal = (item: Equipment) => {
        setSelectedItem(item);
        setTransferTargetId("");
        setIsTransferModalOpen(true);
    };

    const handleTransfer = async () => {
        if (!selectedItem || !transferTargetId) return;
        try {
            await api.post('/equipment/transfer', {
                equipment_id: selectedItem.id,
                to_holder_id: parseInt(transferTargetId)
            });
            alert("Equipment transferred successfully.");
            setIsTransferModalOpen(false);
            setSelectedItem(null);
            setSearchTerm(""); // Reset search
            setSearchResults([]);
            fetchDashboardData();
        } catch (err) {
            alert("Failed to transfer equipment. Ensure item is Functional and within hierarchy.");
        }
    };

    // Smart Search Effect
    useEffect(() => {
        const delayDebounceFn = setTimeout(async () => {
            if (searchTerm.length > 1) { // Start searching after 2 chars
                setIsSearching(true);
                try {
                    const res = await api.get(`/users?q=${searchTerm}`);
                    setSearchResults(res.data);
                } catch (e) { console.error(e); }
                setIsSearching(false);
            } else {
                setSearchResults([]);
            }
        }, 400); // 400ms debounce

        return () => clearTimeout(delayDebounceFn);
    }, [searchTerm]);

    const selectUserForTransfer = (user: User) => {
        setTransferTargetId(user.id.toString());
    };


    if (loading) return (
        <div className="flex h-screen items-center justify-center bg-gray-50">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
        </div>
    );

    return (
        <div className="min-h-screen bg-gray-100 p-8 relative">
            <header className="flex justify-between items-center mb-8 bg-white p-6 rounded-xl shadow-sm">
                <div>
                    <h1 className="text-2xl font-bold text-gray-800">Operational Dashboard</h1>
                    <p className="text-gray-500 text-sm">Real-time Logistics Status</p>
                </div>
                <button
                    onClick={onLogout}
                    className="bg-red-50 text-red-600 px-4 py-2 rounded-lg font-medium hover:bg-red-100 transition-colors"
                >
                    Sign Out
                </button>
            </header>

            <div className="flex justify-end gap-2 mb-4 px-1">
                {/* Admin Panel Button - Only for Master */}
                {userRole === 'master' && (
                    <button
                        onClick={() => setIsAdminOpen(true)}
                        className="bg-slate-800 text-white px-5 py-2 rounded-lg shadow-sm hover:bg-slate-900 transition-all font-medium flex items-center gap-2"
                    >
                        🛡️ Admin Panel
                    </button>
                )}

                {/* Daily Log Toggle */}
                <button
                    onClick={() => setShowDailyLog(!showDailyLog)}
                    className={`px-5 py-2 rounded-lg shadow-sm transition-all font-medium flex items-center gap-2
                    ${showDailyLog ? 'bg-indigo-100 text-indigo-700' : 'bg-white text-gray-700 border border-gray-200'}`}
                >
                    📋 {showDailyLog ? 'Hide Log' : 'Show Daily Log'}
                </button>

                {/* Reports Button - Hidden for 'user' (Soldier) role */}
                {userRole !== 'user' && (
                    <button
                        onClick={() => setIsReportOpen(true)}
                        className="bg-indigo-600 text-white px-5 py-2 rounded-lg shadow-sm hover:bg-indigo-700 transition-all font-medium flex items-center gap-2"
                    >
                        📊 Advanced Reports
                    </button>
                )}
            </div>

            {/* Manager Panel */}
            {isManager && pendingFaults.length > 0 && (
                <div className="bg-yellow-50 border-l-4 border-yellow-400 p-4 mb-8 rounded shadow-sm">
                    <h3 className="text-yellow-800 font-bold mb-2">⚠ Pending Fault Type Requests</h3>
                    <div className="space-y-2">
                        {pendingFaults.map(ft => (
                            <div key={ft.id} className="flex items-center justify-between bg-white p-2 rounded border border-yellow-100">
                                <span className="text-gray-700 font-medium">{ft.name}</span>
                                <div className="flex gap-2">
                                    <button onClick={() => approveFault(ft.id)} className="text-green-600 hover:bg-green-50 p-1 rounded">✔ Approve</button>
                                    <button onClick={() => rejectFault(ft.id)} className="text-red-600 hover:bg-red-50 p-1 rounded">✖ Reject</button>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {error ? (
                <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
                    {error}
                </div>
            ) : (
                <div className="space-y-8">
                    {/* NEW: Operational Log Section (Collapsible) */}
                    {showDailyLog && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 animate-fade-in-down">
                            <h2 className="text-xl font-bold text-gray-800 mb-4">Operational Log (24h)</h2>
                            <DailyActivityTable />
                        </div>
                    )}

                    {/* Stats Grid */}
                    {stats && (
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex flex-col items-center justify-center py-10">
                                <div className="text-gray-500 font-medium mb-2">Unit Readiness</div>
                                <div className={`text-5xl font-bold ${stats.readiness_score >= 80 ? 'text-green-600' : 'text-red-500'}`}>
                                    {stats.readiness_score}%
                                </div>
                                <div className="mt-2 text-xs text-gray-400">Target: 80%</div>
                            </div>

                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                                <div className="flex items-center justify-between mb-4">
                                    <h3 className="text-gray-500 font-medium">Total Inventory</h3>
                                    <span className="p-2 bg-blue-50 text-blue-600 rounded-lg">📦</span>
                                </div>
                                <div className="text-3xl font-bold text-gray-800">{stats.total_items}</div>
                                <div className="mt-2 text-sm text-gray-400">Registered items</div>
                            </div>

                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                                <div className="flex items-center justify-between mb-4">
                                    <h3 className="text-gray-500 font-medium">Operational</h3>
                                    <span className="p-2 bg-green-50 text-green-600 rounded-lg">✅</span>
                                </div>
                                <div className="text-3xl font-bold text-gray-800">{stats.functional_items}</div>
                                <div className="mt-2 text-sm text-gray-400">Ready for deployment</div>
                            </div>
                        </div>
                    )}



                    {/* Equipment Table Helper */}
                    {(() => {
                        const renderActions = (item: Equipment) => {
                            if (item.status === 'Malfunctioning' && user?.profile?.can_change_maintenance_status) {
                                return (
                                    <button
                                        onClick={async () => {
                                            if (!confirm("Mark as Fixed/Repaired?")) return;
                                            try {
                                                await api.post(`/maintenance/fix/${item.id}`);
                                                fetchDashboardData();
                                            } catch (e) { alert("Failed to fix item"); }
                                        }}
                                        className="bg-green-50 text-green-700 hover:bg-green-100 px-3 py-1 rounded border border-green-200 text-xs font-bold"
                                    >
                                        Repair
                                    </button>
                                );
                            }

                            // Transfer Logic
                            if (user?.profile?.can_change_assignment_others) {
                                return (
                                    <button
                                        onClick={() => openTransferModal(item)}
                                        className="bg-blue-50 text-blue-700 hover:bg-blue-100 px-3 py-1 rounded border border-blue-200 text-xs font-bold ml-2"
                                    >
                                        Transfer
                                    </button>
                                );
                            }
                            return null;
                        };

                        return (
                            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
                                <div className="p-6 border-b border-gray-100">
                                    <h2 className="text-lg font-bold text-gray-800">
                                        {user?.profile?.can_view_company_realtime ? "Unit Equipment (Accessible)" : "My Equipment"}
                                    </h2>
                                </div>
                                <div className="overflow-x-auto">
                                    <table className="w-full text-left">
                                        <thead className="bg-gray-50 text-gray-500 text-sm uppercase font-semibold">
                                            <tr>
                                                <th className="px-6 py-4">ID</th>
                                                <th className="px-6 py-4">Type</th>
                                                <th className="px-6 py-4">צ'</th>
                                                <th className="px-6 py-4">Status</th>
                                                <th className="px-6 py-4">Description</th>
                                                <th className="px-6 py-4">Compliance</th>
                                                <th className="px-6 py-4">Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-gray-100 text-sm">
                                            {equipment.length > 0 ? (
                                                equipment.map((item) => (
                                                    <tr key={item.id} className="hover:bg-gray-50 transition-colors">
                                                        <td className="px-6 py-4 font-mono text-gray-600">#{item.id}</td>
                                                        <td className="px-6 py-4 font-medium text-gray-800">{item.type}</td>
                                                        <td className="px-6 py-4 font-mono font-bold text-indigo-600 whitespace-nowrap">
                                                            {item.serial_number || '-'}
                                                        </td>
                                                        <td className="px-6 py-4">
                                                            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium
                                                            ${item.status === 'Functional' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
                                                                {item.status}
                                                            </span>
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-600">{item.smart_description}</td>
                                                        <td className="px-6 py-4">
                                                            {(() => {
                                                                const level = item.compliance_level || "SEVERE"; // Fallback
                                                                let badgeClass = "bg-gray-100 text-gray-800";
                                                                let icon = "❓";

                                                                if (level === 'GOOD') {
                                                                    badgeClass = "bg-green-100 text-green-800";
                                                                    icon = "✓";
                                                                } else if (level === 'WARNING') {
                                                                    badgeClass = "bg-yellow-100 text-yellow-800";
                                                                    icon = "⚠";
                                                                } else if (level === 'SEVERE') {
                                                                    badgeClass = "bg-red-100 text-red-800";
                                                                    icon = "✖";
                                                                }

                                                                return (
                                                                    <div className="flex flex-col">
                                                                        <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold w-fit ${badgeClass}`}>
                                                                            {icon} {level}
                                                                        </span>
                                                                        <span className="text-xs text-gray-400 mt-1">{item.compliance_check}</span>
                                                                    </div>
                                                                );
                                                            })()}
                                                        </td>
                                                        <td className="px-6 py-4 flex gap-2">
                                                            {item.status === 'Functional' && (
                                                                <button
                                                                    onClick={() => openReportModal(item)}
                                                                    className="text-red-600 hover:text-red-800 font-medium text-xs border border-red-200 hover:border-red-400 bg-red-50 hover:bg-red-100 px-3 py-1 rounded transition-colors"
                                                                >
                                                                    Report Fault
                                                                </button>
                                                            )}
                                                            {renderActions(item)}
                                                        </td>
                                                    </tr>
                                                ))
                                            ) : (
                                                <tr>
                                                    <td colSpan={6} className="px-6 py-8 text-center text-gray-400">
                                                        No equipment found.
                                                    </td>
                                                </tr>
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        );
                    })()}
                </div>
            )}

            <div className="mt-8 text-center text-xs text-gray-400">
                Military Logistics System v4.0 • Secured Environment
            </div>

            {/* Report Fault Modal */}
            {isModalOpen && selectedItem && (
                <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
                    <div className="bg-white rounded-xl shadow-2xl w-full max-w-md overflow-hidden transform transition-all">
                        <div className="bg-gray-50 px-6 py-4 border-b border-gray-100 flex justify-between items-center">
                            <h3 className="text-lg font-bold text-gray-800">
                                Report Fault
                            </h3>
                            <button onClick={closeReportModal} className="text-gray-400 hover:text-gray-600">
                                ✕
                            </button>
                        </div>

                        <form onSubmit={submitFaultReport} className="p-6 space-y-4">
                            <div className="text-sm text-gray-600 mb-2">
                                Reporting fault for: <span className="font-semibold">{selectedItem.type} (#{selectedItem.id})</span>
                            </div>

                            {!isRequestingNew ? (
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">
                                        Fault Type
                                    </label>
                                    <select
                                        value={selectedFaultName}
                                        onChange={(e) => setSelectedFaultName(e.target.value)}
                                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none"
                                    >
                                        {faultTypes.map(opt => (
                                            <option key={opt.id} value={opt.name}>{opt.name}</option>
                                        ))}
                                    </select>
                                    <button
                                        type="button"
                                        onClick={() => setIsRequestingNew(true)}
                                        className="text-xs text-indigo-600 hover:text-indigo-800 mt-1 font-medium"
                                    >
                                        Type not listed? Request new.
                                    </button>
                                </div>
                            ) : (
                                <div className="bg-indigo-50 p-3 rounded-lg border border-indigo-100">
                                    <label className="block text-sm font-medium text-indigo-900 mb-1">
                                        Request New Fault Type
                                    </label>
                                    <div className="flex gap-2">
                                        <input
                                            type="text"
                                            value={newFaultName}
                                            onChange={(e) => setNewFaultName(e.target.value)}
                                            className="flex-1 px-3 py-1 border border-indigo-200 rounded text-sm focus:outline-none focus:border-indigo-500"
                                            placeholder="e.g. Battery Leak"
                                        />
                                        <button
                                            type="button"
                                            onClick={handleRequestNewFault}
                                            className="bg-indigo-600 text-white px-3 py-1 rounded text-sm hover:bg-indigo-700"
                                        >
                                            Request
                                        </button>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => setIsRequestingNew(false)}
                                        className="text-xs text-gray-500 mt-2 hover:text-gray-700"
                                    >
                                        Cancel request
                                    </button>
                                </div>
                            )}

                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">
                                    Additional Details
                                </label>
                                <textarea
                                    value={description}
                                    onChange={(e) => setDescription(e.target.value)}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none h-24 resize-none"
                                    placeholder="Describe the issue..."
                                    required
                                />
                            </div>

                            <div className="flex gap-3 mt-6">
                                <button
                                    type="button"
                                    onClick={closeReportModal}
                                    className="flex-1 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 font-medium"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    className="flex-1 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium shadow-sm"
                                >
                                    Submit Report
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* General Report Page Modal (Replaces ReportGenerator) */}
            {isReportOpen && (
                <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
                    <div className="bg-white rounded-xl shadow-2xl w-full max-w-6xl h-[90vh] overflow-hidden flex flex-col">
                        <div className="p-4 border-b flex justify-between items-center bg-gray-50">
                            <h2 className="text-xl font-bold text-gray-800">Advanced Inventory Reports</h2>
                            <button onClick={() => setIsReportOpen(false)} className="text-gray-500 hover:text-gray-800 text-xl font-bold px-2">✕</button>
                        </div>
                        <div className="flex-1 overflow-auto p-4 bg-gray-100">
                            <GeneralReportPage />
                        </div>
                    </div>
                </div>
            )}

            {isAdminOpen && <AdminPanel onClose={() => setIsAdminOpen(false)} />}

            {/* Smart Transfer Modal */}
            {isTransferModalOpen && selectedItem && (
                <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
                    <div className="bg-white rounded-xl shadow-2xl w-full max-w-md overflow-hidden p-6 space-y-4">
                        <h3 className="text-lg font-bold text-gray-800">Transfer Ownership</h3>
                        <p className="text-sm text-gray-600">
                            Transferring <b>{selectedItem.type} (#{selectedItem.id})</b>.
                        </p>

                        <div className="space-y-2">
                            <label className="block text-sm font-medium text-gray-700">Find Recipient</label>
                            <input
                                type="text"
                                placeholder="Type Name or Personal ID..."
                                className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                            />

                            {/* Search Results */}
                            <div className="max-h-48 overflow-y-auto border rounded-lg bg-gray-50 divide-y divide-gray-200">
                                {isSearching ? (
                                    <div className="p-3 text-center text-gray-400 text-xs">Searching...</div>
                                ) : searchResults.length > 0 ? (
                                    searchResults.map(u => (
                                        <button
                                            key={u.id}
                                            onClick={() => selectUserForTransfer(u)}
                                            className={`w-full text-left px-3 py-2 text-sm hover:bg-blue-50 transition-colors flex justify-between items-center
                                                ${transferTargetId === u.id.toString() ? 'bg-blue-100 border-l-4 border-blue-500' : ''}`}
                                        >
                                            <div>
                                                <div className="font-bold text-gray-800">{u.full_name}</div>
                                                <div className="text-xs text-gray-500">{u.personal_number} • {u.company || "N/A"}</div>
                                            </div>
                                            {transferTargetId === u.id.toString() && <span className="text-blue-600 font-bold">Selected</span>}
                                        </button>
                                    ))
                                ) : (
                                    searchTerm.length > 1 && <div className="p-3 text-center text-gray-400 text-xs">No users found</div>
                                )}
                            </div>
                        </div>

                        <div className="flex gap-3 mt-4 pt-2 border-t border-gray-100">
                            <button onClick={() => setIsTransferModalOpen(false)} className="flex-1 px-4 py-2 border rounded text-gray-600 hover:bg-gray-50">Cancel</button>
                            <button
                                onClick={handleTransfer}
                                disabled={!transferTargetId}
                                className={`flex-1 px-4 py-2 rounded text-white font-medium
                                    ${transferTargetId ? 'bg-blue-600 hover:bg-blue-700' : 'bg-gray-300 cursor-not-allowed'}`}
                            >
                                Confirm Transfer
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
