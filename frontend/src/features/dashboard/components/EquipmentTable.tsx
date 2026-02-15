
import type { Equipment, User } from '@/types';

interface EquipmentTableProps {
    equipment: Equipment[];
    user: User | null;
    userRole: string;
    title: string;
    canManageAssets: boolean;
    onVerify: (id: number) => void;
    onRepair: (item: Equipment) => void;
    onReportFault: (item: Equipment) => void;
    onTransfer: (item: Equipment) => void;
    onAssign: (item: Equipment) => void;
}

export default function EquipmentTable({
    equipment,
    user,
    userRole,
    title,
    canManageAssets,
    onVerify,
    onRepair,
    onReportFault,
    onTransfer,
    onAssign
}: EquipmentTableProps) {
    return (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
            <div className="p-6 border-b border-gray-100">
                <h2 className="text-lg font-bold text-gray-800">
                    {title}
                </h2>
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-left">
                    <thead className="bg-gray-50 text-gray-500 text-sm uppercase font-semibold">
                        <tr>
                            {userRole === 'master' && <th className="px-6 py-4">ID</th>}
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
                                    {/* ID cell */}
                                    {userRole === 'master' && (
                                        <td className="px-6 py-4 font-mono text-gray-600">#{item.id}</td>
                                    )}
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
                                    {/* Personalized Description */}
                                    <td className="px-6 py-4 text-gray-600">
                                        {(item.holder_user_id === user?.id) && (item.holder_user_id !== undefined) ? (
                                            <span className="text-blue-600 font-medium">משויך אליך</span>
                                        ) : (
                                            item.current_state_description
                                        )}
                                    </td>
                                    {/* Compliance Cell */}
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
                                            } else if (level === 'NEUTRAL') {
                                                badgeClass = "bg-white border border-gray-200 text-gray-400";
                                                icon = "☾";
                                            }

                                            return (
                                                <div className="flex flex-col">
                                                    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold w-fit ${badgeClass}`}>
                                                        {icon} {level}
                                                    </span>
                                                    {level !== 'NEUTRAL' && <span className="text-xs text-gray-400 mt-1">{item.compliance_check}</span>}
                                                </div>
                                            );
                                        })()}
                                    </td>

                                    {/* ACTION COLUMN */}
                                    <td className="px-6 py-4 flex gap-2">
                                        {/* 1. Report Present (Conditional) */}
                                        {item.holder_user_id === user?.id && (item.compliance_level === 'WARNING' || item.compliance_level === 'SEVERE') && (
                                            <button
                                                onClick={() => onVerify(item.id)}
                                                className="bg-indigo-50 text-indigo-700 hover:bg-indigo-100 px-4 py-1 rounded border border-indigo-200 text-xs font-bold shadow-sm transition-all"
                                            >
                                                Report Present
                                            </button>
                                        )}

                                        {/* 2. Repair (Conditional) */}
                                        {item.status === 'Malfunctioning' && user?.profile?.can_change_maintenance_status && (
                                            <button
                                                onClick={() => onRepair(item)}
                                                className="bg-green-50 text-green-700 hover:bg-green-100 px-3 py-1 rounded border border-green-200 text-xs font-bold"
                                            >
                                                Repair
                                            </button>
                                        )}

                                        {/* 3. Report Fault Button (Functional only) */}
                                        {item.status === 'Functional' && (
                                            <button
                                                onClick={() => onReportFault(item)}
                                                className="text-red-600 hover:text-red-800 font-medium text-xs border border-red-200 hover:border-red-400 bg-red-50 hover:bg-red-100 px-3 py-1 rounded transition-colors"
                                            >
                                                Report Fault
                                            </button>
                                        )}

                                        {/* 4. Asset Management (Transfer & Assign Owner) */}
                                        {canManageAssets && (
                                            <div className="flex gap-2">
                                                <button
                                                    onClick={() => onTransfer(item)}
                                                    className="bg-blue-50 text-blue-700 hover:bg-blue-100 px-3 py-1 rounded border border-blue-200 text-xs font-bold transition-all"
                                                >
                                                    Transfer
                                                </button>
                                                <button
                                                    onClick={() => onAssign(item)}
                                                    className="bg-purple-50 text-purple-700 hover:bg-purple-100 px-3 py-1 rounded border border-purple-200 text-xs font-bold transition-all whitespace-nowrap"
                                                >
                                                    שינוי ייעוד
                                                </button>
                                            </div>
                                        )}
                                    </td>
                                </tr>
                            ))
                        ) : (
                            <tr>
                                <td colSpan={7} className="px-6 py-8 text-center text-gray-400">
                                    No equipment found.
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
