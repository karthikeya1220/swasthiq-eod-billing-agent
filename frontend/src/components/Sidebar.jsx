import { NavLink } from "react-router-dom";
import { BarChart3, LayoutDashboard, MessageSquare, Settings } from "lucide-react";

const navItems = [
  { to: "/reconciliation", icon: LayoutDashboard, label: "EOD Reconciliation" },
  { to: "/analytics", icon: BarChart3, label: "Analytics" },
  { to: "/narrative", icon: MessageSquare, label: "AI Narrative Summary" },
];

export default function Sidebar() {
  return (
    <aside className="sidebar" aria-label="Primary">
      <div className="sidebar-logo" title="SwasthiQ">
        <span className="logo-dot" />
      </div>
      <nav className="sidebar-nav">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `sidebar-item${isActive ? " active" : ""}`
            }
            title={label}
            aria-label={label}
          >
            <Icon size={18} strokeWidth={2} />
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <span
          className="sidebar-item sidebar-item-static"
          title="Settings"
          aria-hidden="true"
        >
          <Settings size={18} strokeWidth={2} />
        </span>
      </div>
    </aside>
  );
}
