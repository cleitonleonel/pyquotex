"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
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
  type ParserConfigPayload,
  type ParserTemplate,
  type ParserTestResponse,
  type ParserType,
  type TelegramDialog,
  parsers,
  telegram,
} from "@/lib/api";

const DEFAULT_CONFIG: ParserConfigPayload = {
  parser_type: "template",
  parser_config: { template: "{DIRECTION} {ASSET} {DURATION}" },
  timezone: "UTC",
  timezone_offset_minutes: 0,
  asset_aliases: {},
  default_stake: 1,
  default_duration_seconds: 60,
  aggregate_window_seconds: 0,
  enabled: true,
};

export default function ParserBuilderPage() {
  const router = useRouter();
  const params = useParams<{ chat_id: string }>();
  const chatId = Number(params.chat_id);

  const qc = useQueryClient();

  const watched = useQuery<TelegramDialog[]>({
    queryKey: ["telegram", "watched"],
    queryFn: telegram.watched,
  });
  const channel = useMemo(
    () => watched.data?.find((d) => d.chat_id === chatId),
    [watched.data, chatId],
  );

  const existing = useQuery({
    queryKey: ["parsers", "config", chatId],
    queryFn: () => parsers.get(chatId),
    retry: false,
  });

  const [cfg, setCfg] = useState<ParserConfigPayload>(DEFAULT_CONFIG);
  const [aliasesText, setAliasesText] = useState<string>("");
  const [aliasesError, setAliasesError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    if (existing.data && !hydrated) {
      const data = existing.data;
      setCfg({
        parser_type: data.parser_type,
        parser_config: data.parser_config,
        timezone: data.timezone,
        timezone_offset_minutes: data.timezone_offset_minutes,
        asset_aliases: data.asset_aliases,
        default_stake: data.default_stake,
        default_duration_seconds: data.default_duration_seconds,
        aggregate_window_seconds: data.aggregate_window_seconds,
        enabled: data.enabled,
      });
      setAliasesText(
        Object.entries(data.asset_aliases)
          .map(([k, v]) => `${k} = ${v}`)
          .join("\n"),
      );
      setHydrated(true);
    } else if (!existing.data && !existing.isLoading && !hydrated) {
      setHydrated(true);
    }
  }, [existing.data, existing.isLoading, hydrated]);

  const save = useMutation({
    mutationFn: () => parsers.upsert(chatId, cfg),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["parsers"] });
    },
  });

  const wipe = useMutation({
    mutationFn: () => parsers.remove(chatId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["parsers"] });
      router.push("/dashboard/parsers");
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

  if (!hydrated || watched.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <section>
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">
              {channel?.title ?? `Chat ${chatId}`}
            </h2>
            <p className="text-sm text-muted-foreground">
              {channel?.username
                ? `@${channel.username}`
                : `chat ${chatId}`}
              {channel?.chat_type ? ` · ${channel.chat_type}` : ""}
            </p>
          </div>
          <Link
            href="/dashboard/parsers"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← All parsers
          </Link>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <ConfigEditor
          cfg={cfg}
          setCfg={setCfg}
          aliasesText={aliasesText}
          setAliasesText={setAliasesText}
          applyAliases={applyAliases}
          aliasesError={aliasesError}
        />
        <LiveTester cfg={cfg} />
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
            {save.isPending ? "Saving…" : "Save config"}
          </Button>
          {save.isSuccess && (
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
          {existing.data && (
            <Button
              variant="outline"
              onClick={() => {
                if (confirm(`Delete parser config for ${channel?.title ?? chatId}?`)) {
                  wipe.mutate();
                }
              }}
              disabled={wipe.isPending}
            >
              {wipe.isPending ? "Deleting…" : "Delete config"}
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
          Pick a template or write a regex with named groups
          (<code>direction</code>, <code>asset</code>, optionally{" "}
          <code>duration</code>, <code>fire_at</code>, <code>stake</code>).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-2">
          <Label>Parser type</Label>
          <div className="flex gap-2">
            <TypePill
              active={cfg.parser_type === "template"}
              onClick={() => set("parser_type", "template")}
            >
              Template
            </TypePill>
            <TypePill
              active={cfg.parser_type === "regex"}
              onClick={() => set("parser_type", "regex")}
            >
              Regex
            </TypePill>
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
          />
          <Field
            label="Timezone offset (minutes)"
            value={cfg.timezone_offset_minutes}
            onChange={(v) => set("timezone_offset_minutes", Number(v) || 0)}
            type="number"
            help="Channel-local offset from UTC (e.g. 60 for UTC+1, -300 for UTC-5)"
          />
          <Field
            label="Aggregate window (seconds)"
            value={cfg.aggregate_window_seconds}
            onChange={(v) =>
              set("aggregate_window_seconds", Math.max(0, Number(v) || 0))
            }
            type="number"
            help="0 = single-message; >0 buffers messages from the same sender"
          />
        </div>

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

function TypePill({
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
}: {
  label: string;
  value: string | number;
  onChange: (v: string) => void;
  type?: string;
  help?: string;
  step?: string;
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Input
        type={type}
        step={step}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {help && <p className="text-xs text-muted-foreground">{help}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Live tester
// ---------------------------------------------------------------------------

function LiveTester({ cfg }: { cfg: ParserConfigPayload }) {
  const [text, setText] = useState("BUY EURUSD 1m");
  const [result, setResult] = useState<ParserTestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

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
          line. Hit <em>Test</em> to see the structured signal.
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
  return (
    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm">
      <div className="flex items-center gap-2 pb-2">
        <Badge variant="success">match</Badge>
        <span className="text-xs text-muted-foreground">via {s.parser_id}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <Row label="Asset" value={s.asset} />
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

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono">{value}</dd>
    </div>
  );
}
