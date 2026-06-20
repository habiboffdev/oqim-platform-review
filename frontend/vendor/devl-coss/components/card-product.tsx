import { HeartIcon, ShoppingBagIcon, StarIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardFooter,
  CardHeader,
  CardPanel,
  CardTitle,
} from "@/components/ui/card";

interface Product {
  slug: string;
  name: string;
  series: string;
  price: number;
  was?: number;
  rating: number;
  reviews: number;
  swatches: string[];
  tag?: string;
  hueA: string;
  hueB: string;
}

const PRODUCTS: Product[] = [
  {
    slug: "orbit-mug",
    name: "Orbit ceramic mug",
    series: "Lab series · 350ml",
    price: 22,
    rating: 4.8,
    reviews: 124,
    swatches: ["#1f1f1f", "#d6d3d1", "#7c3aed"],
    tag: "New",
    hueA: "#fde68a",
    hueB: "#fb7185",
  },
  {
    slug: "orbit-tee",
    name: "Type specimen tee",
    series: "Garment-dyed cotton",
    price: 38,
    was: 48,
    rating: 4.6,
    reviews: 312,
    swatches: ["#0f172a", "#84cc16", "#f59e0b", "#e11d48"],
    tag: "Sale",
    hueA: "#a7f3d0",
    hueB: "#3b82f6",
  },
  {
    slug: "orbit-zine",
    name: "Field notes vol. 04",
    series: "104 pp · soft cover",
    price: 14,
    rating: 4.9,
    reviews: 47,
    swatches: ["#1e293b", "#fef3c7"],
    hueA: "#fbcfe8",
    hueB: "#7c3aed",
  },
];

export function CardProductShowcasePage() {
  return (
    <div className="min-h-svh bg-background px-6 py-12">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6">
          <h1 className="font-heading text-xl">Shop · spring drop</h1>
          <p className="text-muted-foreground text-sm">3 of 24 items</p>
        </header>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {PRODUCTS.map((p) => (
            <ProductCard key={p.slug} product={p} />
          ))}
        </div>
      </div>
    </div>
  );
}

function ProductCard({ product }: { product: Product }) {
  return (
    <Card className="overflow-hidden">
      <div
        className="relative aspect-[4/3] w-full"
        style={{
          background: `radial-gradient(120% 100% at 30% 20%, ${product.hueA}, transparent 60%), radial-gradient(120% 100% at 80% 80%, ${product.hueB}, transparent 60%), color-mix(in srgb, var(--muted) 80%, transparent)`,
        }}
      >
        {product.tag ? (
          <Badge
            variant="secondary"
            className="absolute top-3 left-3 font-mono text-[10px] uppercase tracking-wider"
          >
            {product.tag}
          </Badge>
        ) : null}
        <Button
          size="icon-sm"
          variant="outline"
          className="absolute top-2 right-2 rounded-full bg-background/80 backdrop-blur"
        >
          <HeartIcon />
        </Button>
        <div className="absolute right-3 bottom-3 flex items-center gap-1">
          {product.swatches.map((c) => (
            <span
              key={c}
              className="size-3.5 rounded-full border border-foreground/10 shadow-sm"
              style={{ background: c }}
            />
          ))}
        </div>
      </div>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="font-heading text-base">
            {product.name}
          </CardTitle>
          <div className="flex items-center gap-1 text-muted-foreground text-xs">
            <StarIcon className="size-3 fill-current text-foreground/70" />
            {product.rating}{" "}
            <span className="text-muted-foreground/70">
              ({product.reviews})
            </span>
          </div>
        </div>
        <p className="text-muted-foreground text-sm">{product.series}</p>
      </CardHeader>
      <CardPanel className="pt-0">
        <div className="flex items-baseline gap-2">
          <div className="font-heading font-semibold text-lg">
            ${product.price}
          </div>
          {product.was ? (
            <div className="text-muted-foreground text-sm line-through">
              ${product.was}
            </div>
          ) : null}
        </div>
      </CardPanel>
      <CardFooter className="border-t">
        <Button className="w-full">
          <ShoppingBagIcon />
          Add to bag
        </Button>
      </CardFooter>
    </Card>
  );
}
