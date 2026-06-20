import { useState } from "react";
import {
  BookmarkIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FileTextIcon,
  FolderIcon,
  FolderOpenIcon,
  PlusIcon,
  SearchIcon,
  StickyNoteIcon,
} from "lucide-react";

interface Doc {
  id: string;
  title: string;
  children?: Doc[];
}

const TREE: Doc[] = [
  {
    id: "getting-started",
    title: "Getting started",
    children: [
      { id: "install", title: "Install the CLI" },
      { id: "quickstart", title: "Quickstart" },
      { id: "structure", title: "Project structure" },
    ],
  },
  {
    id: "concepts",
    title: "Concepts",
    children: [
      { id: "workspaces", title: "Workspaces" },
      { id: "teams", title: "Teams" },
      { id: "permissions", title: "Permissions" },
      {
        id: "events",
        title: "Realtime events",
        children: [
          { id: "events-overview", title: "Overview" },
          { id: "events-domain", title: "Domain events" },
          { id: "events-projector", title: "Projectors" },
        ],
      },
    ],
  },
  {
    id: "guides",
    title: "Guides",
    children: [
      { id: "add-permission", title: "Add a permission" },
      { id: "add-context", title: "Add a bounded context" },
      { id: "add-job", title: "Write a job" },
    ],
  },
  {
    id: "reference",
    title: "Reference",
    children: [
      { id: "ref-api", title: "API" },
      { id: "ref-cli", title: "CLI flags" },
    ],
  },
];

export function SidebarsDocsTreeShowcasePage() {
  const [active, setActive] = useState<string>("events-projector");
  const [open, setOpen] = useState<Record<string, boolean>>({
    "getting-started": true,
    concepts: true,
    events: true,
    guides: false,
    reference: false,
  });

  const toggle = (id: string) =>
    setOpen((o) => ({ ...o, [id]: !o[id] }));

  return (
    <div className="grid min-h-svh grid-cols-[280px_1fr] bg-background text-foreground">
      <aside className="flex h-svh flex-col border-r border-border/60 bg-foreground/[0.02]">
        <div className="border-b border-border/60 px-4 py-3.5">
          <div className="flex items-center gap-2">
            <div className="flex size-6 items-center justify-center rounded bg-foreground/[0.08]">
              <BookmarkIcon className="size-3.5" />
            </div>
            <div className="font-medium text-sm">Documentation</div>
          </div>
          <div className="mt-3 flex items-center gap-2 rounded-md border border-border/60 bg-background/60 px-2.5 py-1.5">
            <SearchIcon className="size-3.5 opacity-50" />
            <span className="flex-1 truncate text-muted-foreground text-xs">
              Search docs…
            </span>
            <kbd className="rounded border border-border/60 bg-background/80 px-1 font-mono text-[9px] text-muted-foreground">
              /
            </kbd>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto py-3">
          <ul>
            {TREE.map((doc) => (
              <TreeNode
                key={doc.id}
                doc={doc}
                depth={0}
                open={open}
                onToggle={toggle}
                active={active}
                onSelect={setActive}
              />
            ))}
          </ul>
        </nav>

        <div className="border-t border-border/60 px-3 py-2.5">
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-muted-foreground text-xs transition-colors hover:bg-foreground/[0.03] hover:text-foreground"
          >
            <PlusIcon className="size-3.5" />
            New page
          </button>
        </div>
      </aside>

      <main className="flex items-center justify-center text-muted-foreground">
        <div className="text-center">
          <StickyNoteIcon className="mx-auto size-6 opacity-40" />
          <p className="mt-3 text-sm">Selected · {active}</p>
        </div>
      </main>
    </div>
  );
}

function TreeNode({
  doc,
  depth,
  open,
  onToggle,
  active,
  onSelect,
}: {
  doc: Doc;
  depth: number;
  open: Record<string, boolean>;
  onToggle: (id: string) => void;
  active: string;
  onSelect: (id: string) => void;
}) {
  const isFolder = !!doc.children?.length;
  const isOpen = open[doc.id] ?? false;
  const isActive = active === doc.id;

  return (
    <li>
      <button
        type="button"
        onClick={() =>
          isFolder ? onToggle(doc.id) : onSelect(doc.id)
        }
        style={{ paddingLeft: `${depth * 14 + 12}px` }}
        className={`flex w-full items-center gap-1.5 py-1 pr-3 text-left text-sm transition-colors ${
          isActive
            ? "bg-foreground/[0.08] text-foreground"
            : "text-muted-foreground hover:bg-foreground/[0.03] hover:text-foreground"
        }`}
      >
        {isFolder ? (
          isOpen ? (
            <ChevronDownIcon className="size-3.5 shrink-0 opacity-60" />
          ) : (
            <ChevronRightIcon className="size-3.5 shrink-0 opacity-60" />
          )
        ) : (
          <span className="size-3.5 shrink-0" />
        )}
        {isFolder ? (
          isOpen ? (
            <FolderOpenIcon className="size-3.5 shrink-0 opacity-70" />
          ) : (
            <FolderIcon className="size-3.5 shrink-0 opacity-70" />
          )
        ) : (
          <FileTextIcon className="size-3.5 shrink-0 opacity-60" />
        )}
        <span className="truncate">{doc.title}</span>
      </button>
      {isFolder && isOpen && doc.children ? (
        <ul>
          {doc.children.map((child) => (
            <TreeNode
              key={child.id}
              doc={child}
              depth={depth + 1}
              open={open}
              onToggle={onToggle}
              active={active}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
