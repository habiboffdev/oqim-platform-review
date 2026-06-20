import { useMemo, useState } from "react";
import {
  ChevronDownIcon,
  PlusIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { CheckboxGroup } from "@/components/ui/checkbox-group";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";
import { Popover, PopoverPopup, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectItem,
  SelectPopup,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Slider } from "@/components/ui/slider";

type StatusVal = "active" | "trial" | "churned" | "paused";
type PlanVal = "free" | "pro" | "enterprise";
type CountryVal = "US" | "CA" | "UK" | "DE" | "FR";

interface Customer {
  id: string;
  name: string;
  status: StatusVal;
  plan: PlanVal;
  country: CountryVal;
  mrr: number;
  lastSeenDays: number;
  tags: string[];
}

const CUSTOMERS: Customer[] = [
  { id: "c1", name: "Foundry Labs", status: "active", plan: "pro", country: "UK", mrr: 1200, lastSeenDays: 1, tags: ["enterprise", "design"] },
  { id: "c2", name: "Northwind Co.", status: "active", plan: "pro", country: "US", mrr: 480, lastSeenDays: 2, tags: ["smb"] },
  { id: "c3", name: "Halcyon Ventures", status: "active", plan: "enterprise", country: "DE", mrr: 9800, lastSeenDays: 0, tags: ["enterprise", "finance"] },
  { id: "c4", name: "Atlas Studio", status: "paused", plan: "pro", country: "CA", mrr: 0, lastSeenDays: 60, tags: ["agency"] },
  { id: "c5", name: "Lumen Health", status: "active", plan: "enterprise", country: "US", mrr: 14200, lastSeenDays: 3, tags: ["enterprise", "health"] },
  { id: "c6", name: "Nimbus IO", status: "trial", plan: "pro", country: "US", mrr: 0, lastSeenDays: 1, tags: ["devtools"] },
  { id: "c7", name: "Frequency", status: "churned", plan: "pro", country: "FR", mrr: 0, lastSeenDays: 92, tags: ["agency"] },
  { id: "c8", name: "Quartz Bank", status: "active", plan: "enterprise", country: "UK", mrr: 22000, lastSeenDays: 5, tags: ["enterprise", "finance"] },
  { id: "c9", name: "Cobalt Studio", status: "trial", plan: "free", country: "US", mrr: 0, lastSeenDays: 0, tags: ["agency", "design"] },
  { id: "c10", name: "Ridgeline", status: "active", plan: "pro", country: "DE", mrr: 740, lastSeenDays: 8, tags: ["smb"] },
  { id: "c11", name: "Pivot & Co.", status: "active", plan: "free", country: "FR", mrr: 0, lastSeenDays: 14, tags: ["smb"] },
  { id: "c12", name: "Granite Health", status: "active", plan: "enterprise", country: "CA", mrr: 6400, lastSeenDays: 2, tags: ["enterprise", "health"] },
  { id: "c13", name: "Tinto Ltd", status: "paused", plan: "pro", country: "UK", mrr: 0, lastSeenDays: 35, tags: ["smb"] },
  { id: "c14", name: "Vert Studio", status: "active", plan: "pro", country: "FR", mrr: 580, lastSeenDays: 12, tags: ["agency", "design"] },
  { id: "c15", name: "Slate Group", status: "active", plan: "enterprise", country: "US", mrr: 18800, lastSeenDays: 0, tags: ["enterprise"] },
  { id: "c16", name: "Boreal", status: "trial", plan: "free", country: "CA", mrr: 0, lastSeenDays: 4, tags: ["devtools"] },
];

interface ChipState {
  status?: { values: StatusVal[] };
  plan?: { value: PlanVal };
  country?: { op: "is" | "is not"; values: CountryVal[] };
  mrr?: { min: number };
  lastSeen?: { days: number };
  tag?: { value: string };
}

type ChipKey = keyof ChipState;

const CHIP_DEFS: { key: ChipKey; label: string }[] = [
  { key: "status", label: "Status" },
  { key: "plan", label: "Plan" },
  { key: "country", label: "Country" },
  { key: "mrr", label: "MRR" },
  { key: "lastSeen", label: "Last seen" },
  { key: "tag", label: "Tag" },
];

const INITIAL_CHIPS: ChipState = {
  status: { values: ["active", "trial"] },
  plan: { value: "pro" },
  country: { op: "is not", values: ["US", "CA"] },
  mrr: { min: 500 },
};

export function FilterChipsShowcasePage() {
  const [query, setQuery] = useState("");
  const [chips, setChips] = useState<ChipState>(INITIAL_CHIPS);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return CUSTOMERS.filter((c) => {
      if (q && !c.name.toLowerCase().includes(q)) return false;
      if (chips.status && !chips.status.values.includes(c.status))
        return false;
      if (chips.plan && c.plan !== chips.plan.value) return false;
      if (chips.country) {
        const inSet = chips.country.values.includes(c.country);
        if (chips.country.op === "is" && !inSet) return false;
        if (chips.country.op === "is not" && inSet) return false;
      }
      if (chips.mrr && c.mrr < chips.mrr.min) return false;
      if (chips.lastSeen && c.lastSeenDays > chips.lastSeen.days) return false;
      if (chips.tag) {
        const needle = chips.tag.value.trim().toLowerCase();
        if (needle && !c.tags.some((t) => t.toLowerCase().includes(needle)))
          return false;
      }
      return true;
    });
  }, [chips, query]);

  const activeChips = (Object.keys(chips) as ChipKey[]).filter(
    (k) => chips[k] !== undefined,
  );
  const availableToAdd = CHIP_DEFS.filter((d) => chips[d.key] === undefined);

  const removeChip = (key: ChipKey) => {
    setChips((c) => {
      const next = { ...c };
      delete next[key];
      return next;
    });
  };

  const updateChip = <K extends ChipKey>(
    key: K,
    value: NonNullable<ChipState[K]>,
  ) => {
    setChips((c) => ({ ...c, [key]: value }));
  };

  const addChip = (key: ChipKey) => {
    if (key === "status") updateChip("status", { values: ["active"] });
    else if (key === "plan") updateChip("plan", { value: "pro" });
    else if (key === "country")
      updateChip("country", { op: "is", values: ["US"] });
    else if (key === "mrr") updateChip("mrr", { min: 500 });
    else if (key === "lastSeen") updateChip("lastSeen", { days: 30 });
    else if (key === "tag") updateChip("tag", { value: "enterprise" });
  };

  return (
    <div className="min-h-svh bg-background px-6 py-12">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6">
          <h1 className="font-heading text-xl">Active filter chips</h1>
          <p className="text-muted-foreground text-sm">
            Field · operator · value pills with quick edit and removal.
          </p>
        </header>

        <div className="flex flex-col gap-3 rounded-xl border bg-card p-4 shadow-xs/5">
          <div className="flex flex-wrap items-center gap-2">
            <InputGroup className="w-72">
              <InputGroupAddon>
                <SearchIcon className="size-4 text-muted-foreground" />
              </InputGroupAddon>
              <InputGroupInput
                placeholder="Search customers…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                nativeInput
              />
            </InputGroup>
            <Separator orientation="vertical" className="mx-1 h-6" />
            <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">
              Filters
            </span>
            <AddFilterPopover
              available={availableToAdd}
              onAdd={addChip}
              disabled={availableToAdd.length === 0}
            />
            <Button
              size="xs"
              variant="ghost"
              className="ms-auto text-muted-foreground"
              onClick={() => {
                setChips({});
                setQuery("");
              }}
              disabled={activeChips.length === 0 && !query}
            >
              <XIcon />
              Clear all
            </Button>
          </div>

          {activeChips.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              {chips.status ? (
                <ChipShell
                  field="Status"
                  op="is any of"
                  tone="include"
                  values={chips.status.values}
                  onRemove={() => removeChip("status")}
                  editor={
                    <StatusEditor
                      value={chips.status.values}
                      onChange={(v) => updateChip("status", { values: v })}
                    />
                  }
                />
              ) : null}
              {chips.plan ? (
                <ChipShell
                  field="Plan"
                  op="is"
                  tone="include"
                  values={[chips.plan.value]}
                  onRemove={() => removeChip("plan")}
                  editor={
                    <PlanEditor
                      value={chips.plan.value}
                      onChange={(v) => updateChip("plan", { value: v })}
                    />
                  }
                />
              ) : null}
              {chips.country ? (
                <ChipShell
                  field="Country"
                  op={chips.country.op}
                  tone={chips.country.op === "is not" ? "exclude" : "include"}
                  values={chips.country.values}
                  onRemove={() => removeChip("country")}
                  editor={
                    <CountryEditor
                      value={chips.country}
                      onChange={(v) => updateChip("country", v)}
                    />
                  }
                />
              ) : null}
              {chips.mrr ? (
                <ChipShell
                  field="MRR"
                  op=">"
                  tone="default"
                  values={[`$${chips.mrr.min.toLocaleString()}`]}
                  onRemove={() => removeChip("mrr")}
                  editor={
                    <MrrEditor
                      value={chips.mrr.min}
                      onChange={(v) => updateChip("mrr", { min: v })}
                    />
                  }
                />
              ) : null}
              {chips.lastSeen ? (
                <ChipShell
                  field="Last seen"
                  op="within"
                  tone="default"
                  values={[`${chips.lastSeen.days}d`]}
                  onRemove={() => removeChip("lastSeen")}
                  editor={
                    <LastSeenEditor
                      value={chips.lastSeen.days}
                      onChange={(v) => updateChip("lastSeen", { days: v })}
                    />
                  }
                />
              ) : null}
              {chips.tag ? (
                <ChipShell
                  field="Tag"
                  op="contains"
                  tone="default"
                  values={[chips.tag.value || "—"]}
                  onRemove={() => removeChip("tag")}
                  editor={
                    <TagEditor
                      value={chips.tag.value}
                      onChange={(v) => updateChip("tag", { value: v })}
                    />
                  }
                />
              ) : null}
            </div>
          ) : null}

          <Separator />

          <div className="flex items-center justify-between text-muted-foreground text-xs">
            <span>
              <span className="font-medium text-foreground">
                {filtered.length} customer{filtered.length === 1 ? "" : "s"}
              </span>{" "}
              match · {activeChips.length} filter
              {activeChips.length === 1 ? "" : "s"} applied
            </span>
            <div className="flex items-center gap-2">
              <Button size="xs" variant="ghost">
                Save as view
              </Button>
              <Button size="xs" variant="outline">
                Share
              </Button>
            </div>
          </div>
        </div>

        <CustomerList customers={filtered} />
      </div>
    </div>
  );
}

