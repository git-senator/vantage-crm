import type { Metadata } from "next";
import {
  Bath,
  Bed,
  Building2,
  CalendarClock,
  Eye,
  Heart,
  Map,
  MoreHorizontal,
  Plus,
  Ruler,
  Tag,
} from "lucide-react";

import { DataToolbar } from "@/components/shared/data-toolbar";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatCurrency, formatNumber } from "@/lib/format";
import { properties } from "@/lib/mock-data";
import type { Property } from "@/types";

export const metadata: Metadata = { title: "Properties" };

const stats: Stat[] = [
  { label: "Active listings", value: "24", delta: 9.1, hint: "across 4 markets", icon: Building2 },
  { label: "Total list value", value: "$62.4M", delta: 11.7, hint: "active inventory", icon: Tag },
  { label: "Avg. days on market", value: "27", delta: -12.3, hint: "vs. 31 last quarter", invertDelta: true, icon: CalendarClock },
  { label: "Listing views", value: "24.8K", delta: 31.4, hint: "last 30 days", icon: Eye },
];

/**
 * Listings have no photography in the prototype, so each card gets a generated
 * gradient keyed to the property's hue. Distinct, and no asset pipeline.
 */
function PropertyThumb({ property }: { property: Property }) {
  return (
    <div
      className="relative aspect-[16/10] overflow-hidden"
      style={{
        backgroundImage: `linear-gradient(145deg, oklch(0.78 0.11 ${property.hue}), oklch(0.55 0.14 ${property.hue + 25}))`,
      }}
    >
      <div
        aria-hidden
        className="absolute inset-0 opacity-20"
        style={{
          backgroundImage:
            "linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)",
          backgroundSize: "32px 32px",
        }}
      />
      {/* Roofline motif echoes the brand glyph. */}
      <svg
        aria-hidden
        viewBox="0 0 100 60"
        className="absolute right-4 bottom-0 h-20 w-32 text-white/25"
        fill="currentColor"
      >
        <path d="M10 60V28L34 10l24 18v32H10Z" />
        <path d="M62 60V36l18-12 14 10v26H62Z" opacity="0.7" />
      </svg>

      <div className="absolute top-3 left-3 flex gap-1.5">
        <StatusBadge
          status={property.status}
          className="bg-white/90 text-neutral-900 backdrop-blur-sm dark:bg-neutral-900/85 dark:text-white"
        />
      </div>
      <div className="absolute top-3 right-3">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Save listing"
          className="bg-white/90 backdrop-blur-sm hover:bg-white dark:bg-neutral-900/85"
        >
          <Heart className="size-4" />
        </Button>
      </div>
      <p className="absolute bottom-3 left-3 text-lg font-semibold text-white drop-shadow-sm">
        {formatCurrency(property.price)}
      </p>
    </div>
  );
}

export default function PropertiesPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Properties"
        description="Your brokerage's inventory — listed, pending and off-market."
        actions={
          <>
            <Button variant="outline">
              <Map className="size-4" />
              Map view
            </Button>
            <Button>
              <Plus className="size-4" />
              New listing
            </Button>
          </>
        }
      />

      <StatGrid stats={stats} />

      <Tabs defaultValue="all">
        <TabsList>
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="active">Active</TabsTrigger>
          <TabsTrigger value="pending">Pending</TabsTrigger>
          <TabsTrigger value="sold">Sold</TabsTrigger>
          <TabsTrigger value="off-market">Off-market</TabsTrigger>
        </TabsList>
      </Tabs>

      <DataToolbar
        placeholder="Search by address, MLS ID or neighbourhood…"
        filters={
          <>
            <Select defaultValue="Any type">
              <SelectTrigger size="sm" className="w-[150px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Any type">Any type</SelectItem>
                <SelectItem value="Single Family">Single Family</SelectItem>
                <SelectItem value="Condo">Condo</SelectItem>
                <SelectItem value="Multi-Family">Multi-Family</SelectItem>
                <SelectItem value="Commercial">Commercial</SelectItem>
              </SelectContent>
            </Select>
            <Select defaultValue="Any price">
              <SelectTrigger size="sm" className="w-[150px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Any price">Any price</SelectItem>
                <SelectItem value="Under $1M">Under $1M</SelectItem>
                <SelectItem value="$1M – $2M">$1M – $2M</SelectItem>
                <SelectItem value="Over $2M">Over $2M</SelectItem>
              </SelectContent>
            </Select>
          </>
        }
      />

      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {properties.map((property) => (
          <Card
            key={property.id}
            className="gap-0 overflow-hidden py-0 transition-shadow hover:shadow-lg"
          >
            <PropertyThumb property={property} />

            <CardContent className="p-4">
              <p className="truncate font-medium">{property.title}</p>
              <p className="mt-0.5 truncate text-sm text-muted-foreground">
                {property.address}, {property.city} {property.state}{" "}
                {property.zip}
              </p>

              <div className="tabular mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-muted-foreground">
                {property.beds > 0 && (
                  <span className="flex items-center gap-1.5">
                    <Bed className="size-4" />
                    {property.beds}
                  </span>
                )}
                {property.baths > 0 && (
                  <span className="flex items-center gap-1.5">
                    <Bath className="size-4" />
                    {property.baths}
                  </span>
                )}
                <span className="flex items-center gap-1.5">
                  <Ruler className="size-4" />
                  {property.sqft > 0
                    ? `${formatNumber(property.sqft)} sqft`
                    : property.lotSize}
                </span>
              </div>

              <div className="mt-3 flex items-center gap-1.5">
                <StatusBadge
                  status={property.type}
                  label={property.type}
                  tone="neutral"
                  dot={false}
                />
                {property.daysOnMarket > 0 && (
                  <span className="text-xs text-muted-foreground">
                    {property.daysOnMarket} days on market
                  </span>
                )}
              </div>
            </CardContent>

            <Separator />

            <CardFooter className="flex items-center justify-between gap-2 px-4 py-3">
              <div className="flex min-w-0 items-center gap-2">
                <UserAvatar user={property.agent} size="xs" />
                <span className="truncate text-xs text-muted-foreground">
                  {property.agent.name.split(" ")[0]}
                </span>
              </div>
              <div className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
                <span className="tabular flex items-center gap-1">
                  <Eye className="size-3.5" />
                  {formatNumber(property.views)}
                </span>
                <span className="tabular flex items-center gap-1">
                  <Heart className="size-3.5" />
                  {property.saves}
                </span>
                <DropdownMenu>
                  <DropdownMenuTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Actions for ${property.address}`}
                      >
                        <MoreHorizontal className="size-4" />
                      </Button>
                    }
                  />
                  <DropdownMenuContent align="end" className="w-44">
                    <DropdownMenuItem>Open listing</DropdownMenuItem>
                    <DropdownMenuItem>Schedule showing</DropdownMenuItem>
                    <DropdownMenuItem>Share to portal</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive">
                      Take off market
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </CardFooter>
          </Card>
        ))}
      </div>
    </div>
  );
}
