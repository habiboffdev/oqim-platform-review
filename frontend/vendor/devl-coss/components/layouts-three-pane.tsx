import { useState } from "react";
import {
  BellRingIcon,
  BoldIcon,
  ChevronDownIcon,
  ItalicIcon,
  LinkIcon,
  PinIcon,
  PlusIcon,
  SendIcon,
  SettingsIcon,
  SmileIcon,
  StarIcon,
  UsersIcon,
} from "lucide-react";

type Workspace = {
  id: string;
  letter: string;
  name: string;
  color: string;
};

type Channel = {
  slug: string;
  name: string;
  unread?: boolean;
  count?: number;
};

type Dm = {
  slug: string;
  name: string;
  initials: string;
  presence: "online" | "away" | "offline";
  unread?: number;
};

type AppRow = {
  slug: string;
  name: string;
  color: string;
  letter: string;
};

const WORKSPACES: Workspace[] = [
  { id: "acme", letter: "A", name: "Acme", color: "bg-gradient-to-br from-primary/70 to-primary/30" },
  { id: "moss", letter: "M", name: "Moss", color: "bg-gradient-to-br from-emerald-500/70 to-emerald-500/30" },
  { id: "north", letter: "N", name: "North", color: "bg-gradient-to-br from-amber-500/70 to-amber-500/30" },
  { id: "violet", letter: "V", name: "Violet", color: "bg-gradient-to-br from-violet-500/70 to-violet-500/30" },
];

const CHANNELS: Channel[] = [
  { slug: "general", name: "general" },
  { slug: "design", name: "design", count: 12 },
  { slug: "engineering", name: "engineering", unread: true },
  { slug: "random", name: "random" },
  { slug: "growth", name: "growth", unread: true },
];

const DMS: Dm[] = [
  { slug: "lina", name: "Lina Park", initials: "LP", presence: "online", unread: 2 },
  { slug: "marc", name: "Marc Webber", initials: "MW", presence: "away" },
  { slug: "priya", name: "Priya Shah", initials: "PS", presence: "online" },
  { slug: "ada", name: "Ada Okafor", initials: "AO", presence: "offline", unread: 5 },
];

const APPS: AppRow[] = [
  { slug: "github", name: "GitHub", color: "bg-foreground/80 text-background", letter: "G" },
  { slug: "linear", name: "Linear", color: "bg-violet-500/80 text-white", letter: "L" },
  { slug: "figma", name: "Figma", color: "bg-rose-500/80 text-white", letter: "F" },
];

type Message = {
  id: string;
  author: string;
  initials: string;
  color: string;
  time: string;
  body: string;
  followUp?: string;
};

const MESSAGES_TOP: Message[] = [
  {
    id: "m1",
    author: "Lina Park",
    initials: "LP",
    color: "bg-gradient-to-br from-emerald-500/70 to-emerald-500/30",
    time: "9:14 AM",
    body: "Pushed the new spacing tokens to the design system branch. Let me know if anything looks off in the audit doc — I tried to keep the migration story tight.",
  },
  {
    id: "m2",
    author: "Marc Webber",
    initials: "MW",
    color: "bg-gradient-to-br from-sky-500/70 to-sky-500/30",
    time: "9:21 AM",
    body: "Quick heads-up: the navigation prototype is on staging now. Could use some eyes on the mobile breakpoint before the review at 2.",
    followUp: "Filed two small follow-ups in the tracker.",
  },
  {
    id: "m3",
    author: "Priya Shah",
    initials: "PS",
    color: "bg-gradient-to-br from-amber-500/70 to-amber-500/30",
    time: "10:02 AM",
    body: "Loving the new icon set. The 16px variants finally feel balanced against our type ramp.",
  },
];

const MESSAGES_BOTTOM: Message[] = [
  {
    id: "m4",
    author: "Ada Okafor",
    initials: "AO",
    color: "bg-gradient-to-br from-violet-500/70 to-violet-500/30",
    time: "11:40 AM",
    body: "Posting the usability test recordings here once they're trimmed. Three of five participants stalled on the empty state — worth a session next week?",
  },
  {
    id: "m5",
    author: "Lina Park",
    initials: "LP",
    color: "bg-gradient-to-br from-emerald-500/70 to-emerald-500/30",
    time: "12:08 PM",
    body: "Yes please. I'll prep a couple of quick directions tomorrow morning so we have something to react to.",
    followUp: "Will share Figma links in the thread.",
  },
  {
    id: "m6",
    author: "Marc Webber",
    initials: "MW",
    color: "bg-gradient-to-br from-sky-500/70 to-sky-500/30",
    time: "12:31 PM",
    body: "Heads up — pulling the staging deploy briefly to swap the analytics key. Back in five.",
  },
  {
    id: "m7",
    author: "Priya Shah",
    initials: "PS",
    color: "bg-gradient-to-br from-amber-500/70 to-amber-500/30",
    time: "12:47 PM",
    body: "Settings rework is ready for review. Tried the segmented header pattern we sketched — feels a lot calmer.",
  },
];

