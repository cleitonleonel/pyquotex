"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  type BrokerStatus,
  type TelegramStatus,
  api,
  broker,
  telegram,
} from "@/lib/api";

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
        <CardTitle>API</CardTitle>
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
              value={
                <Badge
                  variant={data.live_trading_enabled ? "warning" : "outline"}
                >
                  {data.live_trading_enabled ? "ENABLED" : "disabled"}
                </Badge>
              }
            />
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function BrokerSummaryCard() {
  const { data, isLoading } = useQuery<BrokerStatus>({
    queryKey: ["broker", "status"],
    queryFn: broker.status,
    refetchInterval: 10_000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Broker</CardTitle>
        <CardDescription>
          {data?.configured ? "Quotex credentials configured." : "Not configured."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {data && (
          <dl className="space-y-2 text-sm">
            <Row
              label="Connection"
              value={
                <Badge variant={data.connected ? "success" : "secondary"}>
                  {data.connected ? "connected" : "disconnected"}
                </Badge>
              }
            />
            <Row
              label="Account"
              value={
                <Badge
                  variant={
                    data.account_mode === "REAL" ? "warning" : "outline"
                  }
                >
                  {data.account_mode}
                </Badge>
              }
            />
            {data.email_masked && (
              <Row label="Email" value={data.email_masked} />
            )}
          </dl>
        )}
        <Link
          href="/dashboard/broker"
          className="inline-flex h-8 items-center justify-center rounded-md border border-input bg-background px-3 text-xs font-medium shadow-sm hover:bg-accent hover:text-accent-foreground"
        >
          Manage
        </Link>
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}

function TelegramSummaryCard() {
  const { data, isLoading } = useQuery<TelegramStatus>({
    queryKey: ["telegram", "status"],
    queryFn: telegram.status,
    refetchInterval: 15_000,
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Telegram</CardTitle>
        <CardDescription>
          {data?.logged_in
            ? `Connected as ${data.first_name ?? "user"}.`
            : "Not connected."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {data && (
          <dl className="space-y-2 text-sm">
            <Row
              label="Login state"
              value={
                <Badge
                  variant={data.logged_in ? "success" : "secondary"}
                >
                  {data.state}
                </Badge>
              }
            />
            {data.phone_masked && (
              <Row label="Phone" value={data.phone_masked} />
            )}
            {data.username && (
              <Row label="Username" value={`@${data.username}`} />
            )}
          </dl>
        )}
        <Link
          href="/dashboard/telegram"
          className="inline-flex h-8 items-center justify-center rounded-md border border-input bg-background px-3 text-xs font-medium shadow-sm hover:bg-accent hover:text-accent-foreground"
        >
          Manage
        </Link>
      </CardContent>
    </Card>
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
          Phase 2 — broker + Telegram online. Parsers, pipeline, and
          risk land in subsequent phases.
        </p>
      </section>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        <HealthCard />
        <BrokerSummaryCard />
        <TelegramSummaryCard />
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
