"""
Smoke test — session_manager + character fields, no API key needed.
Run from ttrpg-signal/: py test_smoke.py
"""
import os, sys
sys.path.insert(0, "bot")
os.environ.setdefault("TTRPG_PATH", "Z:/ttrpg")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("SIGNAL_PHONE_NUMBER", "+00000000000")
os.environ.setdefault("ADMIN_PHONE_NUMBER", "+00000000000")
os.environ.setdefault("SIGNAL_API_URL", "http://localhost:8085")

import session_manager

FOLDER = "misguided_steps"
PLAYERS = ["phieb"]

# ── 1. Flag fields ─────────────────────────────────────────────────────────────
print("=" * 60)
print("1. CHARACTER FIELDS (active flags)")
print("=" * 60)
fields = session_manager.load_character_fields(FOLDER)
if fields:
    for f in fields:
        req = "REQUIRED" if f.get("required") else "optional"
        print(f"  [{req}] {f['key']}  (detail: {f.get('detail', '?')})")
else:
    print("  (none — no flags active or no CHARACTER_FIELDS.yaml found)")

# ── 2. Completeness check ──────────────────────────────────────────────────────
print()
print("=" * 60)
print("2. COMPLETENESS CHECK")
print("=" * 60)
missing = session_manager.check_character_completeness(FOLDER, PLAYERS)
if missing:
    for player, gaps in missing.items():
        print(f"  {player}: MISSING -> {', '.join(gaps)}")
else:
    print("  All characters complete OK")

# ── 3. build_context output ────────────────────────────────────────────────────
print()
print("=" * 60)
print("3. build_context() — what Claude sees")
print("=" * 60)
ctx = session_manager.build_context(FOLDER)
print(ctx.encode("cp1252", errors="replace").decode("cp1252"))
