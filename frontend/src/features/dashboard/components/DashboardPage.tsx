import { useEffect, useState } from 'react';
import api from '@/api';

// Components
import AdminPanel from './AdminPanel';
import DailyActivityTable from './DailyActivityTable';
import GeneralReportPage from '../../reports/components/GeneralReportPage';
import ExportControls from './ExportControls';
import EquipmentTable from './EquipmentTable';
import StatsGrid from './StatsGrid';

// Hooks
import { useDashboardData } from '../hooks/useDashboardData';

// Types
import type { User, Equipment, FaultType as SharedFaultType } from '@/types';

interface DashboardProps {
    onLogout: () => void;
}

export default function Dashboard({ onLogout }: DashboardProps) {
    // -------------------------------------------------------------------------
    // 1. STATE DEFINITIONS
    // -------------------------------------------------------------------------

    // User & Auth State
    const [user, setUser] = useState<User | null>(null);
    const [userRole, setUserRole] = useState("user");
    const [isManager, setIsManager] = useState(false);

    // Data State (via Hook)
    const { stats, equipment, loading: _dataLoading, error: dataError, fetchData, refreshData } = useDashboardData();
    const [initLoading, setInitLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Feature Toggles (UI)
    const [showDailyLog, setShowDailyLog] = useState(false);

    // Fault Management State
    const [faultTypes, setFaultTypes] = useState<SharedFaultType[]>([]);
    const [pendingFaults, setPendingFaults] = useState<SharedFaultType[]>([]);

    // Modals & Forms State
    // Report Fault
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [selectedItem, setSelectedItem] = useState<Equipment | null>(null);
    const [selectedFaultName, setSelectedFaultName] = useState("");
    const [description, setDescription] = useState("");
    const [isRequestingNew, setIsRequestingNew] = useState(false);
    const [newFaultName, setNewFaultName] = useState("");

    // Transfer
    const [isTransferModalOpen, setIsTransferModalOpen] = useState(false);
    const [transferTargetId, setTransferTargetId] = useState("");
    const [transferMode, setTransferMode] = useState<'person' | 'location'>('person');
    const [transferLocation, setTransferLocation] = useState("");

    // Assign Owner
    const [isAssignOwnerModalOpen, setIsAssignOwnerModalOpen] = useState(false);
    const [assignOwnerTargetId, setAssignOwnerTargetId] = useState("");

    // User Search (Shared)
    const [searchTerm, setSearchTerm] = useState("");
    const [searchResults, setSearchResults] = useState<User[]>([]);
    const [isSearching, setIsSearching] = useState(false);

    // Full Screen Reports
    const [isReportOpen, setIsReportOpen] = useState(false);
    const [isAdminOpen, setIsAdminOpen] = useState(false);

    // -------------------------------------------------------------------------
    // 2. CALCULATED VARIABLES
    // -------------------------------------------------------------------------

    // Permission Helper
    const canManageAssets = user?.profile?.can_change_assignment_others ||
        ['Company Tech Soldier', 'Battalion Tech Commander', 'Brigade Tech Commander'].includes(user?.profile?.name || '');

    // -------------------------------------------------------------------------
    // 3. EFFECTS
    // -------------------------------------------------------------------------

    useEffect(() => {
        initDashboard();
    }, []);

    // Smart Search Debounce
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

    // -------------------------------------------------------------------------
    // 4. HANDLERS
    // -------------------------------------------------------------------------

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
            await fetchData(currentUser);
            fetchFaultTypes(currentUser);

        } catch (err) {
            console.error("Failed to init", err);
            setError("Failed to load user profile. Please try refreshing.");
        } finally {
            setInitLoading(false);
        }
    };

    const fetchFaultTypes = async (currentUser?: User) => {
        try {
            const res = await api.get('/setup/fault_types');
            setFaultTypes(res.data);
            if (res.data.length > 0) setSelectedFaultName(res.data[0].name);

            // Optimization: Only fetch pending if allowed
            const activeUser = currentUser || user;
            if (activeUser?.profile?.can_add_category) {
                try {
                    const pendingRes = await api.get('/setup/fault_types/pending');
                    setPendingFaults(pendingRes.data);
                    setIsManager(true);
                } catch (e) {
                    console.warn("Failed to fetch pending faults despite permission");
                    setIsManager(false);
                }
            } else {
                setIsManager(false);
            }

        } catch (err) {
            console.error("Failed to fetch fault types", err);
        }
    };

    // --- Actions Handlers ---

    const handleVerify = async (id: number) => {
        try {
            await api.post(`/equipment/${id}/verify`);
            refreshData(user);
        } catch (err) {
            alert("Verification failed. Ensure you are the holder.");
        }
    };

    const handleRepair = async (item: Equipment) => {
        if (!confirm("Mark as Fixed/Repaired?")) return;
        try {
            await api.post(`/maintenance/fix/${item.id}`);
            refreshData(user);
        } catch (e) { alert("Failed to fix item"); }
    };

    const openReportModal = (item: Equipment) => {
        setSelectedItem(item);
        if (faultTypes.length > 0) setSelectedFaultName(faultTypes[0].name);
        setDescription("");
        setIsRequestingNew(false);
        setNewFaultName("");
        setIsModalOpen(true);
    };

    const openTransferModal = (item: Equipment) => {
        setSelectedItem(item);
        setIsTransferModalOpen(true);
        setTransferMode('person');
        setTransferLocation("");
        setSearchTerm("");
    };

    const openAssignOwnerModal = (item: Equipment) => {
        setSelectedItem(item);
        setIsAssignOwnerModalOpen(true);
        setSearchTerm("");
    };

    // --- Modal Logic ---

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
            fetchFaultTypes();
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

            await refreshData(user);
            alert("Fault reported successfully.");
            closeReportModal();
            setIsRequestingNew(false);
            setNewFaultName("");
            setDescription("");

        } catch (err: any) {
            console.error("Failed to report fault:", err);
            alert("Failed to report fault. Please try again.");
        }
    };

    // --- Transfer Logic ---
    const selectUserForTransfer = (user: User) => {
        setTransferTargetId(user.id.toString());
    };

    const handleTransfer = async () => {
        if (!selectedItem) return;

        try {
            const payload: any = { equipment_id: selectedItem.id };

            if (transferMode === 'person') {
                if (!transferTargetId) return;
                payload.to_holder_id = parseInt(transferTargetId);
            } else {
                if (!transferLocation.trim()) return;
                payload.to_location = transferLocation;
            }

            await api.post('/equipment/transfer', payload);

            alert("Transfer Successful!");
            setIsTransferModalOpen(false);
            setSelectedItem(null);
            setTransferTargetId("");
            setTransferLocation("");
            setSearchTerm("");
            setSearchResults([]);
            refreshData(user);
        } catch (err: any) {
            alert(err.response?.data?.detail || "Transfer failed. Ensure item is Functional and within hierarchy.");
        }
    };

    // --- Assign Owner Logic ---
    const selectUserForAssign = (user: User) => {
        setAssignOwnerTargetId(user.id.toString());
    };

    const handleAssignOwner = async () => {
        if (!selectedItem || !assignOwnerTargetId) return;
        try {
            await api.post('/equipment/assign_owner/', {
                equipment_id: selectedItem.id,
                owner_id: parseInt(assignOwnerTargetId)
            });
            alert("Ownership Assigned Successfully!");
            setIsAssignOwnerModalOpen(false);
            setSelectedItem(null);
            setAssignOwnerTargetId("");
            setSearchTerm("");
            refreshData(user);
        } catch (err: any) {
            alert(err.response?.data?.detail || "Assignment failed");
        }
    };


    // --- Approval Logic ---
    const approveFault = async (id: number) => {
        try {
            await api.put(`/setup/fault_types/${id}/approve`);
            fetchFaultTypes();
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


    if (initLoading) return (
        <div className="flex h-screen items-center justify-center bg-gray-50">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
        </div>
    );

    return (
        <div className="min-h-screen bg-gray-100 p-8 relative">
            {/* Header */}
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

            {/* Top Toolbar */}
            <div className="flex justify-end gap-2 mb-4 px-1">
                {userRole === 'master' && (
                    <button
                        onClick={() => setIsAdminOpen(true)}
                        className="bg-slate-800 text-white px-5 py-2 rounded-lg shadow-sm hover:bg-slate-900 transition-all font-medium flex items-center gap-2"
                    >
                        🛡️ Admin Panel
                    </button>
                )}

                {userRole !== 'user' && (
                    <button
                        onClick={() => setShowDailyLog(!showDailyLog)}
                        className={`px-5 py-2 rounded-lg shadow-sm transition-all font-medium flex items-center gap-2
                        ${showDailyLog ? 'bg-indigo-100 text-indigo-700' : 'bg-white text-gray-700 border border-gray-200'}`}
                    >
                        📋 {showDailyLog ? 'Hide Log' : 'Show Daily Log'}
                    </button>
                )}

                {userRole !== 'user' && (
                    <button
                        onClick={() => setIsReportOpen(true)}
                        className="bg-indigo-600 text-white px-5 py-2 rounded-lg shadow-sm hover:bg-indigo-700 transition-all font-medium flex items-center gap-2"
                    >
                        📊 Advanced Reports
                    </button>
                )}
            </div>

            {/* Manager Panel (Pending Requests) */}
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

            {error || dataError ? (
                <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
                    {error || dataError}
                </div>
            ) : (
                <div className="space-y-8">
                    {/* Operational Log Section */}
                    {showDailyLog && (
                        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 animate-fade-in-down">
                            <div className="flex justify-between items-center mb-4">
                                <h2 className="text-xl font-bold text-gray-800">Operational Log (24h)</h2>
                                <ExportControls />
                            </div>
                            <DailyActivityTable />
                        </div>
                    )}

                    {/* Stats Grid */}
                    {stats && <StatsGrid stats={stats} />}

                    {/* Equipment Table */}
                    <EquipmentTable
                        equipment={equipment}
                        user={user}
                        userRole={userRole}
                        title={user?.profile?.can_view_company_realtime ? "Unit Equipment (Accessible)" : "My Equipment"}
                        canManageAssets={canManageAssets}
                        onVerify={handleVerify}
                        onRepair={handleRepair}
                        onReportFault={openReportModal}
                        onTransfer={openTransferModal}
                        onAssign={openAssignOwnerModal}
                    />
                </div>
            )}

            <div className="mt-8 text-center text-xs text-gray-400">
                Military Logistics System v4.0 • Secured Environment
            </div>

            {/* --- MODALS --- */}

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

            {/* General Report Page Modal */}
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

            {/* Transfer Modal */}
            {isTransferModalOpen && selectedItem && (
                <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
                    <div className="bg-white rounded-xl shadow-2xl w-full max-w-md overflow-hidden p-6 space-y-4">
                        <h3 className="text-lg font-bold text-gray-800">Transfer Possesion</h3>
                        <p className="text-sm text-gray-600">
                            Transferring <b>{selectedItem.type} (#{selectedItem.id})</b>.
                        </p>

                        <div className="flex bg-gray-100 p-1 rounded-lg">
                            <button
                                onClick={() => setTransferMode('person')}
                                className={`flex-1 py-1 text-sm font-medium rounded-md transition-all ${transferMode === 'person' ? 'bg-white shadow-sm text-blue-600' : 'text-gray-500 hover:text-gray-700'}`}
                            >
                                👤 To Person
                            </button>
                            <button
                                onClick={() => setTransferMode('location')}
                                className={`flex-1 py-1 text-sm font-medium rounded-md transition-all ${transferMode === 'location' ? 'bg-white shadow-sm text-green-600' : 'text-gray-500 hover:text-gray-700'}`}
                            >
                                🏢 To Location
                            </button>
                        </div>

                        {transferMode === 'person' ? (
                            <div className="space-y-2">
                                <label className="block text-sm font-medium text-gray-700">Find Recipient</label>
                                <input
                                    type="text"
                                    placeholder="Type Name or Personal ID..."
                                    className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                                    value={searchTerm}
                                    onChange={e => setSearchTerm(e.target.value)}
                                />
                                <div className="max-h-40 overflow-y-auto border rounded bg-gray-50 p-2 space-y-1">
                                    {isSearching ? <div className="text-xs text-gray-400">Searching...</div> :
                                        searchResults.length > 0 ? searchResults.map(u => (
                                            <div key={u.id}
                                                className={`p-2 text-sm rounded cursor-pointer ${transferTargetId === u.id.toString() ? 'bg-blue-100 text-blue-700 border border-blue-200' : 'hover:bg-gray-100'}`}
                                                onClick={() => selectUserForTransfer(u)}
                                            >
                                                <b>{u.full_name}</b> <span className="text-xs text-gray-500">({u.personal_number})</span>
                                            </div>
                                        )) : <div className="text-xs text-gray-400 text-center">No users found</div>}
                                </div>
                            </div>
                        ) : (
                            <div className="space-y-2">
                                <label className="block text-sm font-medium text-gray-700">Enter Location Name</label>
                                <input
                                    type="text"
                                    placeholder="e.g. Warehouse A, Room 302..."
                                    className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-green-500 outline-none"
                                    value={transferLocation}
                                    onChange={e => setTransferLocation(e.target.value)}
                                />
                            </div>
                        )}

                        <div className="flex gap-3 mt-4">
                            <button onClick={() => setIsTransferModalOpen(false)} className="flex-1 py-2 border rounded-lg hover:bg-gray-50">Cancel</button>
                            <button onClick={handleTransfer} className="flex-1 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-bold">Confirm Transfer</button>
                        </div>
                    </div>
                </div>
            )}

            {/* Assign Owner Modal */}
            {isAssignOwnerModalOpen && selectedItem && (
                <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
                    <div className="bg-white rounded-xl shadow-2xl w-full max-w-md overflow-hidden p-6 space-y-4">
                        <h3 className="text-lg font-bold text-gray-800">Assign Owner (Permanent)</h3>
                        <p className="text-sm text-gray-600">
                            Assigning responsible owner for <b>{selectedItem.type} (#{selectedItem.id})</b>.
                        </p>

                        <div className="space-y-2">
                            <label className="block text-sm font-medium text-gray-700">Find Owner</label>
                            <input
                                type="text"
                                placeholder="Type Name or Personal ID..."
                                className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-purple-500 outline-none"
                                value={searchTerm}
                                onChange={e => setSearchTerm(e.target.value)}
                            />
                            <div className="max-h-40 overflow-y-auto border rounded bg-gray-50 p-2 space-y-1">
                                {isSearching ? <div className="text-xs text-gray-400">Searching...</div> :
                                    searchResults.length > 0 ? searchResults.map(u => (
                                        <div key={u.id}
                                            className={`p-2 text-sm rounded cursor-pointer ${assignOwnerTargetId === u.id.toString() ? 'bg-purple-100 text-purple-700 border border-purple-200' : 'hover:bg-gray-100'}`}
                                            onClick={() => selectUserForAssign(u)}
                                        >
                                            <b>{u.full_name}</b> <span className="text-xs text-gray-500">({u.personal_number})</span>
                                        </div>
                                    )) : <div className="text-xs text-gray-400 text-center">No users found</div>}
                            </div>
                        </div>

                        <div className="flex gap-3 mt-4">
                            <button onClick={() => setIsAssignOwnerModalOpen(false)} className="flex-1 py-2 border rounded-lg hover:bg-gray-50">Cancel</button>
                            <button onClick={handleAssignOwner} className="flex-1 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 font-bold">Confirm Assignment</button>
                        </div>
                    </div>
                </div>
            )}

        </div>
    );
}
