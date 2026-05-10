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
  type ParsedSignal,
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
  // Live-tester state: each entry is one Telegram message. We deliberately
  // do *not* split on blank lines anymore — a real Telegram message can
  // contain blank lines, and only a *new message arriving* is a boundary.
  const [testerMessages, setTesterMessages] = useState<string[]>([
    "BUY EURUSD 1m",
  ]);

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
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <a
              href="https://github.com/iahmedani/pyquotex/blob/master/autotrader/docs/PARSERS.md"
              target="_blank"
              rel="noreferrer"
              className="hover:text-foreground"
              title="Parser writing guide + troubleshooting"
            >
              📖 Parser guide
            </a>
            <Link
              href={`/dashboard/parsers/${chatId}`}
              className="hover:text-foreground"
            >
              ← Channel parsers
            </Link>
          </div>
        </div>
      </section>

      <RecentMessagesPanel
        chatId={chatId}
        onAddMessage={(chunk) =>
          setTesterMessages((prev) => {
            // If the only entry is empty (fresh state), replace it;
            // otherwise push as a new message.
            if (prev.length === 1 && !prev[0].trim()) return [chunk];
            return [...prev, chunk];
          })
        }
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
          messages={testerMessages}
          setMessages={setTesterMessages}
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
          <div className="flex flex-wrap gap-2">
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
            <Pill
              active={cfg.parser_type === "prep_trigger"}
              onClick={() => {
                set("parser_type", "prep_trigger");
                // Seed defaults for the two-phase config so the editor
                // has something to render.
                setCfg((p) => ({
                  ...p,
                  parser_config: {
                    prep_kind:
                      typeof p.parser_config.prep_kind === "string"
                        ? p.parser_config.prep_kind
                        : "template",
                    prep:
                      typeof p.parser_config.prep === "string"
                        ? p.parser_config.prep
                        : "PAIR: {ASSET} TIME: {DURATION} Minute",
                    trigger_kind:
                      typeof p.parser_config.trigger_kind === "string"
                        ? p.parser_config.trigger_kind
                        : "template",
                    trigger:
                      typeof p.parser_config.trigger === "string"
                        ? p.parser_config.trigger
                        : "{DIRECTION}",
                  },
                }));
              }}
            >
              Prep + Trigger
            </Pill>
            <Pill
              active={cfg.parser_type === "batch"}
              onClick={() => {
                set("parser_type", "batch");
                setCfg((p) => ({
                  ...p,
                  parser_config: {
                    row_kind:
                      typeof p.parser_config.row_kind === "string"
                        ? p.parser_config.row_kind
                        : "regex",
                    row:
                      typeof p.parser_config.row === "string"
                        ? p.parser_config.row
                        : (
                            "^(?P<time>\\d{1,2}:\\d{2})\\s+" +
                            "(?P<asset>\\S+)\\s+" +
                            "(?P<direction>CALL|PUT)\\s*$"
                          ),
                    header_kind:
                      typeof p.parser_config.header_kind === "string"
                        ? p.parser_config.header_kind
                        : "regex",
                    header:
                      typeof p.parser_config.header === "string"
                        ? p.parser_config.header
                        : (
                            "DATE\\s*:\\s*(?P<date>\\d{1,2}[./-]\\d{1,2}[./-]\\d{2,4}).*?" +
                            "TIMEZONE\\s*:\\s*UTC/GMT\\s*\\((?P<tz_offset>[+-]\\d{1,2}(?::?\\d{2})?)\\)"
                          ),
                  },
                }));
              }}
            >
              Batch
            </Pill>
          </div>
          {cfg.parser_type === "prep_trigger" && (
            <p className="text-xs text-muted-foreground">
              Two-phase: the <strong>prep</strong> message defines
              asset / duration / stake; the <strong>trigger</strong>
              message (often a 👍/👎 sticker) fires the trade
              immediately. Stale prep is dropped after{" "}
              {cfg.aggregate_window_seconds || 120}s.
            </p>
          )}
          {cfg.parser_type === "batch" && (
            <p className="text-xs text-muted-foreground">
              One message → many scheduled signals. The <strong>header</strong>{" "}
              regex captures DATE + timezone offset (applies to every row);
              the <strong>row</strong> regex matches each TIME / ASSET /
              DIRECTION line and is run with <code>finditer</code>. Each row
              becomes a pending order at the resolved UTC time.
            </p>
          )}
        </div>

        {cfg.parser_type === "template" && (
          <TemplateEditor
            value={String(cfg.parser_config.template ?? "")}
            onChange={(v) => setParserConfigField("template", v)}
            templates={templates.data ?? []}
          />
        )}
        {cfg.parser_type === "regex" && (
          <RegexEditor
            value={String(cfg.parser_config.pattern ?? "")}
            onChange={(v) => setParserConfigField("pattern", v)}
          />
        )}
        {cfg.parser_type === "prep_trigger" && (
          <PrepTriggerEditor
            prep={String(cfg.parser_config.prep ?? "")}
            prepKind={
              cfg.parser_config.prep_kind === "regex" ? "regex" : "template"
            }
            trigger={String(cfg.parser_config.trigger ?? "")}
            triggerKind={
              cfg.parser_config.trigger_kind === "regex"
                ? "regex"
                : "template"
            }
            onChange={(field, value) => setParserConfigField(field, value)}
            templates={templates.data ?? []}
          />
        )}
        {cfg.parser_type === "batch" && (
          <BatchEditor
            row={String(cfg.parser_config.row ?? "")}
            rowKind={
              cfg.parser_config.row_kind === "template" ? "template" : "regex"
            }
            header={String(cfg.parser_config.header ?? "")}
            headerKind={
              cfg.parser_config.header_kind === "template"
                ? "template"
                : "regex"
            }
            onChange={(field, value) => setParserConfigField(field, value)}
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
          {cfg.parser_type === "prep_trigger" ? (
            <Field
              label="Prep-to-trigger gap (seconds)"
              value={cfg.aggregate_window_seconds || 120}
              onChange={(v) =>
                set(
                  "aggregate_window_seconds",
                  Math.max(1, Math.min(600, Number(v) || 120)),
                )
              }
              type="number"
              help="Drop a stored prep when no trigger arrives in this many seconds."
            />
          ) : (
            <MultiMessageToggle
              seconds={cfg.aggregate_window_seconds}
              onChange={(s) =>
                set("aggregate_window_seconds", Math.max(0, s))
              }
            />
          )}
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
        <WinningStreakBlock cfg={cfg} setMartingale={setMartingale} />

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

function WinningStreakBlock({
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
    <div className="space-y-3 rounded-md border border-emerald-500/20 bg-emerald-500/5 p-3">
      <div className="flex items-center justify-between gap-2">
        <Label className="flex items-center gap-2">
          Winning streak (Paroli)
          {m.winning_streak_enabled && <Badge variant="success">on</Badge>}
        </Label>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={m.winning_streak_enabled}
            onChange={(e) =>
              setMartingale("winning_streak_enabled", e.target.checked)
            }
            className="h-4 w-4"
          />
          Enable
        </label>
      </div>

      <p className="text-xs text-muted-foreground">
        On a win, the next channel signal stakes at{" "}
        <code>ceil(prev_stake + prev_profit)</code> up to max level,
        then resets to base. A loss at any point also resets to base.
        Stakes round up to the nearest integer (Quotex constraint).
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Max win streak level"
          value={m.winning_streak_max_level}
          onChange={(v) =>
            setMartingale(
              "winning_streak_max_level",
              Math.max(0, Math.min(20, Number(v) || 0)),
            )
          }
          type="number"
          help="0 = uncapped"
          disabled={!m.winning_streak_enabled}
        />
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

// ---------------------------------------------------------------------------
// Prep + Trigger editor
// ---------------------------------------------------------------------------

function PrepTriggerEditor({
  prep,
  prepKind,
  trigger,
  triggerKind,
  onChange,
  templates,
}: {
  prep: string;
  prepKind: "template" | "regex";
  trigger: string;
  triggerKind: "template" | "regex";
  onChange: (
    field: "prep" | "trigger" | "prep_kind" | "trigger_kind",
    value: string,
  ) => void;
  templates: ParserTemplate[];
}) {
  return (
    <div className="space-y-5">
      <PhaseEditor
        title="Prep message (1st)"
        description="Extracts the trade parameters. Required: ASSET. Optional: DURATION, TIME, STAKE."
        kind={prepKind}
        value={prep}
        onKindChange={(k) => onChange("prep_kind", k)}
        onValueChange={(v) => onChange("prep", v)}
        templatePlaceholder="PAIR: {ASSET} TIME: {DURATION} Minute"
        regexPlaceholder="PAIR\s*:\s*(?P<asset>[A-Z\s/-]+?)\s+TIME\s*:\s*(?P<duration>\d+)\s*Minute"
        templates={templates}
        requiredGroups={["asset"]}
      />
      <PhaseEditor
        title="Trigger message (2nd)"
        description="Fires the trade the moment this matches. Required: DIRECTION."
        kind={triggerKind}
        value={trigger}
        onKindChange={(k) => onChange("trigger_kind", k)}
        onValueChange={(v) => onChange("trigger", v)}
        templatePlaceholder="{DIRECTION}"
        regexPlaceholder="(?P<direction>👍|👎)"
        templates={[]}
        requiredGroups={["direction"]}
      />
    </div>
  );
}

function PhaseEditor({
  title,
  description,
  kind,
  value,
  onKindChange,
  onValueChange,
  templatePlaceholder,
  regexPlaceholder,
  templates,
  requiredGroups,
}: {
  title: string;
  description: string;
  kind: "template" | "regex";
  value: string;
  onKindChange: (k: "template" | "regex") => void;
  onValueChange: (v: string) => void;
  templatePlaceholder: string;
  regexPlaceholder: string;
  templates: ParserTemplate[];
  requiredGroups: string[];
}) {
  return (
    <div className="space-y-2 rounded-md border border-amber-500/20 bg-amber-500/5 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label className="font-semibold">{title}</Label>
        <div className="flex gap-1">
          <SmallPill active={kind === "template"} onClick={() => onKindChange("template")}>
            Template
          </SmallPill>
          <SmallPill active={kind === "regex"} onClick={() => onKindChange("regex")}>
            Regex
          </SmallPill>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">{description}</p>

      {kind === "template" ? (
        <Input
          value={value}
          onChange={(e) => onValueChange(e.target.value)}
          placeholder={templatePlaceholder}
          className="font-mono"
        />
      ) : (
        <textarea
          className="flex min-h-[5rem] w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          value={value}
          onChange={(e) => onValueChange(e.target.value)}
          placeholder={regexPlaceholder}
        />
      )}

      <p className="text-[11px] text-muted-foreground">
        Required: {requiredGroups.map((g) => <code key={g}>{g}</code>).reduce<React.ReactNode[]>(
          (acc, el, i) => (i === 0 ? [el] : [...acc, ", ", el]),
          [],
        )}
        {kind === "template" && templates.length > 0 && (
          <span className="ml-2">
            · presets:
            {templates.slice(0, 3).map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => onValueChange(t.template)}
                className="ml-1 rounded border border-input bg-background px-1.5 py-0.5 hover:bg-accent hover:text-foreground"
              >
                {t.label}
              </button>
            ))}
          </span>
        )}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Batch editor
// ---------------------------------------------------------------------------

function BatchEditor({
  row,
  rowKind,
  header,
  headerKind,
  onChange,
}: {
  row: string;
  rowKind: "template" | "regex";
  header: string;
  headerKind: "template" | "regex";
  onChange: (
    field: "row" | "header" | "row_kind" | "header_kind",
    value: string,
  ) => void;
}) {
  return (
    <div className="space-y-5">
      <PhaseEditor
        title="Header (optional)"
        description="Captures DATE and tz_offset. Both groups are optional individually; without a header the parser uses today's date in the channel timezone."
        kind={headerKind}
        value={header}
        onKindChange={(k) => onChange("header_kind", k)}
        onValueChange={(v) => onChange("header", v)}
        templatePlaceholder=""
        regexPlaceholder={
          "DATE\\s*:\\s*(?P<date>\\d{1,2}[./-]\\d{1,2}[./-]\\d{2,4}).*?" +
          "TIMEZONE\\s*:\\s*UTC/GMT\\s*\\((?P<tz_offset>[+-]\\d{1,2}(?::?\\d{2})?)\\)"
        }
        templates={[]}
        requiredGroups={["date", "tz_offset"]}
      />
      <PhaseEditor
        title="Row (per signal)"
        description="One match per scheduled signal. Run with finditer — each match becomes a pending order at the resolved UTC time."
        kind={rowKind}
        value={row}
        onKindChange={(k) => onChange("row_kind", k)}
        onValueChange={(v) => onChange("row", v)}
        templatePlaceholder="{TIME} {ASSET} {DIRECTION}"
        regexPlaceholder={
          "^(?P<time>\\d{1,2}:\\d{2})\\s+" +
          "(?P<asset>\\S+)\\s+" +
          "(?P<direction>CALL|PUT)\\s*$"
        }
        templates={[]}
        requiredGroups={["time", "asset", "direction"]}
      />
    </div>
  );
}

function SmallPill({
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
        "rounded-md border px-2 py-0.5 text-xs font-medium transition-colors " +
        (active
          ? "border-primary bg-primary/10"
          : "border-input text-muted-foreground hover:text-foreground")
      }
    >
      {children}
    </button>
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
  messages,
  setMessages,
}: {
  cfg: ParserConfigPayload;
  messages: string[];
  setMessages: React.Dispatch<React.SetStateAction<string[]>>;
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
      const payload = messages
        .map((text, i) => ({ text, i }))
        .filter((m) => m.text.trim())
        .map((m) => ({ text: m.text }));
      if (payload.length === 0) {
        throw new ApiError("nothing to parse", 0);
      }
      return parsers.test(cfg, payload);
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

  function setAt(index: number, value: string) {
    setMessages((prev) => prev.map((m, i) => (i === index ? value : m)));
  }
  function removeAt(index: number) {
    setMessages((prev) =>
      prev.length === 1 ? [""] : prev.filter((_, i) => i !== index),
    );
  }
  function add() {
    setMessages((prev) => [...prev, ""]);
  }
  function clearAll() {
    setMessages([""]);
    setResult(null);
    setError(null);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Live tester</CardTitle>
        <CardDescription>
          One textarea per Telegram message. Blank lines inside a message
          are kept (real prep messages have them).{" "}
          {assets.data
            ? `${assets.data.count} broker assets cached for auto-resolution.`
            : "Connect the broker to enable asset auto-resolution."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {messages.map((text, i) => (
          <MessageBlock
            key={i}
            index={i}
            count={messages.length}
            value={text}
            onChange={(v) => setAt(i, v)}
            onRemove={() => removeAt(i)}
          />
        ))}

        <div className="flex flex-wrap gap-2">
          <Button onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? "Testing…" : "Test"}
          </Button>
          <Button type="button" variant="outline" onClick={add}>
            + Add message
          </Button>
          {messages.length > 1 || messages[0]?.trim() ? (
            <Button type="button" variant="outline" onClick={clearAll}>
              Clear
            </Button>
          ) : null}
          {error && (
            <span className="self-center text-sm text-destructive">
              {error}
            </span>
          )}
        </div>

        {result && <ResultPanel result={result} />}
      </CardContent>
    </Card>
  );
}

function MessageBlock({
  index,
  count,
  value,
  onChange,
  onRemove,
}: {
  index: number;
  count: number;
  value: string;
  onChange: (v: string) => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-md border border-input">
      <div className="flex items-center justify-between border-b border-input bg-muted/30 px-3 py-1.5 text-xs">
        <span className="font-medium text-muted-foreground">
          Message {index + 1}
          {index === 0 && count > 1 && " (prep)"}
          {index === 1 && count > 1 && " (trigger)"}
        </span>
        <button
          type="button"
          onClick={onRemove}
          className="text-muted-foreground hover:text-destructive"
          aria-label="Remove message"
          title="Remove message"
        >
          ✕
        </button>
      </div>
      <textarea
        className="flex min-h-[5rem] w-full bg-transparent px-3 py-2 font-mono text-xs focus-visible:outline-none"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={
          index === 0
            ? "🌐 PAIR: USD NGN OTC\n\n⏱️ TIME: 01 Minute"
            : "👍"
        }
      />
    </div>
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

  const signals = result.signals.length > 0 ? result.signals : [result.signal!];
  const isBatch = signals.length > 1;

  return (
    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2 pb-2">
        <Badge variant="success">
          match{isBatch ? ` × ${signals.length}` : ""}
        </Badge>
        <Badge variant="outline">{signals[0].trade_mode}</Badge>
        {signals[0].asset_via && <AssetViaBadge via={signals[0].asset_via} />}
        <span className="text-xs text-muted-foreground">
          via {signals[0].parser_id}
        </span>
      </div>
      {isBatch ? <SignalsTable signals={signals} /> : <SignalDetails s={signals[0]} />}
    </div>
  );
}

function SignalDetails({ s }: { s: ParsedSignal }) {
  const assetResolved =
    s.asset_raw && s.asset_raw !== s.asset
      ? `${s.asset_raw} → ${s.asset}`
      : s.asset;
  return (
    <>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <Row label="Asset" value={assetResolved} />
        <Row label="Direction" value={s.direction} />
        <Row label="Duration" value={`${s.duration_seconds}s`} />
        <Row
          label="Stake"
          value={s.stake !== null ? String(s.stake) : "(default)"}
        />
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
    </>
  );
}

function SignalsTable({ signals }: { signals: ParsedSignal[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-emerald-500/20 text-left text-muted-foreground">
            <th className="px-2 py-1 font-medium">#</th>
            <th className="px-2 py-1 font-medium">Asset</th>
            <th className="px-2 py-1 font-medium">Dir</th>
            <th className="px-2 py-1 font-medium">Duration</th>
            <th className="px-2 py-1 font-medium">Fire at (UTC)</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s, i) => {
            const asset =
              s.asset_raw && s.asset_raw !== s.asset
                ? `${s.asset_raw} → ${s.asset}`
                : s.asset;
            return (
              <tr
                key={`${s.parser_id}-${i}`}
                className="border-b border-emerald-500/10 last:border-0"
              >
                <td className="px-2 py-1 text-muted-foreground">{i + 1}</td>
                <td className="px-2 py-1 font-mono">{asset}</td>
                <td className="px-2 py-1">
                  <Badge
                    variant={s.direction === "call" ? "success" : "destructive"}
                  >
                    {s.direction}
                  </Badge>
                </td>
                <td className="px-2 py-1 font-mono">{s.duration_seconds}s</td>
                <td className="px-2 py-1 font-mono">
                  {s.fire_at
                    ? new Date(s.fire_at).toISOString().replace("T", " ").slice(0, 19)
                    : "(live)"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
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
  onAddMessage,
}: {
  chatId: number;
  onAddMessage: (text: string) => void;
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
              Last 20 text messages and stickers from this chat. Click{" "}
              <strong>Add to tester</strong> to push a message into the
              live-tester pane below — each click adds a separate
              message block (so a prep + sticker pair stays as two
              messages, not one merged blob).
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
            No recent text messages or stickers.
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
                  <div className="mb-1 flex flex-wrap items-center gap-1.5">
                    <MediaKindBadge kind={m.media_kind} />
                  </div>
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
                  size="sm"
                  onClick={() => onAddMessage(m.text)}
                  title="Add as a new message block in the live tester"
                >
                  Add to tester
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function MediaKindBadge({ kind }: { kind: TelegramMessage["media_kind"] }) {
  if (kind === "sticker") return <Badge variant="warning">sticker</Badge>;
  if (kind === "caption") return <Badge variant="outline">caption</Badge>;
  return <Badge variant="secondary">text</Badge>;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono">{value}</dd>
    </div>
  );
}
