"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  type AccountMode,
  type BrokerStatus,
  broker,
} from "@/lib/api";

export default function BrokerPage() {
  const qc = useQueryClient();

  const status = useQuery<BrokerStatus>({
    queryKey: ["broker", "status"],
    queryFn: broker.status,
    // Poll faster during transient states so the UI reacts quickly to
    // OTP prompts and connect completion.
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      return s === "connecting" || s === "awaiting_otp" ? 750 : 5_000;
    },
  });

  const balance = useQuery({
    queryKey: ["broker", "balance"],
    queryFn: broker.balance,
    enabled: status.data?.connected ?? false,
    refetchInterval: 10_000,
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["broker"] });

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Broker</h2>
        <p className="text-sm text-muted-foreground">
          Connect to Quotex. Credentials are encrypted at rest with the
          server&rsquo;s Fernet key — they never leave the box.
        </p>
      </section>

      {status.data?.state === "awaiting_otp" && (
        <OtpCard status={status.data} onChanged={refresh} />
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <StatusCard status={status.data} loading={status.isLoading} />
        <BalanceCard
          balance={balance.data?.balance}
          loading={balance.isLoading}
          enabled={status.data?.connected ?? false}
        />
      </div>

      <CredentialsCard existing={status.data} onSaved={refresh} />

      <ActionsCard status={status.data} onChanged={refresh} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// OTP / 2FA prompt
// ---------------------------------------------------------------------------

function OtpCard({
  status,
  onChanged,
}: {
  status: BrokerStatus;
  onChanged: () => void;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Reset the input whenever the prompt itself changes (e.g. broker
  // re-challenged after a wrong code).
  useEffect(() => {
    setCode("");
    setError(null);
  }, [status.otp_prompt]);

  const submit = useMutation({
    mutationFn: () => broker.submitOtp(code),
    onSuccess: () => {
      setError(null);
      setCode("");
      onChanged();
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : String(err));
    },
  });

  const cancel = useMutation({
    mutationFn: () => broker.cancelConnect(),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  return (
    <Card className="border-amber-500/50">
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Badge variant="warning">2FA</Badge>
              <span>Verification required</span>
            </CardTitle>
            <CardDescription>
              {status.otp_prompt ?? "Enter the code the broker sent you."}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (code) submit.mutate();
          }}
          className="space-y-4"
        >
          <div className="space-y-2">
            <Label htmlFor="otp">Code</Label>
            <Input
              id="otp"
              autoFocus
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]*"
              maxLength={12}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, ""))}
              placeholder="123456"
              className="font-mono tracking-widest"
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <div className="flex flex-wrap gap-2">
            <Button
              type="submit"
              disabled={submit.isPending || !code}
            >
              {submit.isPending ? "Verifying…" : "Submit code"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
            >
              {cancel.isPending ? "Cancelling…" : "Cancel"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Status / Balance
// ---------------------------------------------------------------------------

function StatusCard({
  status,
  loading,
}: {
  status?: BrokerStatus;
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Status</CardTitle>
        <CardDescription>Live connection state.</CardDescription>
      </CardHeader>
      <CardContent>
        {loading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {status && (
          <dl className="space-y-3 text-sm">
            <Row
              label="Connection"
              value={<StateBadge state={status.state} />}
            />
            <Row
              label="Email"
              value={status.email_masked ?? "— not configured —"}
            />
            <Row
              label="Account mode"
              value={
                <Badge
                  variant={
                    status.account_mode === "REAL" ? "warning" : "outline"
                  }
                >
                  {status.account_mode}
                </Badge>
              }
            />
            {status.last_error && status.state !== "awaiting_otp" && (
              <Row
                label="Last error"
                value={
                  <span className="text-destructive">
                    {status.last_error}
                  </span>
                }
              />
            )}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function StateBadge({ state }: { state: BrokerStatus["state"] }) {
  switch (state) {
    case "connected":
      return <Badge variant="success">connected</Badge>;
    case "connecting":
      return <Badge variant="secondary">connecting…</Badge>;
    case "awaiting_otp":
      return <Badge variant="warning">awaiting OTP</Badge>;
    case "error":
      return <Badge variant="destructive">error</Badge>;
    case "idle":
    default:
      return <Badge variant="secondary">idle</Badge>;
  }
}

function BalanceCard({
  balance,
  loading,
  enabled,
}: {
  balance?: number;
  loading: boolean;
  enabled: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Balance</CardTitle>
        <CardDescription>Refreshes every 10s while connected.</CardDescription>
      </CardHeader>
      <CardContent>
        {!enabled && (
          <p className="text-sm text-muted-foreground">
            Connect the broker to see balance.
          </p>
        )}
        {enabled && loading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {enabled && balance !== undefined && (
          <p className="font-mono text-3xl font-semibold tracking-tight">
            {balance.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------

function CredentialsCard({
  existing,
  onSaved,
}: {
  existing?: BrokerStatus;
  onSaved: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<AccountMode>("PRACTICE");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: () => broker.putCredentials(email, password, mode),
    onSuccess: () => {
      setError(null);
      setSaved(true);
      setEmail("");
      setPassword("");
      onSaved();
      window.setTimeout(() => setSaved(false), 3000);
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : String(err));
    },
  });

  const wipe = useMutation({
    mutationFn: () => broker.deleteCredentials(),
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : String(err));
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Credentials</CardTitle>
        <CardDescription>
          {existing?.configured
            ? `Configured (${existing.email_masked}). Re-submit to replace.`
            : "Add your Quotex login. Stored encrypted (Fernet)."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
          className="space-y-4"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="username"
                placeholder="trader@example.com"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label>Account mode</Label>
            <div className="flex gap-2">
              <ModePill
                active={mode === "PRACTICE"}
                onClick={() => setMode("PRACTICE")}
              >
                Practice
              </ModePill>
              <ModePill
                active={mode === "REAL"}
                onClick={() => setMode("REAL")}
                variant="warning"
              >
                Real
              </ModePill>
            </div>
            {mode === "REAL" && (
              <p className="text-xs text-amber-400">
                Real-money mode is gated by{" "}
                <code>AUTOTRADER_LIVE_TRADING_ENABLED</code>. The server
                will reject this if the env flag is off.
              </p>
            )}
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
          {saved && (
            <p className="text-sm text-emerald-400">Credentials saved.</p>
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              type="submit"
              disabled={save.isPending || !email || !password}
            >
              {save.isPending ? "Saving…" : "Save credentials"}
            </Button>
            {existing?.configured && (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  if (confirm("Remove stored broker credentials?")) {
                    wipe.mutate();
                  }
                }}
                disabled={wipe.isPending}
              >
                Forget credentials
              </Button>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function ModePill({
  active,
  onClick,
  children,
  variant = "default",
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  variant?: "default" | "warning";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors " +
        (active
          ? variant === "warning"
            ? "border-amber-500 bg-amber-500/15 text-amber-300"
            : "border-primary bg-primary/10 text-primary-foreground"
          : "border-input text-muted-foreground hover:text-foreground")
      }
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

function ActionsCard({
  status,
  onChanged,
}: {
  status?: BrokerStatus;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const connect = useMutation({
    mutationFn: () => broker.connect(),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  const disconnect = useMutation({
    mutationFn: () => broker.disconnect(),
    onSuccess: onChanged,
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  const cancel = useMutation({
    mutationFn: () => broker.cancelConnect(),
    onSuccess: onChanged,
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  const switchMode = useMutation({
    mutationFn: (m: AccountMode) => broker.setAccountMode(m),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  if (!status?.configured) return null;

  const otherMode: AccountMode =
    status.account_mode === "REAL" ? "PRACTICE" : "REAL";
  const inFlight = status.state === "connecting" || status.state === "awaiting_otp";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Actions</CardTitle>
        <CardDescription>
          Connect, disconnect, or hot-swap account modes.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex flex-wrap gap-2">
          {status.connected ? (
            <Button
              variant="outline"
              onClick={() => disconnect.mutate()}
              disabled={disconnect.isPending}
            >
              {disconnect.isPending ? "Disconnecting…" : "Disconnect"}
            </Button>
          ) : inFlight ? (
            <Button
              variant="outline"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
            >
              {cancel.isPending ? "Cancelling…" : "Cancel connect"}
            </Button>
          ) : (
            <Button
              onClick={() => connect.mutate()}
              disabled={connect.isPending}
            >
              {connect.isPending ? "Connecting…" : "Connect"}
            </Button>
          )}

          <Button
            variant="outline"
            onClick={() => {
              if (
                otherMode === "REAL" &&
                !confirm(
                  "Switch to REAL-money trading? Trades will use real funds.",
                )
              ) {
                return;
              }
              switchMode.mutate(otherMode);
            }}
            disabled={switchMode.isPending || !status.connected}
          >
            Switch to {otherMode}
          </Button>
        </div>
        {!status.connected && status.account_mode === "REAL" && (
          <p className="text-xs text-amber-400">
            Account mode is REAL but disconnected — connect to apply.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function Row({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