function AddFilterPopover({
  available,
  onAdd,
  disabled,
}: {
  available: { key: ChipKey; label: string }[];
  onAdd: (key: ChipKey) => void;
  disabled: boolean;
}) {
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            size="xs"
            variant="outline"
            className="ml-1 border-dashed"
            disabled={disabled}
          >
            <PlusIcon />
            Add filter
          </Button>
        }
      />
      <PopoverPopup className="w-48 p-1">
        <div className="px-2 pt-1 pb-1.5 font-mono text-[10px] text-muted-foreground uppercase tracking-wider">
          Add filter
        </div>
        {available.length === 0 ? (
          <p className="px-2 py-2 text-muted-foreground text-xs">
            All filters added.
          </p>
        ) : (
          available.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => onAdd(f.key)}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent"
            >
              {f.label}
            </button>
          ))
        )}
      </PopoverPopup>
    </Popover>
  );
}

function ChipShell({
  field,
  op,
  values,
  tone,
  onRemove,
  editor,
}: {
  field: string;
  op: string;
  values: string[];
  tone: "default" | "include" | "exclude";
  onRemove: () => void;
  editor: React.ReactNode;
}) {
  const toneClass =
    tone === "include"
      ? "border-emerald-500/30 bg-emerald-500/5 text-foreground"
      : tone === "exclude"
        ? "border-destructive/30 bg-destructive/5 text-foreground"
        : "border-border bg-background text-foreground";
  const opTone =
    tone === "include"
      ? "text-emerald-700 dark:text-emerald-400"
      : tone === "exclude"
        ? "text-destructive"
        : "text-muted-foreground";

  return (
    <div
      className={
        "inline-flex items-center divide-x divide-border overflow-hidden rounded-md border text-xs " +
        toneClass
      }
    >
      <span className="flex items-center gap-1.5 px-2 py-1 font-medium">
        {field}
      </span>
      <span className={"px-2 py-1 font-mono text-[11px] " + opTone}>{op}</span>
      <Popover>
        <PopoverTrigger
          render={
            <button
              type="button"
              className="flex cursor-pointer items-center gap-1 px-2 py-1 hover:bg-accent/60"
            >
              {values.length > 1 ? (
                <span className="font-medium">
                  {values[0]}
                  <span className="text-muted-foreground">
                    {" "}
                    +{values.length - 1}
                  </span>
                </span>
              ) : (
                <span className="font-medium">{values[0]}</span>
              )}
              <ChevronDownIcon className="size-3 opacity-60" />
            </button>
          }
        />
        <PopoverPopup className="w-56 p-2">{editor}</PopoverPopup>
      </Popover>
      <button
        type="button"
        aria-label={`Remove ${field} filter`}
        onClick={onRemove}
        className="flex items-center px-1.5 py-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
      >
        <XIcon className="size-3" />
      </button>
    </div>
  );
}

