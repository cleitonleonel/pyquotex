"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
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
  DEFAULT_PARSER_CONFIG,
  type BrokerAssets,
  type ParserConfigPayload,
  type ParserTemplate,
  type ParserTestResponse,
  type TelegramDialog,
  type TelegramMessage,
  type TradeMode,
  broker,
  parsers,
  telegram,
} from "@/lib/api";

export default function ParserEditor() {
  const router = useRouter();
  const params = useParams<{ chat_id: string; config_id: string }>();
  const chatId = Number(params.chat_id);
  const isNew = params.config_id === "new";
  const configId = isNew ? null : Number(params.config_id);
  const qc = useQueryClient();

  const watched = useQuery<TelegramDialog[]>({
    queryKey: ["telegram", "watched"],
    queryFn: telegram.watched,
  });
  const channel = watched.data?.find((d) => d.chat_id === chatId);

  const existing = useQuery({
    queryKey: ["parsers", "config", configId],
    queryFn: () => parsers.get(configId as number),
    enabled: !isNew && configId !== null,
    retry: false,
  });

  const [cfg, setCfg] = useState<ParserConfigPayload>(DEFAULT_PARSER_CONFIG);
  const [aliasesText, setAliasesText] = useState<string>("");
  const [aliasesError, setAliasesError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(isNew);
  const [testerText, setTesterText] = useState<string>("BUY EURUSD 1m");

  useEffect(() => {
    if (!isNew && existing.data && !hydrated) {
      const data = existing.data;
      setCfg({
        name: data.name,
        priority: data.priority,
        parser_type: data.parser_type,
        parser_config: data.parser_config,
        timezone: data.timezone,
        timezone_offset_minutes: data.timezone_offset_minutes,
        asset_aliases: data.asset_aliases,
        aggregate_window_seconds: data.aggregate_window_seconds,
        default_stake: data.default_stake,
        default_duration_seconds: data.default_duration_seconds,
        trade_mode: data.trade_mode,
        martingale: data.martingale,
        enabled: data.enabled,
      });
      setAliasesText(
        Object.entries(data.asset_aliases)
          .map(([k, v]) => `${k} = ${v}`)
          .join("\n"),
      );
      setHydrated(true);
    }
  }, [existing.data, hydrated, isNew]);

  const save = useMutation({
    mutationFn: () =>
      isNew
        ? parsers.create(chatId, cfg)
        : parsers.update(configId as number, cfg),
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["parsers"] });
      if (isNew) {
        router.replace(`/dashboard/parsers/${chatId}/${saved.id}`);
      }
    },
  });

  const wipe = useMutation({
    mutationFn: () => parsers.remove(configId as number),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["parsers"] });
      router.push(`/dashboard/parsers/${chatId}`);
    },
  });

  function applyAliases() {
    const next: Record<string, string> = {};
    setAliasesError(null);
    for (const line of aliasesText.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const eq = trimmed.indexOf("=");
      if (eq < 0) {
        setAliasesError(`Bad alias line (need '='): ${trimmed}`);
        return;
      }
      const k = trimmed.slice(0, eq).trim();
      const v = trimmed.slice(eq + 1).trim();
      if (!k || !v) {
        setAliasesError(`Bad alias line: ${trimmed}`);
        return;
      }
      next[k] = v;
    }
    setCfg((c) => ({ ...c, asset_aliases: next }));
  }

  if (!hydrated) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <section>
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <h2 className="truncate text-2xl font-semibold tracking-tight">
              {isNew
                ? `New parser for ${channel?.title ?? `chat ${chatId}`}`
                : cfg.name || `parser #${configId}`}
            </h2>
            <p className="text-sm text-muted-foreground">
              {channel?.title ?? `chat ${chatId}`}
              {channel?.username ? ` · @${channel.username}` : ""}
            </p>
          </div>
          <Link
            href={`/dashboard/parsers/${chatId}`}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← Channel parsers
          </Link>
        </div>
      </section>

      <RecentMessagesPanel
        chatId={chatId}
        onUse={(t) => setTesterText(t)}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <ConfigEditor
          cfg={cfg}
          setCfg={setCfg}
          aliasesText={aliasesText}
          setAliasesText={setAliasesText}
          applyAliases={applyAliases}
          aliasesError={aliasesError}
        />
        <LiveTester
          cfg={cfg}
          text={testerText}
          setText={setTesterText}
        />
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 py-4">
          <Button
            onClick={() => {
              applyAliases();
              save.mutate();
            }}
            disabled={save.isPending}
          >
            {save.isPending
              ? "Saving…"
              : isNew
                ? "Create parser"
                : "Save changes"}
          </Button>
          {save.isSuccess && !isNew && (
            <span className="text-sm text-emerald-400">Saved.</span>
          )}
          {save.isError && (
            <span className="text-sm text-destructive">
              {save.error instanceof ApiError
                ? save.error.message
                : String(save.error)}
            </span>
          )}
          <span className="flex-1" />
          {!isNew && (
            <Button
              variant="outline"
              onClick={() => {
                if (confirm(`Delete parser ${cfg.name || configId}?`)) {
                  wipe.mutate();
                }
              }}
              disabled={wipe.isPending}
            >
              {wipe.isPending ? "Deleting…" : "Delete"}
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Config editor
// ---------------------------------------------------------------------------

function ConfigEditor({
  cfg,
  setCfg,
  aliasesText,
  setAliasesText,
  applyAliases,
  aliasesError,
}: {
  cfg: ParserConfigPayload;
  setCfg: (
    updater: (p: ParserConfigPayload) => ParserConfigPayload,
  ) => void;
  aliasesText: string;
  setAliasesText: (s: string) => void;
  applyAliases: () => void;
  aliasesError: string | null;
}) {
  const templates = useQuery<ParserTemplate[]>({
    queryKey: ["parsers", "templates"],
    queryFn: parsers.templates,
  });

  function set<K extends keyof ParserConfigPayload>(
    key: K,
    value: ParserConfigPayload[K],
  ) {
    setCfg((p) => ({ ...p, [key]: value }));
  }

  function setMartingale<
    K extends keyof ParserConfigPayload["martingale"],
  >(key: K, value: ParserConfigPayload["martingale"][K]) {
    setCfg((p) => ({
      ...p,
      martingale: { ...p.martingale, [key]: value },
    }));
  }

  function setParserConfigField(key: string, value: unknown) {
    setCfg((p) => ({
      ...p,
      parser_config: { ...p.parser_config, [key]: value },
    }));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Configuration</CardTitle>
        <CardDescription>
          Multiple parsers can live on one channel. Lower priority values
          run first; the first match wins.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Name"
            value={cfg.name}
            onChange={(v) => set("name", v)}
            placeholder="e.g. signals-pro main"
          />
          <Field
            label="Priority"
            value={cfg.priority}
            onChange={(v) => set("priority", Math.max(0, Number(v) || 0))}
            type="number"
            help="Lower runs first. 100 is the default."
          />
        </div>

        <div className="space-y-2">
          <Label>Parser type</Label>
          <div className="flex gap-2">
            <Pill
              active={cfg.parser_type === "template"}
              onClick={() => set("parser_type", "template")}
            >
              Template
            </Pill>
            <Pill
              active={cfg.parser_type === "regex"}
              onClick={() => set("parser_type", "regex")}
            >
              Regex
            </Pill>
          </div>
        </div>

        {cfg.parser_type === "template" ? (
          <TemplateEditor
            value={String(cfg.parser_config.template ?? "")}
            onChange={(v) => setParserConfigField("template", v)}
            templates={templates.data ?? []}
          />
        ) : (
          <RegexEditor
            value={String(cfg.parser_config.pattern ?? "")}
            onChange={(v) => setParserConfigField("pattern", v)}
          />
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Default duration (seconds)"
            value={cfg.default_duration_seconds}
            onChange={(v) =>
              set("default_duration_seconds", Math.max(1, Number(v) || 60))
            }
            type="number"
          />
          <Field
            label="Default stake"
            value={cfg.default_stake}
            onChange={(v) =>
              set("default_stake", Math.max(0, Number(v) || 0))
            }
            type="number"
            step="0.01"
            help="Used as base stake for martingale."
          />
          <Field
            label="Timezone offset (minutes)"
            value={cfg.timezone_offset_minutes}
            onChange={(v) => set("timezone_offset_minutes", Number(v) || 0)}
            type="number"
            help="UTC offset (60 = UTC+1, -300 = UTC-5)"
          />
          <MultiMessageToggle
            seconds={cfg.aggregate_window_seconds}
            onChange={(s) =>
              set("aggregate_window_seconds", Math.max(0, s))
            }
          />
        </div>

        <div className="space-y-2">
          <Label>Trade mode</Label>
          <div className="flex flex-wrap gap-2">
            {(["live", "scheduled", "auto"] as TradeMode[]).map((m) => (
              <Pill
                key={m}
                active={cfg.trade_mode === m}
                onClick={() => set("trade_mode", m)}
              >
                {m}
              </Pill>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            <code>live</code> ignores any extracted time and fires immediately.{" "}
            <code>scheduled</code> requires a parsed time (rejects live-only signals).{" "}
            <code>auto</code> uses the time if present, otherwise live.
          </p>
        </div>

        <MartingaleBlock cfg={cfg} setMartingale={setMartingale} />

        <div className="space-y-2">
          <Label>Asset aliases (one per line, <code>raw = broker</code>)</Label>
          <textarea
            className="flex min-h-[6rem] w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            value={aliasesText}
            onChange={(e) => setAliasesText(e.target.value)}
            onBlur={applyAliases}
            placeholder={"EUR/USD = EURUSD_otc\nGOLD = XAUUSD"}
          />
          {aliasesError && (
            <p className="text-xs text-destructive">{aliasesError}</p>
          )}
        </div>

        <div className="flex items-center gap-2">
          <input
            id="enabled"
            type="checkbox"
            checked={cfg.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
            className="h-4 w-4"
          />
          <Label htmlFor="enabled">Enabled</Label>
        </div>
      </CardContent>
    </Card>
  );
}

function MartingaleBlock({
  cfg,
  setMartingale,
}: {
  cfg: ParserConfigPayload;
  setMartingale: <K extends keyof ParserConfigPayload["martingale"]>(
    key: K,
    value: ParserConfigPayload["martingale"][K],
  ) => void;
}) {
  const m = cfg.martingale;
  return (
    <div className="space-y-3 rounded-md border border-amber-500/20 bg-amber-500/5 p-3">
      <div className="flex items-center justify-between gap-2">
        <Label className="flex items-center gap-2">
          Martingale recovery
          {m.enabled && <Badge variant="warning">on</Badge>}
        </Label>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={m.enabled}
            onChange={(e) => setMartingale("enabled", e.target.checked)}
            className="h-4 w-4"
          />
          Enable
        </label>
      </div>

      <p className="text-xs text-muted-foreground">
        On a loss, multiply the next stake by the multiplier; reset on a
        win (or after <code>max_streak</code> consecutive losses).
      </p>

      <div className="grid gap-3 sm:grid-cols-3">
        <Field
          label="Multiplier"
          value={m.multiplier}
          onChange={(v) =>
            setMartingale(
              "multiplier",
              Math.max(1, Math.min(10, Number(v) || 2)),
            )
          }
          type="number"
          step="0.1"
          disabled={!m.enabled}
        />
        <Field
          label="Max streak level"
          value={m.max_streak}
          onChange={(v) =>
            setMartingale(
              "max_streak",
              Math.max(0, Math.min(20, Number(v) || 0)),
            )
          }
          type="number"
          help="0 = uncapped"
          disabled={!m.enabled}
        />
        <div className="flex items-end gap-2 pb-1.5">
          <input
            id="reset_on_win"
            type="checkbox"
            checked={m.reset_on_win}
            onChange={(e) => setMartingale("reset_on_win", e.target.checked)}
            disabled={!m.enabled}
            className="h-4 w-4"
          />
          <Label htmlFor="reset_on_win" className="leading-tight">
            Reset on win
          </Label>
        </div>
      </div>
    </div>
  );
}

function TemplateEditor({
  value,
  onChange,
  templates,
}: {
  value: string;
  onChange: (v: string) => void;
  templates: ParserTemplate[];
}) {
  return (
    <div className="space-y-2">
      <Label>Template</Label>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="{DIRECTION} {ASSET} {DURATION}"
        className="font-mono"
      />
      <p className="text-xs text-muted-foreground">
        Placeholders: <code>{"{DIRECTION}"}</code>, <code>{"{ASSET}"}</code>,{" "}
        <code>{"{DURATION}"}</code>, <code>{"{TIME}"}</code>,{" "}
        <code>{"{STAKE}"}</code>
      </p>
      {templates.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {templates.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => onChange(t.template)}
              className="rounded-md border border-input bg-background px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function RegexEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-2">
      <Label>Regex</Label>
      <textarea
        className="flex min-h-[6rem] w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="^(?P<direction>BUY|SELL)\s+(?P<asset>[A-Z/]+)\s+(?P<duration>\d+m)"
      />
      <p className="text-xs text-muted-foreground">
        Required named groups: <code>direction</code>, <code>asset</code>.
        Optional: <code>duration</code>, <code>fire_at</code>,{" "}
        <code>stake</code>.
      </p>
    </div>
  );
}

function Pill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors " +
        (active
          ? "border-primary bg-primary/10"
          : "border-input text-muted-foreground hover:text-foreground")
      }
    >
      {children}
    </button>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  help,
  step,
  placeholder,
  disabled,
}: {
  label: string;
  value: string | number;
  onChange: (v: string) => void;
  type?: string;
  help?: string;
  step?: string;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Input
        type={type}
        step={step}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
      {help && <p className="text-xs text-muted-foreground">{help}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Live tester
// ---------------------------------------------------------------------------

function LiveTester({
  cfg,
  text,
  setText,
}: {
  cfg: ParserConfigPayload;
  text: string;
  setText: (v: string) => void;
}) {
  const [result, setResult] = useState<ParserTestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const assets = useQuery<BrokerAssets>({
    queryKey: ["broker", "assets"],
    queryFn: () => broker.assets(),
    staleTime: 60_000,
  });

  const run = useMutation({
    mutationFn: () => {
      const messages = text
        .split(/\n\s*\n/)
        .map((chunk) => chunk.trim())
        .filter(Boolean)
        .map((chunk) => ({ text: chunk }));
      if (messages.length === 0) {
        throw new ApiError("nothing to parse", 0);
      }
      return parsers.test(cfg, messages);
    },
    onSuccess: (r) => {
      setResult(r);
      setError(null);
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : String(err));
      setResult(null);
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Live tester</CardTitle>
        <CardDescription>
          Paste sample messages — separate multiple messages with a blank
          line. {assets.data
            ? `${assets.data.count} broker assets cached for auto-resolution.`
            : "Connect the broker to enable asset auto-resolution."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <textarea
          className="flex min-h-[10rem] w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="🟢 BUY EUR/USD 1m"
        />

        <div className="flex items-center gap-2">
          <Button onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? "Testing…" : "Test"}
          </Button>
          {error && (
            <span className="text-sm text-destructive">{error}</span>
          )}
        </div>

        {result && <ResultPanel result={result} />}
      </CardContent>
    </Card>
  );
}

function ResultPanel({ result }: { result: ParserTestResponse }) {
  if (!result.matched) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3">
        <div className="flex items-center gap-2">
          <Badge variant="destructive">no match</Badge>
          <span className="text-sm text-destructive">
            {result.error ?? "Unknown error"}
          </span>
        </div>
        {result.error_detail && (
          <pre className="mt-2 overflow-x-auto text-xs text-muted-foreground">
            {JSON.stringify(result.error_detail, null, 2)}
          </pre>
        )}
      </div>
    );
  }

  const s = result.signal!;
  const assetResolved =
    s.asset_raw && s.asset_raw !== s.asset
      ? `${s.asset_raw} → ${s.asset}`
      : s.asset;
  return (
    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2 pb-2">
        <Badge variant="success">match</Badge>
        <Badge variant="outline">{s.trade_mode}</Badge>
        {s.asset_via && <AssetViaBadge via={s.asset_via} />}
        <span className="text-xs text-muted-foreground">via {s.parser_id}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <Row label="Asset" value={assetResolved} />
        <Row label="Direction" value={s.direction} />
        <Row label="Duration" value={`${s.duration_seconds}s`} />
        <Row label="Stake" value={s.stake !== null ? String(s.stake) : "(default)"} />
        <Row label="Fire at" value={s.fire_at ?? "(live)"} />
      </dl>
      {Object.keys(s.matched_groups).length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            matched groups
          </summary>
          <pre className="mt-1 overflow-x-auto text-xs text-muted-foreground">
            {JSON.stringify(s.matched_groups, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function AssetViaBadge({ via }: { via: string }) {
  const variants: Record<string, "default" | "secondary" | "outline" | "warning" | "success"> = {
    alias: "default",
    exact: "success",
    otc: "warning",
    fallback: "outline",
  };
  return <Badge variant={variants[via] ?? "outline"}>asset: {via}</Badge>;
}

// ---------------------------------------------------------------------------
// Multi-message toggle
// ---------------------------------------------------------------------------

function MultiMessageToggle({
  seconds,
  onChange,
}: {
  seconds: number;
  onChange: (s: number) => void;
}) {
  const enabled = seconds > 0;
  return (
    <div className="space-y-2">
      <Label className="flex items-center gap-2">
        Multi-message signal
        {enabled && <Badge variant="outline">{seconds}s window</Badge>}
      </Label>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onChange(e.target.checked ? 10 : 0)}
            className="h-4 w-4"
          />
          Enable
        </label>
        {enabled && (
          <Input
            type="number"
            min={1}
            max={300}
            value={seconds}
            onChange={(e) => onChange(Math.max(1, Number(e.target.value) || 1))}
            className="w-24"
          />
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        On: buffers messages from the same sender within this window so a
        signal split across messages still parses as one.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent messages from this channel
// ---------------------------------------------------------------------------

function RecentMessagesPanel({
  chatId,
  onUse,
}: {
  chatId: number;
  onUse: (text: string) => void;
}) {
  const messages = useQuery<TelegramMessage[]>({
    queryKey: ["telegram", "messages", chatId],
    queryFn: () => telegram.messages(chatId, 20),
    retry: false,
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Recent messages</CardTitle>
            <CardDescription>
              Last 20 from this chat. Click <strong>Use</strong> to drop one
              into the live tester.
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => messages.refetch()}
            disabled={messages.isFetching}
          >
            {messages.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {messages.isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {messages.error && (
          <p className="text-sm text-destructive">
            {messages.error instanceof ApiError
              ? messages.error.message
              : String(messages.error)}
            {" — "}
            make sure you&rsquo;re logged into Telegram and this chat is in
            your watch list.
          </p>
        )}
        {messages.data && messages.data.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No recent text messages.
          </p>
        )}
        {messages.data && messages.data.length > 0 && (
          <ul className="divide-y rounded-md border">
            {messages.data.map((m) => (
              <li
                key={m.id}
                className="flex items-start justify-between gap-3 p-3"
              >
                <div className="min-w-0 flex-1">
                  <pre className="whitespace-pre-wrap break-words font-mono text-xs">
                    {m.text}
                  </pre>
                  <p className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                    {m.date ? new Date(m.date).toLocaleString() : "(no date)"}
                    {" · sender "}
                    {m.sender_id}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onUse(m.text)}
                >
                  Use
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono">{value}</dd>
    </div>
  );
}