export function LayoutsThreePaneShowcasePage() {
  const [activeWorkspace, setActiveWorkspace] = useState(WORKSPACES[0].id);
  const [activeChannel, setActiveChannel] = useState("design");

  const channel = CHANNELS.find((c) => c.slug === activeChannel) ?? CHANNELS[1];

  return (
    <div className="grid min-h-svh grid-cols-[64px_240px_1fr] bg-background text-foreground">
      <aside className="flex flex-col items-center gap-2 border-r border-border/60 bg-foreground/[0.02] py-3">
        {WORKSPACES.map((w) => {
          const isActive = activeWorkspace === w.id;
          return (
            <div key={w.id} className="relative">
              <button
                type="button"
                onClick={() => setActiveWorkspace(w.id)}
                className={`flex size-9 items-center justify-center rounded-lg font-medium text-[12px] text-foreground/85 transition-transform hover:scale-105 ${w.color}`}
                aria-label={w.name}
              >
                {w.letter}
              </button>
              {isActive ? (
                <span className="-translate-y-1/2 absolute top-1/2 -left-3 h-5 w-1 rounded-r bg-foreground" />
              ) : null}
            </div>
          );
        })}

        <button
          type="button"
          className="flex size-9 items-center justify-center rounded-lg border border-dashed border-border/70 text-muted-foreground transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
          aria-label="Add workspace"
        >
          <PlusIcon className="size-4" />
        </button>

        <div className="mt-auto flex flex-col items-center gap-2">
          <button
            type="button"
            className="flex size-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
            aria-label="Settings"
          >
            <SettingsIcon className="size-4" />
          </button>
          <button
            type="button"
            className="flex size-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
            aria-label="Notifications"
          >
            <BellRingIcon className="size-4" />
          </button>
          <div className="relative">
            <div className="flex size-8 items-center justify-center rounded-full bg-foreground font-medium text-[11px] text-background">
              S
            </div>
            <span className="absolute right-0 bottom-0 size-2.5 rounded-full bg-emerald-500 ring-2 ring-background" />
          </div>
        </div>
      </aside>

      <aside className="flex flex-col border-r border-border/60 bg-foreground/[0.015]">
        <div className="border-b border-border/60 px-3 py-3">
          <button
            type="button"
            className="flex w-full items-center justify-between gap-2 text-left"
          >
            <span className="font-heading text-sm">Acme</span>
            <ChevronDownIcon className="size-3.5 opacity-60" />
          </button>
          <div className="mt-0.5 text-muted-foreground text-xs">16 members</div>
        </div>

        <div className="flex-1 overflow-y-auto px-1.5 py-2">
          <SectionLabel>Channels</SectionLabel>
          <ul className="flex flex-col gap-0.5">
            {CHANNELS.map((c) => {
              const isActive = activeChannel === c.slug;
              return (
                <li key={c.slug}>
                  <button
                    type="button"
                    onClick={() => setActiveChannel(c.slug)}
                    className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-sm transition-colors ${
                      isActive
                        ? "bg-foreground/[0.08] text-foreground"
                        : c.unread
                          ? "font-medium text-foreground hover:bg-foreground/[0.04]"
                          : "text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground"
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className="opacity-60">#</span>
                      <span className="truncate">{c.name}</span>
                    </span>
                    {c.unread ? (
                      <span className="size-1.5 shrink-0 rounded-full bg-primary" />
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>

          <SectionLabel>Direct messages</SectionLabel>
          <ul className="flex flex-col gap-0.5">
            {DMS.map((d) => (
              <li key={d.slug}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-muted-foreground text-sm transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="relative">
                      <span className="flex size-5 items-center justify-center rounded-full bg-foreground/10 font-medium text-[9px] text-foreground">
                        {d.initials}
                      </span>
                      <span
                        className={`absolute -right-0.5 -bottom-0.5 size-1.5 rounded-full ring-2 ring-background ${
                          d.presence === "online"
                            ? "bg-emerald-500"
                            : d.presence === "away"
                              ? "bg-amber-500"
                              : "bg-muted-foreground/40"
                        }`}
                      />
                    </span>
                    <span className="truncate">{d.name}</span>
                  </span>
                  {d.unread ? (
                    <span className="rounded-full bg-primary px-1.5 py-0.5 font-mono text-[9px] text-primary-foreground">
                      {d.unread}
                    </span>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>

          <SectionLabel>Apps</SectionLabel>
          <ul className="flex flex-col gap-0.5">
            {APPS.map((a) => (
              <li key={a.slug}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-muted-foreground text-sm transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
                >
                  <span
                    className={`flex size-4 items-center justify-center rounded-sm font-medium text-[9px] ${a.color}`}
                  >
                    {a.letter}
                  </span>
                  <span className="truncate">{a.name}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>

      <main className="flex flex-col">
        <header className="flex items-center gap-3 border-b border-border/60 px-5 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-muted-foreground">#</span>
            <span className="font-heading text-base">{channel.name}</span>
            <span className="hidden truncate text-muted-foreground text-xs md:inline">
              Where the design team hangs out
            </span>
          </div>
          <div className="ml-auto flex items-center gap-1">
            <HeaderIconButton label="Pin">
              <PinIcon className="size-4" />
            </HeaderIconButton>
            <HeaderIconButton label="Star">
              <StarIcon className="size-4" />
            </HeaderIconButton>
            <HeaderIconButton label="Members">
              <UsersIcon className="size-4" />
            </HeaderIconButton>
            <HeaderIconButton label="Settings">
              <SettingsIcon className="size-4" />
            </HeaderIconButton>
            <span className="ml-2 flex items-center gap-1.5 rounded-full border border-border/60 bg-card px-2 py-0.5 text-muted-foreground text-xs">
              <UsersIcon className="size-3 opacity-70" />
              12 members
            </span>
          </div>
        </header>

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
          {MESSAGES_TOP.map((m) => (
            <MessageRow key={m.id} message={m} />
          ))}

          <div className="flex items-center gap-3 py-1">
            <div className="h-px flex-1 bg-border/60" />
            <span className="rounded-full border border-border/60 bg-card px-2.5 py-0.5 font-mono text-[10px] text-muted-foreground uppercase tracking-[0.2em]">
              Today
            </span>
            <div className="h-px flex-1 bg-border/60" />
          </div>

          {MESSAGES_BOTTOM.map((m, i) => (
            <div key={m.id} className="flex flex-col gap-2">
              <MessageRow message={m} />
              {i === 1 ? (
                <div className="ml-12 flex items-center gap-2 rounded-md border border-border/60 bg-card/60 px-2.5 py-1.5">
                  <div className="flex -space-x-1.5">
                    <span className="size-5 rounded-full bg-gradient-to-br from-emerald-500/70 to-emerald-500/30 ring-2 ring-card" />
                    <span className="size-5 rounded-full bg-gradient-to-br from-sky-500/70 to-sky-500/30 ring-2 ring-card" />
                    <span className="size-5 rounded-full bg-gradient-to-br from-violet-500/70 to-violet-500/30 ring-2 ring-card" />
                  </div>
                  <span className="text-foreground text-xs">3 replies</span>
                  <span className="text-muted-foreground text-xs">·</span>
                  <span className="text-muted-foreground text-xs">last reply 2h ago</span>
                </div>
              ) : null}
            </div>
          ))}
        </div>

        <div className="px-5 pb-5">
          <div className="rounded-2xl border border-border bg-card p-3">
            <div
              role="textbox"
              aria-label="Message"
              className="min-h-[56px] w-full resize-none bg-transparent text-sm text-muted-foreground outline-none"
            >
              Message #{channel.name}
            </div>
            <div className="mt-2 flex items-center justify-between border-border/60 border-t pt-2">
              <div className="flex items-center gap-1">
                <ComposerIconButton label="Bold">
                  <BoldIcon className="size-3.5" />
                </ComposerIconButton>
                <ComposerIconButton label="Italic">
                  <ItalicIcon className="size-3.5" />
                </ComposerIconButton>
                <ComposerIconButton label="Link">
                  <LinkIcon className="size-3.5" />
                </ComposerIconButton>
                <ComposerIconButton label="Emoji">
                  <SmileIcon className="size-3.5" />
                </ComposerIconButton>
              </div>
              <button
                type="button"
                className="flex items-center gap-1.5 rounded-md bg-foreground px-2.5 py-1 font-medium text-[12px] text-background transition-opacity hover:opacity-90"
              >
                <SendIcon className="size-3.5" />
                Send
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

function MessageRow({ message }: { message: Message }) {
  return (
    <div className="flex gap-3">
      <div
        className={`mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md font-medium text-[11px] text-foreground/85 ${message.color}`}
      >
        {message.initials}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-heading text-sm">{message.author}</span>
          <span className="font-mono text-[10px] text-muted-foreground">{message.time}</span>
        </div>
        <p className="mt-0.5 text-foreground text-sm leading-relaxed">{message.body}</p>
        {message.followUp ? (
          <p className="mt-1 text-muted-foreground text-sm">{message.followUp}</p>
        ) : null}
      </div>
    </div>
  );
}

function HeaderIconButton({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      className="flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
    >
      {children}
    </button>
  );
}

function ComposerIconButton({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-foreground/[0.05] hover:text-foreground"
    >
      {children}
    </button>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-4 mb-1 px-2 first:mt-1">
      <span className="font-mono text-[9px] text-muted-foreground/70 uppercase tracking-[0.25em]">
        {children}
      </span>
    </div>
  );
}
