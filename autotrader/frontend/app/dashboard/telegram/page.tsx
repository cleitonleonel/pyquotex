"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useMemo, useState } from "react";
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
  type TelegramDialog,
  type TelegramStatus,
  telegram,
} from "@/lib/api";

export default function TelegramPage() {
  const qc = useQueryClient();
  const status = useQuery<TelegramStatus>({
    queryKey: ["telegram", "status"],
    queryFn: telegram.status,
    refetchInterval: 10_000,
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["telegram"] });

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Telegram</h2>
        <p className="text-sm text-muted-foreground">
          Log in with your phone (Telegram MTProto via Pyrogram). The
          encrypted session is stored locally so subsequent restarts
          skip the SMS round-trip.
        </p>
      </section>

      {!status.data ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">Loading…</p>
          </CardContent>
        </Card>
      ) : status.data.logged_in ? (
        <LoggedInPanel status={status.data} onChanged={refresh} />
      ) : (
        <LoginFlow status={status.data} onChanged={refresh} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Login flow
// ---------------------------------------------------------------------------

function LoginFlow({
  status,
  onChanged,
}: {
  status: TelegramStatus;
  onChanged: () => void;
}) {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const sendCode = useMutation({
    mutationFn: () => telegram.login(phone),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  const submitCode = useMutation({
    mutationFn: () => telegram.submitCode(code),
    onSuccess: () => {
      setError(null);
      setCode("");
      onChanged();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  const submitPassword = useMutation({
    mutationFn: () => telegram.submitPassword(password),
    onSuccess: () => {
      setError(null);
      setPassword("");
      onChanged();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  const cancel = useMutation({
    mutationFn: () => telegram.cancel(),
    onSuccess: () => {
      setError(null);
      setPhone("");
      setCode("");
      setPassword("");
      onChanged();
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <span>Sign in</span>
          <StateBadge state={status.state} />
        </CardTitle>
        <CardDescription>
          {status.state === "idle" &&
            "Enter the phone number registered with your Telegram account."}
          {status.state === "awaiting_code" &&
            `Telegram sent a code to ${status.phone_masked}. Enter it below.`}
          {status.state === "awaiting_password" &&
            "Two-factor authentication is on — enter your cloud password."}
          {status.state === "error" &&
            (status.last_error ?? "Something went wrong. Try again.")}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {status.state === "idle" || status.state === "error" ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (phone) sendCode.mutate();
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label htmlFor="phone">Phone (with country code)</Label>
              <Input
                id="phone"
                inputMode="tel"
                autoFocus
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+15551234567"
                autoComplete="tel"
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" disabled={sendCode.isPending || !phone}>
              {sendCode.isPending ? "Sending code…" : "Send code"}
            </Button>
          </form>
        ) : status.state === "awaiting_code" ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (code) submitCode.mutate();
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label htmlFor="tg-code">Code</Label>
              <Input
                id="tg-code"
                autoFocus
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={12}
                value={code}
                onChange={(e) =>
                  setCode(e.target.value.replace(/[^0-9]/g, ""))
                }
                placeholder="12345"
                className="font-mono tracking-widest"
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={submitCode.isPending || !code}>
                {submitCode.isPending ? "Verifying…" : "Verify"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => cancel.mutate()}
                disabled={cancel.isPending}
              >
                Cancel
              </Button>
            </div>
          </form>
        ) : status.state === "awaiting_password" ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (password) submitPassword.mutate();
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label htmlFor="tg-password">Cloud password</Label>
              <Input
                id="tg-password"
                type="password"
                autoFocus
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div className="flex flex-wrap gap-2">
              <Button
                type="submit"
                disabled={submitPassword.isPending || !password}
              >
                {submitPassword.isPending ? "Verifying…" : "Submit"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => cancel.mutate()}
                disabled={cancel.isPending}
              >
                Cancel
              </Button>
            </div>
          </form>
        ) : null}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Logged-in: identity + dialog browser
// ---------------------------------------------------------------------------

function LoggedInPanel({
  status,
  onChanged,
}: {
  status: TelegramStatus;
  onChanged: () => void;
}) {
  const [query, setQuery] = useState("");

  const dialogs = useQuery<TelegramDialog[]>({
    queryKey: ["telegram", "dialogs"],
    queryFn: () => telegram.dialogs(undefined, 200),
    staleTime: 60_000,
  });

  const filtered = useMemo(() => {
    const list = dialogs.data ?? [];
    if (!query.trim()) return list;
    const needle = query.trim().toLowerCase();
    return list.filter(
      (d) =>
        d.title.toLowerCase().includes(needle) ||
        (d.username ?? "").toLowerCase().includes(needle),
    );
  }, [dialogs.data, query]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2">
                <span>Logged in</span>
                <StateBadge state={status.state} />
              </CardTitle>
              <CardDescription>
                {status.first_name ?? "Telegram user"}
                {status.username ? ` · @${status.username}` : ""}
                {status.phone_masked ? ` · ${status.phone_masked}` : ""}
              </CardDescription>
            </div>
            <LogoutButton onChanged={onChanged} />
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Channels &amp; groups</CardTitle>
          <CardDescription>
            Pick the chats whose messages should feed the parser. Your
            choices persist; only watched chats are dispatched in the
            live pipeline (Phase 3+).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name or @username"
              className="max-w-sm"
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => dialogs.refetch()}
              disabled={dialogs.isFetching}
            >
              {dialogs.isFetching ? "Refreshing…" : "Refresh"}
            </Button>
          </div>

          {dialogs.isLoading && (
            <p className="text-sm text-muted-foreground">Loading dialogs…</p>
          )}
          {dialogs.error && (
            <p className="text-sm text-destructive">
              {String(dialogs.error)}
            </p>
          )}
          {dialogs.data && filtered.length === 0 && (
            <p className="text-sm text-muted-foreground">No matches.</p>
          )}

          <ul className="divide-y rounded-md border">
            {filtered.map((d) => (
              <DialogRow key={d.chat_id} dialog={d} />
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function DialogRow({ dialog }: { dialog: TelegramDialog }) {
  const qc = useQueryClient();
  const watch = useMutation({
    mutationFn: (enabled: boolean) =>
      enabled
        ? telegram.watch({
            chat_id: dialog.chat_id,
            title: dialog.title,
            chat_type: dialog.chat_type,
            username: dialog.username,
            enabled: true,
          })
        : telegram.unwatch(dialog.chat_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["telegram"] }),
  });

  return (
    <li className="flex items-center justify-between gap-4 p-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{dialog.title}</span>
          <ChatTypeBadge type={dialog.chat_type} />
          {dialog.is_verified && <Badge variant="success">verified</Badge>}
        </div>
        <p className="truncate text-xs text-muted-foreground">
          {dialog.username ? `@${dialog.username}` : `chat ${dialog.chat_id}`}
          {dialog.members_count
            ? ` · ${dialog.members_count.toLocaleString()} members`
            : ""}
        </p>
      </div>
      <Button
        size="sm"
        variant={dialog.watched ? "secondary" : "outline"}
        onClick={() => watch.mutate(!dialog.watched)}
        disabled={watch.isPending}
      >
        {watch.isPending
          ? "…"
          : dialog.watched
            ? "Watching"
            : "Watch"}
      </Button>
    </li>
  );
}

function ChatTypeBadge({ type }: { type: string }) {
  const variant: "default" | "secondary" | "outline" = type.includes("channel")
    ? "default"
    : type.includes("group")
      ? "secondary"
      : "outline";
  return <Badge variant={variant}>{type}</Badge>;
}

function LogoutButton({ onChanged }: { onChanged: () => void }) {
  const logout = useMutation({
    mutationFn: () => telegram.logout(),
    onSuccess: onChanged,
  });
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => {
        if (confirm("Log out of Telegram and forget the saved session?")) {
          logout.mutate();
        }
      }}
      disabled={logout.isPending}
    >
      {logout.isPending ? "Logging out…" : "Log out"}
    </Button>
  );
}

// ---------------------------------------------------------------------------
// State badge
// ---------------------------------------------------------------------------

function StateBadge({ state }: { state: TelegramStatus["state"] }) {
  switch (state) {
    case "logged_in":
      return <Badge variant="success">connected</Badge>;
    case "awaiting_code":
      return <Badge variant="warning">awaiting code</Badge>;
    case "awaiting_password":
      return <Badge variant="warning">awaiting 2FA</Badge>;
    case "error":
      return <Badge variant="destructive">error</Badge>;
    case "idle":
    default:
      return <Badge variant="secondary">idle</Badge>;
  }
}
