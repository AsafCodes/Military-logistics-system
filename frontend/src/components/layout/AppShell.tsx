import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
    LayoutDashboard,
    Package,
    Wrench,
    FileBarChart,
    Shield,
    ChevronRight,
    ChevronLeft,
    LogOut,
    Menu,
    X
} from 'lucide-react';
import { ThemeToggle } from '@/components/ui/ThemeToggle';
import type { User } from '@/types';

// ============================================================
// Types
// ============================================================

interface NavItem {
    path: string;
    label: string;
    icon: React.ReactNode;
    requiredRole?: string;
}

interface AppShellProps {
    user: User | null;
    onLogout: () => void;
    children: React.ReactNode;
}

// ============================================================
// Navigation Items
// ============================================================

const NAV_ITEMS: NavItem[] = [
    { path: '/dashboard', label: 'לוח בקרה', icon: <LayoutDashboard size={20} /> },
    { path: '/equipment', label: 'ציוד', icon: <Package size={20} /> },
    { path: '/maintenance', label: 'תחזוקה', icon: <Wrench size={20} /> },
    { path: '/reports', label: 'דוחות', icon: <FileBarChart size={20} /> },
    { path: '/admin', label: 'ניהול מערכת', icon: <Shield size={20} />, requiredRole: 'master' },
];

// ============================================================
// Component
// ============================================================

