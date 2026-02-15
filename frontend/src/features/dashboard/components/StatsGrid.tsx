
import type { UnitReadiness } from '@/types';

interface StatsGridProps {
    stats: UnitReadiness;
}

export default function StatsGrid({ stats }: StatsGridProps) {
    return (
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
    );
}
