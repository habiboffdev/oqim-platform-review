import {
  BellIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  HashIcon,
  HomeIcon,
  InboxIcon,
  PlusIcon,
} from "lucide-react";
import { useState } from "react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";

const PRIMARY = [
  { key: "home", icon: HomeIcon, label: "Home" },
  { key: "inbox", icon: InboxIcon, label: "Inbox", badge: "4" },
  { key: "activity", icon: BellIcon, label: "Activity" },
];

const PROJECTS = [
  { key: "onboarding", name: "Onboarding redesign", color: "bg-emerald-500/80", count: "23" },
  { key: "q3", name: "Q3 planning", color: "bg-amber-500/80", count: "12" },
  { key: "marketing", name: "Marketing site", color: "bg-violet-500/80", count: "7" },
  { key: "dust", name: "Dust storm", color: "bg-sky-500/80", count: "31" },
];

const TEAMS = [
  { key: "design", name: "design" },
  { key: "engineering", name: "engineering" },
  { key: "growth", name: "growth" },
];

const STATS = [
  { label: "Unread", value: "12", delta: "+3 today" },
  { label: "Mentions", value: "3", delta: "+1 today" },
  { label: "Following", value: "8", delta: "2 active" },
];

const ACTIVITY = [
  { name: "Maya Chen", initials: "MC", action: "commented on Onboarding redesign", time: "2m ago" },
  { name: "Riya Patel", initials: "RP", action: "assigned you to Q3 planning", time: "14m ago" },
  { name: "Tomás García", initials: "TG", action: "mentioned you in Dust storm", time: "1h ago" },
  { name: "Jules Wren", initials: "JW", action: "merged a PR in Marketing site", time: "3h ago" },
  { name: "Priya Shah", initials: "PS", action: "shared a doc with Inbox", time: "yesterday" },
];

