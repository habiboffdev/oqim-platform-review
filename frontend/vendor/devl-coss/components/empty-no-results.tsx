import { useState } from "react";
import { SearchIcon, XIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";

const INITIAL_FILTERS = ["status:open", "owner:me", "label:billing"];

export function EmptyNoResultsShowcasePage() {
  const [query, setQuery] = useState("invoice 2024-Q4");
  const [filters, setFilters] = useState<string[]>(INITIAL_FILTERS);

  return (
    <div className="min-h-svh bg-background px-6 py-12">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        <InputGroup>
          <InputGroupAddon align="inline-start">
            <SearchIcon />
          </InputGroupAddon>
          <InputGroupInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search invoices…"
            nativeInput
          />
        </InputGroup>

        {filters.length ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-[0.2em]">
              Filters
            </span>
            {filters.map((f) => (
              <Badge
                key={f}
                variant="outline"
                className="gap-1 font-mono text-[11px]"
              >
                {f}
                <button
                  type="button"
                  className="-mr-0.5 rounded-sm opacity-60 hover:opacity-100"
                  onClick={() =>
                    setFilters((prev) => prev.filter((x) => x !== f))
                  }
                  aria-label={`Remove ${f}`}
                >
                  <XIcon className="size-3" />
                </button>
              </Badge>
            ))}
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setFilters([])}
              className="ml-1"
            >
              Clear all
            </Button>
          </div>
        ) : null}

        <div className="rounded-xl border border-border/70 border-dashed bg-background/40">
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <SearchIcon aria-hidden="true" />
              </EmptyMedia>
              <EmptyTitle>No matches</EmptyTitle>
              <EmptyDescription>
                We couldn't find anything for{" "}
                <span className="font-mono text-foreground">"{query}"</span>{" "}
                with the filters above. Try removing a filter or searching for
                a different term.
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setFilters([])}
                  disabled={!filters.length}
                >
                  Clear filters
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setQuery("")}>
                  Reset search
                </Button>
              </div>
            </EmptyContent>
          </Empty>
        </div>
      </div>
    </div>
  );
}
