import { useEffect, useState } from 'react';
import api from '../api';

interface DailyActivityItem {
    item_type: string;
    serial_number: string;
    unit: string;
    event_type: string; // "Movement" | "Fault" | "Fix"
    timestamp: string;
    reporter_name: string;
    status: string; // e.g. "On Time" or "Late"
}

export default function DailyActivityTable() {
    const [activities, setActivities] = useState<DailyActivityItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchActivity = async () => {
            try {
                const res = await api.get('/reports/daily_movement');
                setActivities(res.data.items || []);
            } catch (err) {
                console.error("Failed to fetch daily activity", err);
                setError("Failed to load activity log.");
            } finally {
                setLoading(false);
            }
        };

        fetchActivity();
    }, []);

    if (loading) return <div className="p-4 text-center text-gray-500">Loading daily activity...</div>;
    if (error) return <div className="p-4 text-center text-red-500">{error}</div>;
    if (activities.length === 0) return <div className="p-4 text-center text-gray-400">No activity recorded in the last 24 hours.</div>;

    return (
        <div className="overflow-x-auto">
            <table className="w-full text-left bg-white rounded-lg overflow-hidden">
                <thead className="bg-gray-50 text-gray-500 text-xs uppercase font-semibold border-b border-gray-100">
                    <tr>
                        <th className="px-4 py-3">Time</th>
                        <th className="px-4 py-3">Type</th>
                        <th className="px-4 py-3">Tsadiq (Serial)</th>
                        <th className="px-4 py-3">Event</th>
                        <th className="px-4 py-3">Reporter</th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 text-sm">
                    {activities.map((item, index) => (
                        <tr key={index} className="hover:bg-gray-50 transition-colors">
                            <td className="px-4 py-3 text-gray-600 font-mono text-xs">
                                {new Date(item.timestamp).toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' })}
                            </td>
                            <td className="px-4 py-3 font-medium text-gray-800">{item.item_type}</td>
                            <td className="px-4 py-3 font-mono font-bold text-indigo-600">{item.serial_number}</td>
                            <td className="px-4 py-3">
                                <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium
                                    ${item.status === 'Late' ? 'bg-red-100 text-red-800' : 'bg-blue-50 text-blue-700'}`}>
                                    {item.event_type}
                                </span>
                            </td>
                            <td className="px-4 py-3 text-gray-600 text-xs">{item.reporter_name}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
