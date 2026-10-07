import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { readLocal, writeLocal } from "@/lib/safeStorage";

type Theme = "light" | "dark";

// Guarded read: readLocal answers null when the storage accessor throws, so
// a browser that blocks storage falls back to the system preference instead
// of failing the render. A stored value that is neither theme falls back the
// same way. Taking it as the theme left the page light, with a button that
// offered "Light" or, for an empty string, no button at all.
//
// The script in index.html makes this same choice before the first paint.
// Change one and ThemeToggle.test.tsx fails until the other matches.
function initialTheme(): Theme {
    const stored = readLocal("theme");
    if (stored === "dark" || stored === "light") {
        return stored;
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeToggle() {
    // FE-H6: the theme is read once, in the state initialiser, so the button
    // is there on the first frame and no effect has to write state.
    const [theme, setTheme] = useState<Theme>(initialTheme);

    useEffect(() => {
        if (theme === "dark") {
            document.documentElement.classList.add("dark");
        } else {
            document.documentElement.classList.remove("dark");
        }
    }, [theme]);

    const toggleTheme = () => {
        const newTheme = theme === "light" ? "dark" : "light";
        setTheme(newTheme);
        writeLocal("theme", newTheme);
    };

    return (
        <Button
            variant="ghost"
            size="icon"
            onClick={toggleTheme}
            className="rounded-full w-10 h-10 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            title={`Switch to ${theme === "light" ? "Dark" : "Light"} Mode`}
        >
            {theme === "light" ? (
                <Moon className="h-5 w-5" />
            ) : (
                <Sun className="h-5 w-5" />
            )}
        </Button>
    );
}
