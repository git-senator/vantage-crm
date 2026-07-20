import type { Metadata } from "next";
import {
  Award,
  Building2,
  CircleDollarSign,
  Handshake,
  Mail,
  MapPin,
  Pencil,
  Phone,
  Share2,
  Star,
} from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { activity, currentUser } from "@/lib/mock-data";

export const metadata: Metadata = { title: "Profile" };

const productionStats = [
  { label: "Closed volume", value: "$12.4M", icon: CircleDollarSign },
  { label: "Deals closed", value: "9", icon: Handshake },
  { label: "Active listings", value: "6", icon: Building2 },
  { label: "Client rating", value: "4.9", icon: Star },
];

const specialties = [
  "Luxury residential",
  "Multi-family",
  "1031 exchanges",
  "Noe Valley",
  "Pacific Heights",
  "First-time buyers",
];

const certifications = [
  { name: "CA DRE License #01998432", detail: "Active through Mar 2028" },
  { name: "Certified Residential Specialist (CRS)", detail: "Since 2019" },
  { name: "Accredited Buyer's Representative (ABR)", detail: "Since 2017" },
  { name: "GREEN Designation", detail: "Since 2022" },
];

const goals = [
  { label: "Annual volume", current: 12.4, target: 20, unit: "M" },
  { label: "Closings", current: 9, target: 16, unit: "" },
  { label: "New clients", current: 23, target: 30, unit: "" },
];

export default function ProfilePage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Profile"
        description="How you appear to teammates and on shared listing pages."
        actions={
          <>
            <Button variant="outline">
              <Share2 className="size-4" />
              Share profile
            </Button>
            <Button>
              <Pencil className="size-4" />
              Edit profile
            </Button>
          </>
        }
      />

      {/* ---------------------------------------------------------- hero */}
      <Card className="gap-0 overflow-hidden py-0">
        <div
          className="h-32"
          style={{
            backgroundImage:
              "linear-gradient(120deg, oklch(0.55 0.19 268), oklch(0.62 0.15 210))",
          }}
        />
        <CardContent className="p-6 pt-0">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
            <UserAvatar
              user={currentUser}
              size="xl"
              className="-mt-10 ring-4 ring-card"
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-xl font-semibold">{currentUser.name}</h2>
                <StatusBadge status="active" label="Active" />
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                {currentUser.role} · Vantage Realty Group
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <Mail className="size-3.5" />
                  avery.chen@vantagerealty.com
                </span>
                <span className="flex items-center gap-1.5">
                  <Phone className="size-3.5" />
                  (415) 555-0100
                </span>
                <span className="flex items-center gap-1.5">
                  <MapPin className="size-3.5" />
                  San Francisco, CA
                </span>
              </div>
            </div>
          </div>

          <Separator className="my-6" />

          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {productionStats.map((stat) => (
              <div key={stat.label} className="flex items-center gap-3">
                <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
                  <stat.icon className="size-4" />
                </span>
                <div className="min-w-0">
                  <p className="tabular text-lg font-semibold">{stat.value}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {stat.label}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="listings">Listings</TabsTrigger>
          <TabsTrigger value="reviews">Reviews</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>
      </Tabs>

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Public bio</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="headline">Headline</Label>
                <Input
                  id="headline"
                  defaultValue="Managing Broker · Bay Area residential & multi-family"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="bio">About</Label>
                <Textarea
                  id="bio"
                  rows={5}
                  defaultValue="Fourteen years representing buyers and sellers across San Francisco and the East Bay, with a focus on multi-family acquisitions and 1031 exchanges. I lead a team of eleven agents and still take on a small book of clients personally — usually the complicated ones."
                />
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="ghost">Cancel</Button>
                <Button>Save changes</Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Specialties</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {specialties.map((item) => (
                  <span
                    key={item}
                    className="rounded-full border px-2.5 py-1 text-sm text-muted-foreground"
                  >
                    {item}
                  </span>
                ))}
                <button className="rounded-full border border-dashed px-2.5 py-1 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
                  + Add
                </button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Recent activity</CardTitle>
            </CardHeader>
            <CardContent>
              <ol className="space-y-4">
                {activity.map((item) => (
                  <li key={item.id} className="flex gap-3">
                    <UserAvatar user={item.actor} size="xs" className="mt-0.5" />
                    <div className="min-w-0 flex-1 text-sm">
                      <p className="leading-snug">
                        <span className="font-medium">{item.actor.name}</span>{" "}
                        <span className="text-muted-foreground">
                          {item.action}
                        </span>{" "}
                        <span className="font-medium">{item.target}</span>
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {item.timestamp}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">2026 goals</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {goals.map((goal) => (
                <div key={goal.label}>
                  <div className="mb-1.5 flex items-baseline justify-between text-sm">
                    <span>{goal.label}</span>
                    <span className="tabular text-muted-foreground">
                      {goal.current}
                      {goal.unit} / {goal.target}
                      {goal.unit}
                    </span>
                  </div>
                  <Progress
                    value={(goal.current / goal.target) * 100}
                    className="h-2"
                  />
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Licenses & certifications</CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              {certifications.map((cert, index) => (
                <div
                  key={cert.name}
                  className={`flex gap-3 py-3 ${index > 0 ? "border-t" : ""}`}
                >
                  <Award className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{cert.name}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {cert.detail}
                    </p>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