export function LayoutsAppShellShowcasePage() {
  const [activeItem, setActiveItem] = useState("inbox");

  return (
    <div className="grid min-h-svh grid-rows-[56px_1fr] bg-background text-foreground">
      <header className="col-span-2 flex h-14 items-center gap-4 border-b border-border/60 bg-background px-4">
        <button
          type="button"
          className="flex items-center gap-2 rounded-md border border-border bg-card px-2 py-1.5 transition-colors hover:bg-foreground/[0.03]"
        >
          <span className="size-6 rounded bg-gradient-to-br from-emerald-500/80 to-sky-500/70" />
          <span className="font-medium text-sm">Acme Inc.</span>
          <ChevronDownIcon className="size-3.5 opacity-60" />
        </button>

        <div className="mx-auto w-full max-w-md">
          <InputGroup>
            <InputGroupInput placeholder="Search…" />
            <InputGroupAddon align="inline-end">
              <Kbd>⌘K</Kbd>
            </InputGroupAddon>
          </InputGroup>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" size="icon" aria-label="Notifications">
            <BellIcon />
          </Button>
          <div className="relative">
            <Avatar className="size-7">
              <AvatarFallback>SB</AvatarFallback>
            </Avatar>
            <span className="absolute right-0 bottom-0 size-2 rounded-full bg-emerald-500 ring-2 ring-background" />
          </div>
        </div>
      </header>

      <div className="grid grid-cols-[256px_1fr] overflow-hidden">
        <aside className="flex w-64 flex-col overflow-y-auto border-r border-border/60 bg-foreground/[0.015]">
          <SidebarSection label="WORKSPACE" />
          <ul className="flex flex-col gap-0.5 px-2">
            {PRIMARY.map((item) => {
              const active = activeItem === item.key;
              return (
                <li key={item.key}>
                  <button
                    type="button"
                    onClick={() => setActiveItem(item.key)}
                    className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                      active
                        ? "bg-foreground/[0.06] text-foreground"
                        : "text-muted-foreground hover:bg-foreground/[0.04]"
                    }`}
                  >
                    <item.icon className="size-4 opacity-70" />
                    <span className="flex-1 text-left">{item.label}</span>
                    {item.badge ? (
                      <span className="rounded-full bg-foreground/10 px-1.5 py-0.5 font-mono text-[9px] text-foreground">
                        {item.badge}
                      </span>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>

          <SidebarSection label="PROJECTS" />
          <ul className="flex flex-col gap-0.5 px-2">
            {PROJECTS.map((p) => {
              const active = activeItem === p.key;
              return (
                <li key={p.key}>
                  <button
                    type="button"
                    onClick={() => setActiveItem(p.key)}
                    className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                      active
                        ? "bg-foreground/[0.06] text-foreground"
                        : "text-muted-foreground hover:bg-foreground/[0.04]"
                    }`}
                  >
                    <span className={`size-2 shrink-0 rounded-sm ${p.color}`} />
                    <span className="flex-1 truncate text-left">{p.name}</span>
                    <span className="font-mono text-[10px] text-muted-foreground/70">
                      {p.count}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          <SidebarSection label="TEAMS" />
          <ul className="flex flex-col gap-0.5 px-2 pb-3">
            {TEAMS.map((t) => {
              const active = activeItem === t.key;
              return (
                <li key={t.key}>
                  <button
                    type="button"
                    onClick={() => setActiveItem(t.key)}
                    className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                      active
                        ? "bg-foreground/[0.06] text-foreground"
                        : "text-muted-foreground hover:bg-foreground/[0.04]"
                    }`}
                  >
                    <HashIcon className="size-3.5 opacity-60" />
                    <span className="flex-1 text-left">{t.name}</span>
                  </button>
                </li>
              );
            })}
          </ul>

          <button
            type="button"
            className="mt-auto flex items-center gap-2 border-t border-border/60 px-3 py-2.5 text-left transition-colors hover:bg-foreground/[0.03]"
          >
            <Avatar className="size-7">
              <AvatarFallback>SB</AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium text-sm">Sean Brydon</div>
              <div className="truncate text-muted-foreground text-xs">
                sean@cal.com
              </div>
            </div>
            <ChevronUpIcon className="size-3.5 opacity-60" />
          </button>
        </aside>

        <main className="overflow-y-auto p-6">
          <div className="font-mono text-muted-foreground text-xs">
            Home / Inbox
          </div>

          <div className="mt-2 flex items-center justify-between">
            <h1 className="font-heading text-2xl tracking-tight">Inbox</h1>
            <Button size="sm">
              <PlusIcon />
              New
            </Button>
          </div>

          <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {STATS.map((s) => (
              <div
                key={s.label}
                className="rounded-xl border border-border bg-card p-4"
              >
                <div className="font-mono text-[10px] text-muted-foreground uppercase tracking-[0.25em]">
                  {s.label}
                </div>
                <div className="mt-2 font-heading text-2xl">{s.value}</div>
                <div className="mt-1 text-muted-foreground text-xs">
                  {s.delta}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-6">
            <div className="mb-2 font-mono text-[10px] text-muted-foreground uppercase tracking-[0.25em]">
              Recent activity
            </div>
            <div className="rounded-xl border border-border bg-card">
              <ul className="divide-y divide-border/60">
                {ACTIVITY.map((a) => (
                  <li
                    key={a.name + a.time}
                    className="flex items-center gap-3 px-4 py-3"
                  >
                    <Avatar className="size-7">
                      <AvatarFallback>{a.initials}</AvatarFallback>
                    </Avatar>
                    <div className="min-w-0 flex-1 truncate text-sm">
                      <span className="font-medium">{a.name}</span>{" "}
                      <span className="text-muted-foreground">{a.action}</span>
                    </div>
                    <span className="font-mono text-[10px] text-muted-foreground/70 uppercase tracking-[0.2em]">
                      {a.time}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

function SidebarSection({ label }: { label: string }) {
  return (
    <div className="px-3 pt-4 pb-1.5 font-mono text-[10px] text-muted-foreground tracking-[0.25em]">
      {label}
    </div>
  );
}