export default function AppShell({ user, onLogout, children }: AppShellProps) {
    const navigate = useNavigate();
    const location = useLocation();

    // Sidebar state
    const [isExpanded, setIsExpanded] = useState(true);
    const [isMobileOpen, setIsMobileOpen] = useState(false);

    // Close mobile menu on route change
    useEffect(() => {
        setIsMobileOpen(false);
    }, [location.pathname]);

    // Close mobile menu on resize past breakpoint
    useEffect(() => {
        const handleResize = () => {
            if (window.innerWidth >= 768) {
                setIsMobileOpen(false);
            }
        };
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    // Filter nav items by user role
    const visibleItems = NAV_ITEMS.filter(item => {
        if (!item.requiredRole) return true;
        return user?.role === item.requiredRole;
    });

    // Current page label
    const currentPageLabel = visibleItems.find(i => location.pathname.startsWith(i.path))?.label || 'לוח בקרה';

    // Role label in Hebrew
    const getRoleLabel = (role: string) => {
        switch (role) {
            case 'master': return 'מנהל ראשי';
            case 'manager': return 'מפקד';
            case 'technician_manager': return 'קצין טכני';
            case 'user': return 'חייל';
            default: return role;
        }
    };

    return (
        <div className="flex h-screen overflow-hidden bg-background" dir="rtl">
            {/* ── Mobile Overlay ── */}
            {isMobileOpen && (
                <div
                    className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm md:hidden animate-fade-in"
                    onClick={() => setIsMobileOpen(false)}
                />
            )}

            {/* ── Sidebar ── */}
            <aside
                className={`
                    fixed md:relative z-50 h-full flex flex-col
                    glass-sidebar transition-all duration-300 ease-in-out
                    ${isMobileOpen ? 'translate-x-0' : 'translate-x-full md:translate-x-0'}
                    ${isExpanded ? 'w-[var(--sidebar-expanded)]' : 'w-[var(--sidebar-collapsed)]'}
                `}
                style={{ right: 0 }}
            >
                {/* Logo / Brand */}
                <div className="flex items-center justify-between h-16 px-4 border-b border-border/40">
                    {isExpanded && (
                        <div className="flex items-center gap-2 animate-fade-in">
                            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center">
                                <span className="text-white font-bold text-sm">M</span>
                            </div>
                            <span className="font-bold text-foreground text-sm">מערכת סימון</span>
                        </div>
                    )}
                    {/* Collapse button (desktop) */}
                    <button
                        onClick={() => setIsExpanded(!isExpanded)}
                        className="hidden md:flex items-center justify-center w-8 h-8 rounded-lg
                                   text-muted-foreground hover:text-foreground hover:bg-accent
                                   transition-colors"
                        title={isExpanded ? 'כווץ תפריט' : 'הרחב תפריט'}
                    >
                        {isExpanded ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
                    </button>
                    {/* Close button (mobile) */}
                    <button
                        onClick={() => setIsMobileOpen(false)}
                        className="md:hidden flex items-center justify-center w-8 h-8 rounded-lg
                                   text-muted-foreground hover:text-foreground hover:bg-accent
                                   transition-colors"
                    >
                        <X size={16} />
                    </button>
                </div>

                {/* Navigation Links */}
                <nav className="flex-1 py-4 px-2 space-y-1 overflow-y-auto">
                    {visibleItems.map((item) => {
                        const isActive = location.pathname.startsWith(item.path);
                        return (
                            <button
                                key={item.path}
                                onClick={() => navigate(item.path)}
                                title={!isExpanded ? item.label : undefined}
                                className={`
                                    w-full flex items-center gap-3 px-3 py-2.5 rounded-lg
                                    text-sm font-medium transition-all duration-200
                                    ${isActive
                                        ? 'bg-primary/10 text-primary dark:bg-primary/20 dark:text-blue-400 shadow-sm'
                                        : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                                    }
                                    ${!isExpanded ? 'justify-center' : ''}
                                `}
                            >
                                <span className={`flex-shrink-0 ${isActive ? 'text-primary dark:text-blue-400' : ''}`}>
                                    {item.icon}
                                </span>
                                {isExpanded && (
                                    <span className="truncate animate-fade-in">{item.label}</span>
                                )}
                            </button>
                        );
                    })}
                </nav>

                {/* User Section (bottom of sidebar) */}
                <div className="border-t border-border/40 p-3">
                    {isExpanded ? (
                        <div className="animate-fade-in">
                            <div className="flex items-center gap-3 px-2 py-2">
                                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center flex-shrink-0">
                                    <span className="text-white font-bold text-xs">
                                        {user?.full_name?.[0] || '?'}
                                    </span>
                                </div>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-foreground truncate">{user?.full_name || 'משתמש'}</p>
                                    <p className="text-xs text-muted-foreground truncate">{getRoleLabel(user?.role || 'user')}</p>
                                </div>
                            </div>
                            <button
                                onClick={onLogout}
                                className="w-full flex items-center gap-3 px-3 py-2 mt-1 rounded-lg
                                           text-sm text-destructive hover:bg-destructive/10 transition-colors"
                            >
                                <LogOut size={16} />
                                <span>התנתק</span>
                            </button>
                        </div>
                    ) : (
                        <div className="flex flex-col items-center gap-2">
                            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center"
                                title={user?.full_name || 'משתמש'}>
                                <span className="text-white font-bold text-xs">
                                    {user?.full_name?.[0] || '?'}
                                </span>
                            </div>
                            <button
                                onClick={onLogout}
                                className="flex items-center justify-center w-8 h-8 rounded-lg
                                           text-destructive hover:bg-destructive/10 transition-colors"
                                title="התנתק"
                            >
                                <LogOut size={16} />
                            </button>
                        </div>
                    )}
                </div>
            </aside>

            {/* ── Main Content Area ── */}
            <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                {/* Top Bar */}
                <header className="h-16 flex items-center justify-between px-4 md:px-6
                                   border-b border-border/40 bg-background/80 backdrop-blur-sm">
                    {/* Mobile hamburger */}
                    <button
                        onClick={() => setIsMobileOpen(true)}
                        className="md:hidden flex items-center justify-center w-10 h-10 rounded-lg
                                   text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                    >
                        <Menu size={20} />
                    </button>

                    {/* Page Title */}
                    <h1 className="text-lg font-bold text-foreground hidden md:block">
                        {currentPageLabel}
                    </h1>

                    {/* Right controls */}
                    <div className="flex items-center gap-2">
                        <ThemeToggle />
                    </div>
                </header>

                {/* Page Content */}
                <main className="flex-1 overflow-y-auto p-4 md:p-6">
                    {children}
                </main>
            </div>
        </div>
    );
}
