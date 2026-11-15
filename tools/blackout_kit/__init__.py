"""Blackout Kit package: encrypted special feeds for the ArvanCloud mirror.

Contents (stdlib only):
  aesgcm.py        pure-Python AES-GCM (validated against `cryptography`)
  crypto.py        key handling + the BK1 envelope used for special feeds
  feeds.py         special-feed definitions, newest-wins, dedupe registry
  telegram_mine.py Telegram mining, public channels/groups + Saved Messages
  login.py         Telethon login script (run by Ryan, interactively)
  search_loop.py   blackout-gated intensive search (monitor verdict trigger)
  publish.py       findings -> encrypted feeds -> feed-data repo
  cli.py           `python3 -m tools.blackout_kit.cli ...` entry points
  make_decrypt_helper.py  builds the standalone decrypt_helper.py
"""

from __future__ import annotations