function StatusEditor({
  value,
  onChange,
}: {
  value: StatusVal[];
  onChange: (v: StatusVal[]) => void;
}) {
  const opts: { v: StatusVal; label: string }[] = [
    { v: "active", label: "Active" },
    { v: "trial", label: "Trial" },
    { v: "paused", label: "Paused" },
    { v: "churned", label: "Churned" },
  ];
  return (
    <CheckboxGroup
      aria-label="Status"
      value={value}
      onValueChange={(v) => onChange(v as StatusVal[])}
      className="flex flex-col gap-1"
    >
      {opts.map((o) => (
        <Label
          key={o.v}
          className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-accent"
        >
          <Checkbox value={o.v} />
          {o.label}
        </Label>
      ))}
    </CheckboxGroup>
  );
}

function PlanEditor({
  value,
  onChange,
}: {
  value: PlanVal;
  onChange: (v: PlanVal) => void;
}) {
  const opts = [
    { value: "free", label: "Free" },
    { value: "pro", label: "Pro" },
    { value: "enterprise", label: "Enterprise" },
  ];
  return (
    <Select
      items={opts}
      value={value}
      onValueChange={(v) => v && onChange(v as PlanVal)}
    >
      <SelectTrigger size="sm">
        <SelectValue />
      </SelectTrigger>
      <SelectPopup>
        {opts.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectPopup>
    </Select>
  );
}

function CountryEditor({
  value,
  onChange,
}: {
  value: { op: "is" | "is not"; values: CountryVal[] };
  onChange: (v: { op: "is" | "is not"; values: CountryVal[] }) => void;
}) {
  const opts: CountryVal[] = ["US", "CA", "UK", "DE", "FR"];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1 rounded-md border p-0.5">
        {(["is", "is not"] as const).map((op) => (
          <button
            key={op}
            type="button"
            onClick={() => onChange({ ...value, op })}
            className={
              "flex-1 rounded-sm px-2 py-1 font-mono text-[11px] " +
              (value.op === op
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-accent")
            }
          >
            {op}
          </button>
        ))}
      </div>
      <CheckboxGroup
        aria-label="Country"
        value={value.values}
        onValueChange={(v) =>
          onChange({ ...value, values: v as CountryVal[] })
        }
        className="flex flex-col gap-1"
      >
        {opts.map((o) => (
          <Label
            key={o}
            className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-accent"
          >
            <Checkbox value={o} />
            {o}
          </Label>
        ))}
      </CheckboxGroup>
    </div>
  );
}

function MrrEditor({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">Min MRR</span>
        <span className="font-mono text-foreground tabular-nums">
          ${value.toLocaleString()}
        </span>
      </div>
      <Slider
        value={[value]}
        min={0}
        max={20000}
        step={100}
        onValueChange={(v) => {
          const arr = v as number[];
          onChange(arr[0] ?? 0);
        }}
      />
    </div>
  );
}

function LastSeenEditor({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">Within</span>
        <span className="font-mono text-foreground tabular-nums">
          {value} days
        </span>
      </div>
      <Slider
        value={[value]}
        min={1}
        max={120}
        step={1}
        onValueChange={(v) => {
          const arr = v as number[];
          onChange(arr[0] ?? 30);
        }}
      />
    </div>
  );
}

function TagEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <InputGroup>
      <InputGroupAddon>
        <SearchIcon className="size-3.5 text-muted-foreground" />
      </InputGroupAddon>
      <InputGroupInput
        placeholder="contains…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 text-sm"
        nativeInput
      />
    </InputGroup>
  );
}

