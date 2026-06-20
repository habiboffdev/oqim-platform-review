import { useState } from "react";
import { CheckIcon, ExternalLinkIcon, SearchIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Integration {
  id: string;
  name: string;
  category: string;
  description: string;
  connected?: boolean;
  badge?: string;
  glyph: string;
  glyphTone: string;
}

const ALL: Integration[] = [
  {
    id: "github",
    name: "GitHub",
    category: "Source",
    description: "Sync issues, link PRs, and post deploy events.",
    connected: true,
    glyph: "GH",
    glyphTone: "bg-foreground text-background",
  },
  {
    id: "linear",
    name: "Linear",
    category: "Project",
    description: "Two-way sync issues and projects.",
    connected: true,
    glyph: "LN",
    glyphTone: "bg-violet-500 text-white",
  },
  {
    id: "slack",
    name: "Slack",
    category: "Notify",
    description: "Pipe activity and alerts into channels.",
    connected: true,
    glyph: "SL",
    glyphTone: "bg-pink-500 text-white",
  },
  {
    id: "stripe",
    name: "Stripe",
    category: "Billing",
    description: "Customer + subscription state in real time.",
    glyph: "ST",
    glyphTone: "bg-indigo-500 text-white",
    badge: "New",
  },
  {
    id: "vercel",
    name: "Vercel",
    category: "Deploy",
    description: "Trigger preview previews and deploy hooks.",
    glyph: "VC",
    glyphTone: "bg-foreground text-background",
  },
  {
    id: "datadog",
    name: "Datadog",
    category: "Observability",
    description: "Send domain events as DD metrics.",
    glyph: "DD",
    glyphTone: "bg-purple-600 text-white",
  },
  {
    id: "sentry",
    name: "Sentry",
    category: "Observability",
    description: "Attach traces to user activity.",
    glyph: "SY",
    glyphTone: "bg-amber-500 text-white",
  },
  {
    id: "notion",
    name: "Notion",
    category: "Docs",
    description: "Embed pages and sync content blocks.",
    glyph: "NT",
    glyphTone: "bg-foreground text-background",
  },
  {
    id: "figma",
    name: "Figma",
    category: "Design",
    description: "Embed frames and watch components.",
    glyph: "FG",
    glyphTone: "bg-rose-500 text-white",
    badge: "Beta",
  },
];

const FILTERS = ["All", "Connected", "Source", "Notify", "Observability", "Design"];

export function SettingsIntegrationsShowcasePage() {
  const [filter, setFilter] = useState("All");
  const [query, setQuery] = useState("");

  const visible = ALL.filter((i) => {
    if (query.trim() && !i.name.toLowerCase().includes(query.toLowerCase())) return false;
    if (filter === "All") return true;
    if (filter === "Connected") return !!i.connected;
    return i.category === filter;
  });

  const connected = ALL.filter((i) => i.connected).length;

  return (
    <div className="min-h-svh bg-background text-foreground">
      <div className="mx-auto max-w-5xl px-8 py-12">
        <div className="font-mono text-[10px] text-muted-foreground uppercase tracking-[0.3em]">
          Settings · Integrations
        </div>
        <h1 className="mt-1 font-heading text-3xl">Integrations</h1>
        <p className="mt-1.5 text-muted-foreground text-sm">
          {connected} connected · {ALL.length - connected} more available
        </p>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="flex flex-1 items-center gap-2 rounded-md border border-border/70 bg-background/40 px-3">
            <SearchIcon className="size-3.5 opacity-50" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find an integration"
              className="h-9 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={`rounded-md px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.25em] transition-colors ${
                  filter === f
                    ? "bg-foreground text-background"
                    : "border border-border/60 text-muted-foreground hover:text-foreground"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <ul className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((i) => (
            <IntegrationCard key={i.id} integration={i} />
          ))}
        </ul>

        {visible.length === 0 ? (
          <div className="mt-8 rounded-xl border border-dashed border-border/70 bg-background/30 px-6 py-12 text-center">
            <p className="text-muted-foreground text-sm">
              Nothing matches "{query}".
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function IntegrationCard({ integration }: { integration: Integration }) {
  return (
    <li className="group flex flex-col gap-3 rounded-xl border border-border/60 bg-background/40 p-4 transition-colors hover:border-foreground/30">
      <div className="flex items-start justify-between">
        <div
          className={`flex size-10 items-center justify-center rounded-lg font-medium text-[12px] ${integration.glyphTone}`}
        >
          {integration.glyph}
        </div>
        {integration.badge ? (
          <span className="rounded bg-foreground/[0.06] px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
            {integration.badge}
          </span>
        ) : null}
      </div>

      <div>
        <div className="flex items-center gap-2">
          <span className="font-medium">{integration.name}</span>
          {integration.connected ? (
            <span className="inline-flex items-center gap-1 rounded bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.2em] text-emerald-700 dark:text-emerald-400">
              <CheckIcon className="size-2.5" />
              On
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 font-mono text-[10px] text-muted-foreground/80 uppercase tracking-[0.2em]">
          {integration.category}
        </div>
        <p className="mt-2 text-muted-foreground text-xs leading-relaxed">
          {integration.description}
        </p>
      </div>

      <div className="mt-auto flex items-center gap-2 pt-1">
        <Button
          size="sm"
          variant={integration.connected ? "outline" : "default"}
          type="button"
          className="flex-1"
        >
          {integration.connected ? "Configure" : "Connect"}
        </Button>
        <button
          type="button"
          className="inline-flex items-center justify-center rounded-md border border-border/60 bg-background/40 p-2 text-muted-foreground transition-colors hover:text-foreground"
          aria-label="Open docs"
        >
          <ExternalLinkIcon className="size-3.5" />
        </button>
      </div>
    </li>
  );
}
