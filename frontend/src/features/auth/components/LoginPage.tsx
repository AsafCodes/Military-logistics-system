import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Loader2, ShieldCheck, ArrowRight } from "lucide-react";

// Shadcn UI Components
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import ParticlesBackground from "@/components/ui/ParticlesBackground";

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
            // Error from server
            setServerError("שגיאת התחברות: בדוק את הפרטים או את החיבור לרשת.");
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="min-h-screen w-full flex items-center justify-center bg-slate-50 dark:bg-slate-900 relative overflow-hidden" dir="rtl">

            {/* Layer 0: Particles Background (Bottom) */}
            <ParticlesBackground className="absolute inset-0 z-0" />

            {/* Layer 1: Decorative Background Blobs (Overlay) */}
            <div className="absolute top-0 left-0 w-full h-full overflow-hidden z-0 pointer-events-none">
                <div className="absolute -top-[20%] -left-[10%] w-[50%] h-[50%] rounded-full bg-blue-100/50 blur-3xl opacity-60 dark:bg-blue-900/20" />
                <div className="absolute top-[40%] -right-[10%] w-[40%] h-[40%] rounded-full bg-indigo-100/50 blur-3xl opacity-60 dark:bg-indigo-900/20" />
            </div>

            {/* Layer 2: Login Card (Top - z-10 for interactivity) */}
            <Card className="w-full max-w-md mx-4 shadow-2xl border-t-4 border-t-indigo-600 z-10 bg-white/80 backdrop-blur-sm dark:bg-slate-950/80">
                <CardHeader className="space-y-1 text-center">
                    <div className="flex justify-center mb-4">
                        <div className="h-12 w-12 bg-indigo-100 rounded-full flex items-center justify-center text-indigo-600">
                            <ShieldCheck size={28} />
                        </div>
                    </div>
                    <CardTitle className="text-2xl font-bold tracking-tight">
                        Military Logistics
                    </CardTitle>
                    <CardDescription>
                        מערכת ניהול ובקרה לוגיסטית מאובטחת
                    </CardDescription>
                </CardHeader>

                <CardContent>
                    <Form {...form}>
                        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">

                            {/* Personal Number Field */}
                            <FormField
                                control={form.control}
                                name="personalNumber"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>מספר אישי</FormLabel>
                                        <FormControl>
                                            <Input
                                                placeholder="הזן מספר אישי..."
                                                {...field}
                                                className="bg-white dark:bg-slate-900/50"
                                                disabled={isLoading}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />

                            {/* Password Field */}
                            <FormField
                                control={form.control}
                                name="password"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>סיסמה</FormLabel>
                                        <FormControl>
                                            <Input
                                                type="password"
                                                placeholder="••••••••"
                                                {...field}
                                                className="bg-white dark:bg-slate-900/50"
                                                disabled={isLoading}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />

                            {/* Server Error Message */}
                            {serverError && (
                                <div className="p-3 text-sm text-red-500 bg-red-50 rounded-md border border-red-100 flex items-center gap-2">
                                    Alert: {serverError}
                                </div>
                            )}

                            <Button
                                type="submit"
                                className="w-full bg-indigo-600 hover:bg-indigo-700 text-white transition-all h-11 text-md shadow-md hover:shadow-lg"
                                disabled={isLoading}
                            >
                                {isLoading ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        מתחבר...
                                    </>
                                ) : (
                                    <>
                                        כניסה למערכת
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </>
                                )}
                            </Button>
                        </form>
                    </Form>
                </CardContent>

                <CardFooter className="flex flex-col space-y-2 text-center text-xs text-gray-500">
                    <p>חיבור מאובטח (SSL/TLS)</p>
                    <p>גרסה 4.0 • סביבה מבצעית</p>
                </CardFooter>
            </Card>
        </div>
    );
}
