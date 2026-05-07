"""Service layer.

Phases land here:
    * Phase 1 — quotex_manager.py    (long-lived pyquotex client)
    * Phase 2 — telegram_client.py   (Pyrogram session + dispatch)
    * Phase 3 — parsers/             (template, regex, aggregator)
    * Phase 4 — pipeline.py          (signal -> risk -> execute)
    * Phase 5 — risk.py              (limits, kill switch, sizing)
"""
