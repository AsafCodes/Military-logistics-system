import React, { useState, useRef, useEffect } from 'react';

interface SearchableMultiSelectProps {
    options: string[];
    selected: string[];
    onChange: (selected: string[]) => void;
    placeholder?: string;
}

export const SearchableMultiSelect: React.FC<SearchableMultiSelectProps> = ({ options, selected, onChange, placeholder = "Select..." }) => {
    const [isOpen, setIsOpen] = useState(false);
    const [searchTerm, setSearchTerm] = useState("");
    const containerRef = useRef<HTMLDivElement>(null);

    // Close on click outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    const toggleOption = (option: string) => {
        if (selected.includes(option)) {
            onChange(selected.filter(s => s !== option));
        } else {
            onChange([...selected, option]);
        }
    };

    const filteredOptions = options.filter(opt =>
        opt.toLowerCase().includes(searchTerm.toLowerCase())
    );

    return (
        <div className="relative w-full" ref={containerRef}>
            <div
                className="w-full px-2 py-1 border border-gray-300 rounded bg-white text-xs cursor-pointer flex justify-between items-center h-8"
                onClick={() => setIsOpen(!isOpen)}
            >
                <span className="truncate text-gray-700">
                    {selected.length === 0
                        ? placeholder
                        : selected.length === 1
                            ? selected[0]
                            : `${selected.length} selected`}
                </span>
                <span className="text-gray-400 text-[10px]">▼</span>
            </div>

            {isOpen && (
                <div className="absolute z-50 mt-1 w-48 bg-white border border-gray-200 rounded shadow-lg max-h-60 overflow-hidden flex flex-col">
                    <input
                        type="text"
                        className="w-full px-2 py-1 border-b border-gray-100 text-xs focus:outline-none"
                        placeholder="Search..."
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        autoFocus
                    />
                    <div className="overflow-y-auto flex-1 p-1">
                        {filteredOptions.length > 0 ? (
                            filteredOptions.map(opt => (
                                <div
                                    key={opt}
                                    className="flex items-center gap-2 px-2 py-1.5 hover:bg-gray-50 cursor-pointer rounded"
                                    onClick={() => toggleOption(opt)}
                                >
                                    <input
                                        type="checkbox"
                                        checked={selected.includes(opt)}
                                        readOnly
                                        className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 h-3 w-3"
                                    />
                                    <span className="text-xs text-gray-700 truncate">{opt}</span>
                                </div>
                            ))
                        ) : (
                            <div className="text-xs text-gray-400 p-2 text-center">No results</div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};