function CustomerList({ customers }: { customers: Customer[] }) {
  if (customers.length === 0) {
    return (
      <div className="mt-6 rounded-xl border border-dashed bg-card/40 p-12 text-center">
        <p className="text-muted-foreground text-sm">
          No customers match these filters.
        </p>
      </div>
    );
  }
  return (
    <ul className="mt-6 divide-y rounded-xl border bg-card shadow-xs/5">
      {customers.map((c) => (
        <li
          key={c.id}
          className="flex items-center gap-3 px-4 py-3 first:rounded-t-xl last:rounded-b-xl hover:bg-background"
        >
          <span
            className={
              "size-1.5 rounded-full " +
              (c.status === "active"
                ? "bg-emerald-500"
                : c.status === "trial"
                  ? "bg-sky-500"
                  : c.status === "paused"
                    ? "bg-amber-500"
                    : "bg-muted-foreground/40")
            }
          />
          <div className="min-w-0 flex-1">
            <div className="font-medium text-sm">{c.name}</div>
            <div className="font-mono text-[11px] text-muted-foreground">
              {c.country} · {c.plan} · {c.tags.join(", ")}
            </div>
          </div>
          <Badge
            variant="outline"
            size="sm"
            className="font-mono text-[10px] uppercase"
          >
            {c.status}
          </Badge>
          <span className="w-20 text-right font-mono text-foreground text-xs tabular-nums">
            ${c.mrr.toLocaleString()}
          </span>
          <span className="w-16 text-right font-mono text-[11px] text-muted-foreground tabular-nums">
            {c.lastSeenDays}d
          </span>
        </li>
      ))}
    </ul>
  );
}
