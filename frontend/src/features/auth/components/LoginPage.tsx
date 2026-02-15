import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Loader2, ShieldCheck } from "lucide-react";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import NetworkGlobe from "@/components/ui/NetworkGlobe";

// Shadcn UI Components
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Form, FormControl, FormField, FormItem, FormMessage } from "@/components/ui/form";

// 1. Schema
const loginSchema = z.object({
    personalNumber: z.string().min(5, {
        message: "מספר אישי חייב להכיל לפחות 5 תווים",
    }),
    password: z.string().min(1, {
        message: "חובה להזין סיסמה",
    }),
});

// Types
export type LoginFormValues = z.infer<typeof loginSchema>;

interface LoginPageProps {
    onLogin: (values: LoginFormValues) => Promise<void>;
}

export default function LoginPage({ onLogin }: LoginPageProps) {
    const [isLoading, setIsLoading] = useState(false);
    const [serverError, setServerError] = useState<string | null>(null);
    const [isDark, setIsDark] = useState(false);

    // Watch for theme changes to pass to Globe
    useEffect(() => {
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.type === "attributes" && mutation.attributeName === "class") {
                    setIsDark(document.documentElement.classList.contains("dark"));
                }
            });
        });

        observer.observe(document.documentElement, { attributes: true });

        // Initial check
        setIsDark(document.documentElement.classList.contains("dark"));

        return () => observer.disconnect();
    }, []);

    // 2. React Hook Form
    const form = useForm<LoginFormValues>({
        resolver: zodResolver(loginSchema),
        defaultValues: {
            personalNumber: "",
            password: "",
        },
    });

    // 3. Submit Handler
    const onSubmit = async (values: LoginFormValues) => {
        setIsLoading(true);
        setServerError(null);
        try {
            await onLogin(values);
        } catch (error: any) {
            setServerError("שגיאת התחברות: בדוק את הפרטים או את החיבור לרשת.");
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="min-h-screen w-full flex flex-col bg-slate-50 dark:bg-[#020617] text-slate-900 dark:text-slate-50 transition-colors duration-500 overflow-hidden" dir="rtl">

            {/* Navbar */}
            <header className="w-full h-16 px-8 flex items-center justify-between z-50 fixed top-0 bg-transparent backdrop-blur-sm">
                <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white">
                        <div className="w-3 h-3 rounded-full bg-white animate-pulse" />
                    </div>
                    <span className="font-bold text-xl tracking-tight">LogicX <span className="text-indigo-600 dark:text-indigo-400">Vector</span></span>
                </div>

                <div className="flex items-center gap-6">
                    <nav className="hidden md:flex gap-6 text-sm font-medium text-slate-600 dark:text-slate-400">
                        <a href="#" className="hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors">Features</a>
                        <a href="#" className="hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors">Solutions</a>
                        <a href="#" className="hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors">Resources</a>
                    </nav>
                    <div className="h-4 w-[1px] bg-slate-200 dark:bg-slate-800 hidden md:block" />
                    <ThemeToggle />
                </div>
            </header>

            {/* Main Content */}
            <main className="flex-1 flex flex-col lg:flex-row relative">

                {/* Left Column: Hero & Form */}
                <div className="lg:w-[45%] w-full flex flex-col justify-center px-8 lg:px-24 pt-20 z-10">
                    <div className="animate-in fade-in slide-in-from-bottom-8 duration-700">
                        <h1 className="text-5xl lg:text-7xl font-bold tracking-tighter leading-[1.1] mb-6">
                            Connect your world with <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-600 to-purple-600 dark:from-indigo-400 dark:to-purple-400">Vector</span>
                        </h1>
                        <p className="text-lg text-slate-600 dark:text-slate-400 max-w-md mb-10 leading-relaxed">
                            Build, track, and manage your logistics with a seamless platform designed for modern command.
                        </p>

                        <div className="w-full max-w-sm">
                            <Form {...form}>
                                <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
                                    <div className="flex gap-4">
                                        <FormField
                                            control={form.control}
                                            name="personalNumber"
                                            render={({ field }) => (
                                                <FormItem className="flex-1">
                                                    <FormControl>
                                                        <Input
                                                            placeholder="מספר אישי"
                                                            {...field}
                                                            className="bg-white/50 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800 h-11 transition-all focus:ring-2 focus:ring-indigo-500"
                                                            disabled={isLoading}
                                                        />
                                                    </FormControl>
                                                    <FormMessage />
                                                </FormItem>
                                            )}
                                        />
                                        <FormField
                                            control={form.control}
                                            name="password"
                                            render={({ field }) => (
                                                <FormItem className="flex-1">
                                                    <FormControl>
                                                        <Input
                                                            type="password"
                                                            placeholder="סיסמה"
                                                            {...field}
                                                            className="bg-white/50 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800 h-11 transition-all focus:ring-2 focus:ring-indigo-500"
                                                            disabled={isLoading}
                                                        />
                                                    </FormControl>
                                                    <FormMessage />
                                                </FormItem>
                                            )}
                                        />
                                    </div>

                                    {serverError && (
                                        <div className="text-sm text-red-500 bg-red-50 dark:bg-red-900/10 p-2 rounded border border-red-100 dark:border-red-900/20">
                                            {serverError}
                                        </div>
                                    )}

                                    <Button
                                        type="submit"
                                        className="w-full bg-indigo-600 hover:bg-indigo-700 text-white h-11 font-medium text-md shadow-lg shadow-indigo-500/20 transition-all hover:scale-[1.02]"
                                        disabled={isLoading}
                                    >
                                        {isLoading ? <Loader2 className="animate-spin" /> : "Sign in / התחברות"}
                                    </Button>
                                </form>
                            </Form>

                            <div className="mt-8 flex items-center gap-2 text-xs text-slate-400">
                                <ShieldCheck className="w-3 h-3" />
                                <span>Military Grade Encryption</span>
                                <span className="mx-2">•</span>
                                <span>SSL/TLS Secured</span>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Right Column: 3D Globe */}
                <div className="lg:w-[55%] w-full h-[50vh] lg:h-auto absolute lg:relative top-0 right-0 opacity-20 lg:opacity-100 pointer-events-none lg:pointer-events-auto">
                    <div className="w-full h-full absolute inset-0 bg-gradient-to-l from-transparent via-transparent to-slate-50 dark:to-[#020617] z-10" />
                    <NetworkGlobe isDark={isDark} />
                </div>

            </main>

            {/* Bottom Stats Bar */}
            <footer className="w-full py-8 border-t border-slate-200 dark:border-slate-800/50 bg-slate-50/50 dark:bg-[#020617]/50 backdrop-blur-sm z-20">
                <div className="container mx-auto px-8 grid grid-cols-2 md:grid-cols-4 gap-8">
                    <div>
                        <div className="text-3xl font-bold text-slate-900 dark:text-white mb-1">87%</div>
                        <div className="text-sm text-slate-500 dark:text-slate-400">Faster Logistics Flow</div>
                    </div>
                    <div>
                        <div className="text-3xl font-bold text-slate-900 dark:text-white mb-1">10k+</div>
                        <div className="text-sm text-slate-500 dark:text-slate-400">Assets Tracked</div>
                    </div>
                    <div>
                        <div className="text-3xl font-bold text-slate-900 dark:text-white mb-1">24/7</div>
                        <div className="text-sm text-slate-500 dark:text-slate-400">Automated Monitoring</div>
                    </div>
                    <div>
                        <div className="text-3xl font-bold text-slate-900 dark:text-white mb-1">99.9%</div>
                        <div className="text-sm text-slate-500 dark:text-slate-400">Uptime Guarantee</div>
                    </div>
                </div>
            </footer>
        </div>
    );
}
