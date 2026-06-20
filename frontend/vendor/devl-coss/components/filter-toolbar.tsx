import { useMemo, useState } from "react";
import {
  CalendarIcon,
  LayoutGridIcon,
  ListFilterIcon,
  RowsIcon,
  SearchIcon,
  Settings2Icon,
  XIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";
import {
  Select,
  SelectItem,
  SelectPopup,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

type Status = "active" | "paused" | "archived";
type Owner = "me" | "team" | "other";

interface Project {
  id: string;
  name: string;
  status: Status;
  owner: Owner;
  ownerName: string;
  lastSeenDays: number;
  members: number;
}

const PROJECTS: Project[] = [
  { id: "p1", name: "Atlas pricing v2", status: "active", owner: "me", ownerName: "You", lastSeenDays: 1, members: 4 },
  { id: "p2", name: "Billing webhooks", status: "active", owner: "team", ownerName: "Platform", lastSeenDays: 2, members: 3 },
  { id: "p3", name: "Q2 marketing site", status: "active", owner: "team", ownerName: "Brand", lastSeenDays: 3, members: 6 },
  { id: "p4", name: "Audit log retention", status: "paused", owner: "team", ownerName: "Platform", lastSeenDays: 12, members: 2 },
  { id: "p5", name: "Onboarding rewrite", status: "active", owner: "me", ownerName: "You", lastSeenDays: 4, members: 3 },
  { id: "p6", name: "Mobile app refresh", status: "active", owner: "team", ownerName: "Mobile", lastSeenDays: 8, members: 5 },
  { id: "p7", name: "Search relevance", status: "paused", owner: "other", ownerName: "Marc", lastSeenDays: 22, members: 2 },
  { id: "p8", name: "DataDog migration", status: "archived", owner: "team", ownerName: "Infra", lastSeenDays: 95, members: 4 },
  { id: "p9", name: "Particle field demo", status: "active", owner: "me", ownerName: "You", lastSeenDays: 0, members: 1 },
  { id: "p10", name: "OAuth refresh tokens", status: "active", owner: "team", ownerName: "Platform", lastSeenDays: 6, members: 3 },
  { id: "p11", name: "Legacy SSO sunset", status: "archived", owner: "team", ownerName: "Platform", lastSeenDays: 140, members: 2 },
  { id: "p12", name: "Public changelog", status: "active", owner: "other", ownerName: "Lina", lastSeenDays: 11, members: 2 },
  { id: "p13", name: "Email digest", status: "paused", owner: "me", ownerName: "You", lastSeenDays: 38, members: 1 },
  { id: "p14", name: "Postgres → planetscale", status: "active", owner: "team", ownerName: "Infra", lastSeenDays: 14, members: 4 },
  { id: "p15", name: "Pricing page A/B", status: "active", owner: "other", ownerName: "Priya", lastSeenDays: 5, members: 3 },
  { id: "p16", name: "Holiday landing page", status: "archived", owner: "team", ownerName: "Brand", lastSeenDays: 220, members: 3 },
];

const STATUSES = [
  { label: "All statuses", value: "all" },
  { label: "Active", value: "active" },
  { label: "Paused", value: "paused" },
  { label: "Archived", value: "archived" },
];

const OWNERS = [
  { label: "Anyone", value: "anyone" },
  { label: "Just me", value: "me" },
  { label: "My team", value: "team" },
];

const RANGES = [
  { label: "Any time", value: "any" },
  { label: "Last 7 days", value: "7" },
  { label: "Last 30 days", value: "30" },
  { label: "Last 90 days", value: "90" },
];

const INITIAL = {
  query: "",
  status: "active",
  owner: "anyone",
  range: "30",
  density: "rows" as "rows" | "grid",
};

export function FilterToolbarShowcasePage() {
  const [query, setQuery] = useState(INITIAL.query);
  const [status, setStatus] = useState(INITIAL.status);
  const [owner, setOwner] = useState(INITIAL.owner);
  const [range, setRange] = useState(INITIAL.range);
  const [density, setDensity] = useState<"rows" | "grid">(INITIAL.density);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const days = range === "any" ? Infinity : Number.parseInt(range, 10);
    return PROJECTS.filter((p) => {
      if (q && !p.name.toLowerCase().includes(q)) return false;
      if (status !== "all" && p.status !== status) return false;
      if (owner === "me" && p.owner !== "me") return false;
      if (owner === "team" && p.owner !== "team") return false;
      if (p.lastSeenDays > days) return false;
      return true;
    });
  }, [query, status, owner, range]);

  const activeCount =
    (query ? 1 : 0) +
    (status !== INITIAL.status ? 1 : 0) +
    (owner !== INITIAL.owner ? 1 : 0) +
    (range !== INITIAL.range ? 1 : 0);

  const reset = () => {
    setQuery(INITIAL.query);
    setStatus(INITIAL.status);
    setOwner(INITIAL.owner);
    setRange(INITIAL.range);
  };

  return (
    <div className="min-h-svh bg-background px-6 py-12">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6">
          <h1 className="font-heading text-xl">Toolbar filters</h1>
          <p className="text-muted-foreground text-sm">
            One row · search, scoped selects, date range, density toggle.
          </p>
        </header>

        <div className="flex flex-col gap-3 rounded-xl border bg-card p-3 shadow-xs/5">
          <div className="flex flex-wrap items-center gap-2">
            <InputGroup className="w-64">
              <InputGroupAddon>
                <SearchIcon className="size-4 text-muted-foreground" />
              </InputGroupAddon>
              <InputGroupInput
                placeholder="Search projects…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                nativeInput
              />
              <InputGroupAddon align="inline-end">
                <Kbd>/</Kbd>
              </InputGroupAddon>
            </InputGroup>

            <Separator orientation="vertical" className="mx-1 h-6" />

            <Select
              items={STATUSES}
              value={status}
              onValueChange={(v) => setStatus(v ?? "all")}
            >
              <SelectTrigger className="w-36" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectPopup>
                {STATUSES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectPopup>
            </Select>

            <Select
              items={OWNERS}
              value={owner}
              onValueChange={(v) => setOwner(v ?? "anyone")}
            >
              <SelectTrigger className="w-32" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectPopup>
                {OWNERS.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectPopup>
            </Select>

            <Select
              items={RANGES}
              value={range}
              onValueChange={(v) => setRange(v ?? "any")}
            >
              <SelectTrigger className="w-44" size="sm">
                <CalendarIcon className="text-muted-foreground" />
                <SelectValue />
              </SelectTrigger>
              <SelectPopup>
                {RANGES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectPopup>
            </Select>

            <Button size="sm" variant="ghost" disabled>
              <ListFilterIcon />
              More filters
              <Badge variant="outline" size="sm" className="ml-1 font-mono">
                +0
              </Badge>
            </Button>

            <div className="ms-auto flex items-center gap-2">
              <ToggleGroup
                value={[density]}
                onValueChange={(v) => {
                  const next = (v as string[])[0];
                  if (next === "rows" || next === "grid") setDensity(next);
                }}
                aria-label="Density"
              >
                <ToggleGroupItem value="rows" size="sm" aria-label="Rows">
                  <RowsIcon />
                </ToggleGroupItem>
                <ToggleGroupItem value="grid" size="sm" aria-label="Grid">
                  <LayoutGridIcon />
                </ToggleGroupItem>
              </ToggleGroup>
              <Button size="icon-sm" variant="outline" aria-label="View options">
                <Settings2Icon />
              </Button>
            </div>
          </div>

          <Separator />

          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-xs">Saved view:</span>
            <Button size="xs" variant="secondary" className="font-mono">
              Q2 — Active
            </Button>
            <Button size="xs" variant="ghost" className="text-muted-foreground">
              + New view
            </Button>
            <span className="ms-auto inline-flex items-center gap-1.5 text-muted-foreground text-xs">
              <span
                className={
                  "size-1.5 rounded-full " +
                  (filtered.length === 0
                    ? "bg-destructive"
                    : "bg-emerald-500")
                }
              />
              Showing {filtered.length} of {PROJECTS.length} projects
              {activeCount > 0 ? (
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={reset}
                  className="-mr-2 h-6 px-1.5 text-muted-foreground"
                >
                  <XIcon /> Clear ({activeCount})
                </Button>
              ) : null}
            </span>
          </div>
        </div>

        <Results projects={filtered} density={density} onReset={reset} />
      </div>
    </div>
  );
}

function Results({
  projects,
  density,
  onReset,
}: {
  projects: Project[];
  density: "rows" | "grid";
  onReset: () => void;
}) {
  if (projects.length === 0) {
    return (
      <div className="mt-6 rounded-xl border border-dashed bg-card/40 p-12 text-center">
        <p className="text-muted-foreground text-sm">
          No projects match these filters.
        </p>
        <Button size="sm" variant="outline" className="mt-3" onClick={onReset}>
          Reset filters
        </Button>
      </div>
    );
  }

  if (density === "grid") {
    return (
      <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {projects.map((p) => (
          <ProjectCard key={p.id} project={p} />
        ))}
      </div>
    );
  }

  return (
    <ul className="mt-6 divide-y rounded-xl border bg-card shadow-xs/5">
      {projects.map((p) => (
        <li
          key={p.id}
          className="flex items-center gap-3 px-4 py-3 first:rounded-t-xl last:rounded-b-xl hover:bg-background"
        >
          <StatusDot status={p.status} />
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium text-sm">{p.name}</div>
            <div className="text-muted-foreground text-xs">
              {p.ownerName} · {p.members} member{p.members === 1 ? "" : "s"}
            </div>
          </div>
          <span className="font-mono text-[11px] text-muted-foreground tabular-nums">
            {formatLastSeen(p.lastSeenDays)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ProjectCard({ project }: { project: Project }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-xs/5">
      <div className="flex items-center gap-2">
        <StatusDot status={project.status} />
        <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">
          {project.status}
        </span>
      </div>
      <div className="mt-2 font-medium text-sm">{project.name}</div>
      <div className="mt-1 flex items-center justify-between text-muted-foreground text-xs">
        <span>{project.ownerName}</span>
        <span className="font-mono tabular-nums">
          {formatLastSeen(project.lastSeenDays)}
        </span>
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: Status }) {
  const tone =
    status === "active"
      ? "bg-emerald-500"
      : status === "paused"
        ? "bg-amber-500"
        : "bg-muted-foreground/40";
  return <span className={"size-1.5 shrink-0 rounded-full " + tone} />;
}

function formatLastSeen(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  return `${months}mo ago`;
}
