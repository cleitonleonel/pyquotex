"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api } from "@/lib/api";

interface Health {
  status: string;
  version: string;
  live_trading_enabled: boolean;
}

function HealthCard() {
  const { data, error, isLoading } = useQuery<Health>({
    queryKey: ["health"],
    queryFn: () => api<Health>("/health"),
    refetchInterval: 15_000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>API status</CardTitle>
        <CardDescription>Backend health and version.</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {error && (
          <p className="text-sm text-destructive">{String(error)}</p>
        )}
        {data && (
          <dl className="space-y-2 text-sm">
            <Row label="Status" value={data.status} />
            <Row label="Version" value={data.version} />
            <Row
              label="Live trading"
              value={data.live_trading_enabled ? "enabled" : "disabled"}
            />
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}

function PhaseCard({
  title,
  phase,
  description,
}: {
  title: string;
  phase: string;
  description: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{phase}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Dashboard</h2>
        <p className="text-sm text-muted-foreground">
          Phase 0 scaffold — feature panels arrive in subsequent phases.
        </p>
      </section>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        <HealthCard />
        <PhaseCard
          title="Broker"
          phase="Phase 1"
          description="Quotex login, balance, demo/real toggle."
        />
        <PhaseCard
          title="Telegram"
          phase="Phase 2"
          description="Pyrogram login, channel browser, watch list."
        />
        <PhaseCard
          title="Parsers"
          phase="Phase 3"
          description="Templates, regex, multi-message aggregator."
        />
        <PhaseCard
          title="Pipeline"
          phase="Phase 4"
          description="Signal → risk gate → live or scheduled trade."
        />
        <PhaseCard
          title="Risk"
          phase="Phase 5"
          description="Limits, position sizing, kill switch."
        />
      </div>
    </div>
  );
}
