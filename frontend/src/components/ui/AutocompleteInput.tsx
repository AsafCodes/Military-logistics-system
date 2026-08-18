import React, { useState, useRef, useEffect } from 'react';

interface AutocompleteInputProps {
    options: string[];
    value: string;
    onChange: (val: string) => void;
    placeholder?: string;
}

export const AutocompleteInput: React.FC<AutocompleteInputProps> = ({ options, value, onChange, placeholder = "Type to search..." }) => {
    const [isOpen, setIsOpen] = useState(false);
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

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        onChange(e.target.value);
        setIsOpen(true);
    };

    const handleSelect = (val: string) => {
        onChange(val);
        setIsOpen(false);
    };

    const filteredOptions = value
        ? options.filter(opt => opt.toLowerCase().includes(value.toLowerCase()))
        : options;

    return (
        <div className="relative w-full" ref={containerRef}>
            <input
                type="text"
                className="w-full px-2 py-1 border border-gray-300 rounded text-xs focus:ring-1 focus:ring-indigo-500 outline-none h-8"
                placeholder={placeholder}
                value={value}
                onChange={handleChange}
                onFocus={() => setIsOpen(true)}
            />
            {isOpen && filteredOptions.length > 0 && (
                <div className="absolute z-50 mt-1 w-full bg-white border border-gray-200 rounded shadow-lg max-h-48 overflow-y-auto">
                    {filteredOptions.map((opt, idx) => (
                        <div
                            key={idx}
                            className="px-3 py-1.5 hover:bg-gray-50 cursor-pointer text-xs text-gray-700 truncate"
                            onClick={() => handleSelect(opt)}
                        >
                            {opt}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};
