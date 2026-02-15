import { useEffect, useState } from 'react';
import axios from 'axios';

export default function ConnectionTest() {
    const [status, setStatus] = useState<string>('Pinging Server...');
    const [error, setError] = useState<string | null>(null);

    // Hardcoded URL to ensure we hit the correct port
    const BACKEND_URL = 'http://127.0.0.1:8000/';

    useEffect(() => {
        const testConnection = async () => {
            try {
                console.log(`Sending request to: ${BACKEND_URL}`);

                // Use direct axios call, bypassing the generated client
                const response = await axios.get(BACKEND_URL);

                console.log("Response received:", response.data);
                setStatus(response.data.message || JSON.stringify(response.data));
            } catch (err: any) {
                console.error("Direct fetch failed:", err);
                setError(err.message);

                if (err.code === "ERR_NETWORK") {
                    setError("Network Error - Is the backend running on port 8000?");
                }
            }
        };

        testConnection();
    }, []);

    if (error) {
        return (
            <div className="p-4 mt-4 bg-red-50 border border-red-200 rounded-md">
                <h3 className="text-lg font-bold text-red-700 flex items-center gap-2">
                    ❌ Connection Failed
                </h3>
                <p className="text-red-600 font-mono text-sm mt-1">{error}</p>
                <p className="text-gray-500 text-xs mt-2">Target: {BACKEND_URL}</p>
            </div>
        );
    }

    return (
        <div className="p-4 mt-4 bg-green-50 border border-green-200 rounded-md shadow-sm">
            <h3 className="text-lg font-bold text-green-700 flex items-center gap-2">
                ✅ System Online
            </h3>
            <p className="text-gray-700 mt-1 font-medium">{status}</p>
            <div className="mt-2 text-xs text-green-600 bg-green-100 px-2 py-1 rounded inline-block">
                Connected to Port 8000
            </div>
        </div>
    );
}
