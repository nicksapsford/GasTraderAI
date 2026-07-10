"""
GasTrader AI -- dashboard_gas.py
Two-page browser dashboard at http://localhost:5006
Page 1: Live trading view -- Daily/1h/5m trend cards, Arthur's full-width
        decision panel, performance, open position, liquidity period,
        pre-checks, calendar.
Page 2: P&L, performance detail, monthly breakdown, full trade history.
Uses Response() to avoid Jinja2 template conflicts.
All JS uses string concatenation -- no template literals.
"""

import csv
import json
import logging
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from flask import Flask, Response, jsonify, request

import guinevere_news

log = logging.getLogger("GasTrader.Dashboard")
logging.basicConfig(level=logging.WARNING)

from pathlib import Path
_BASE = Path(__file__).resolve().parent
_VER = _BASE / "VERSION"
APP_VERSION = _VER.read_text().strip() if _VER.exists() else "1.0.0"


def get_git_hash():
    try:
        result = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__)))
        return result.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


VERSION_STRING = "v" + str(APP_VERSION) + " (" + get_git_hash() + ")"


def get_stay_out_quality():
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'phantom_trades.csv')
    if not os.path.exists(csv_path):
        return {'status': 'No data yet', 'decisions': [], 'quality_score': None,
                'net_saved': None, 'correct': 0, 'wrong': 0, 'neutral': 0}
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        last_10 = rows[-10:]
        correct = sum(1 for r in last_10 if r.get('verdict') == 'CORRECT')
        wrong   = sum(1 for r in last_10 if r.get('verdict') == 'WRONG')
        neutral = sum(1 for r in last_10 if r.get('verdict') == 'NEUTRAL')
        total   = (correct + wrong + neutral)
        quality_score = round((correct / total) * 100) if total else 0
        net_saved  = sum(float(r.get('pnl_1hr', 0) or 0) for r in last_10 if r.get('verdict') == 'CORRECT')
        net_missed = sum(float(r.get('pnl_1hr', 0) or 0) for r in last_10 if r.get('verdict') == 'WRONG')
        return {'status': 'ok', 'decisions': last_10, 'quality_score': quality_score,
                'net_saved': net_saved, 'net_missed': net_missed, 'correct': correct, 'wrong': wrong, 'neutral': neutral}
    except Exception as e:
        return {'status': 'Error: ' + str(e), 'decisions': []}

BASE_DIR         = Path(__file__).resolve().parent
PORT             = 5006
LOG_DIR          = BASE_DIR / "logs"
TRADES_LOG       = LOG_DIR / "gas_trades.csv"
SHUTDOWN_FLAG    = LOG_DIR / "shutdown.flag"
STARTING_CAPITAL = 1000.0

app = Flask(__name__)

_state_lock = threading.Lock()
_state: dict = {
    "mode":             "PAPER",
    "version":          APP_VERSION,
    "liquidity_period": "CLOSED",
    "gas_price_usd":   0.0,
    "connector_status": "yahoo",
    "capital":          1000.0,
    "daily_pnl":        0.0,
    "total_trades":     0,
    "win_rate":         0.0,
    "in_trade":         False,
    "current_trade":    None,
    "decision":         None,
    "panel_mode":       "pre_checks",
    "pre_checks":       None,
    "checklist":        {},
    "trend_1d":         "NEUTRAL",
    "trend_1h":         "NEUTRAL",
    "signal_5m":        "NEUTRAL",
    "indicators_1d":    {},
    "indicators_1h":    {},
    "indicators_5m":    {},
    "perf":             None,
    "calendar":         "",
    "kill_switch":      False,
    "kill_tier":        0,
    "gbpusd_rate":      0.0,
    "updated_at":       "--",
}


def push_state(new_state: dict) -> None:
    """Called from the main loop to update dashboard state."""
    with _state_lock:
        _state.update(new_state)


def get_state() -> dict:
    with _state_lock:
        return dict(_state)


# ---------------------------------------------------------------------------
# Trade log readers (Page 2)
# ---------------------------------------------------------------------------

def load_trades() -> list:
    """Load all Gas trades from CSV, most recent first."""
    if not TRADES_LOG.exists():
        return []
    try:
        df = pd.read_csv(TRADES_LOG)
        if df.empty:
            return []
        trades = []
        for _, row in df.iterrows():
            pnl_gbp = float(row["pnl_gbp"])
            pnl_usd = float(row["pnl_usd"])
            trades.append({
                "direction":   row["direction"],
                "entry_time":  row["entry_time"],
                "exit_time":   row["exit_time"],
                "entry_price": f"{float(row['entry_price_usd']):,.2f}",
                "exit_price":  f"{float(row['exit_price_usd']):,.2f}",
                "points":      f"{float(row['points_gained']):+.1f}",
                "pnl_usd":     f"{pnl_usd:+.2f}",
                "pnl_gbp":     f"{pnl_gbp:+.2f}",
                "gbpusd":      f"{float(row['gbpusd_rate']):.4f}",
                "pnl_class":   "win" if pnl_gbp >= 0 else "loss",
                "reason":      row["exit_reason"],
                "liquidity":   row.get("liquidity_period", "--"),
            })
        return list(reversed(trades))
    except Exception:
        return []


def load_account_stats() -> dict:
    empty = {
        "capital": STARTING_CAPITAL, "total_pnl": 0.0,
        "total_return": 0.0, "total_trades": 0,
        "winners": 0, "losers": 0, "win_rate": 0.0,
        "daily_pnl": 0.0,
    }
    if not TRADES_LOG.exists():
        return empty
    try:
        df = pd.read_csv(TRADES_LOG)
        if df.empty:
            return empty
        capital      = float(df["capital_after_gbp"].iloc[-1])
        pnls         = df["pnl_gbp"].astype(float)
        total_pnl    = capital - STARTING_CAPITAL
        total_return = (capital / STARTING_CAPITAL - 1) * 100
        winners      = int(len(pnls[pnls > 0]))
        losers       = int(len(pnls[pnls < 0]))
        total        = int(len(pnls))
        win_rate     = (winners / total * 100) if total > 0 else 0.0
        today        = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_df     = df[df["date"] == today] if "date" in df.columns else df
        daily_pnl    = today_df["pnl_gbp"].astype(float).sum() if not today_df.empty else 0.0
        return {
            "capital": capital, "total_pnl": total_pnl,
            "total_return": total_return, "total_trades": total,
            "winners": winners, "losers": losers, "win_rate": win_rate,
            "daily_pnl": daily_pnl,
        }
    except Exception:
        return empty


def load_monthly_stats() -> list:
    """Group trades by calendar month for the Page 2 breakdown table."""
    if not TRADES_LOG.exists():
        return []
    try:
        df = pd.read_csv(TRADES_LOG)
        if df.empty:
            return []
        df["pnl_gbp"] = df["pnl_gbp"].astype(float)
        df["_dt"]     = pd.to_datetime(df["entry_time"], errors="coerce")
        df["_mk"]     = df["_dt"].dt.strftime("%Y-%m")
        df["_ml"]     = df["_dt"].dt.strftime("%b %Y")
        monthly = []
        for mk, grp in df.groupby("_mk"):
            pnls  = grp["pnl_gbp"]
            wins  = int(len(pnls[pnls > 0]))
            total = int(len(pnls))
            gross = round(float(pnls.sum()), 2)
            monthly.append({
                "month":    grp["_ml"].iloc[0],
                "trades":   total,
                "wins":     wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
                "pnl":      gross,
            })
        monthly.sort(key=lambda x: x["month"])
        return monthly
    except Exception:
        return []


# ---------------------------------------------------------------------------
# HTML -- two-page dashboard
# ---------------------------------------------------------------------------
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NaturalGasTrader A.I. &mdash; Natural Gas</title>
<style>
:root{
  --bg:#0d0d0d;--bg2:#141414;--bg3:#1e1e1e;--border:#2a2a2a;
  --gas:#228B22;--green:#2ecc71;--red:#e74c3c;--amber:#f39c12;
  --text:#e0e0e0;--muted:#888;
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;overflow:hidden;}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;display:flex;flex-direction:column;}

/* HEADER */
.header{background:var(--bg2);border-bottom:2px solid var(--gas);padding:7px 14px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;height:46px;}
.header-brand{display:flex;align-items:center;gap:8px;}
.logo{font-size:17px;font-weight:700;color:var(--gas);letter-spacing:1px;}
.logo span{color:var(--text);}
.logo-line{display:flex;align-items:baseline;gap:10px;}
.hdr-version{font-size:11px;color:var(--muted);font-family:monospace;font-weight:400;letter-spacing:0.5px;}
.subtitle{color:var(--muted);font-size:10px;margin-top:1px;}
.header-right{display:flex;align-items:center;gap:10px;}
.clock{font-size:15px;font-weight:600;color:var(--gas);font-family:monospace;}
.excalibur-status{font-size:10px;color:var(--amber);white-space:nowrap;}
.header-price{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:0 20px;border-left:1px solid var(--border);border-right:1px solid var(--border);}
.hdr-price-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:1px;}
.hdr-price-val{font-size:22px;font-weight:700;color:var(--gas);font-family:monospace;letter-spacing:1px;}

/* BUTTONS */
.shutdown-btn{background:rgba(231,76,60,0.08);border:1px solid var(--red);color:var(--red);padding:4px 9px;border-radius:4px;font-size:10px;cursor:pointer;letter-spacing:0.5px;text-transform:uppercase;transition:background 0.15s;}
.shutdown-btn:hover{background:rgba(231,76,60,0.25);}
.nav-btn{background:rgba(255,215,0,0.15);border:1px solid var(--gas);color:var(--gas);padding:4px 12px;border-radius:4px;font-size:11px;font-weight:600;cursor:pointer;letter-spacing:0.3px;transition:background 0.15s;}
.nav-btn:hover{background:rgba(255,215,0,0.32);}

/* SHUTDOWN MODAL */
.modal-overlay{display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,0.78);justify-content:center;align-items:center;}
.modal-overlay.open{display:flex;}
.modal{background:var(--bg2);border:2px solid var(--red);border-radius:10px;padding:22px 28px;max-width:380px;text-align:center;}
.modal h3{color:var(--red);font-size:15px;margin-bottom:10px;}
.modal p{color:var(--muted);font-size:12px;line-height:1.5;margin-bottom:6px;}
.modal-trade-warn{background:rgba(231,76,60,0.1);border:1px solid var(--red);border-radius:5px;padding:8px;margin:10px 0;color:var(--red);font-size:11px;font-weight:600;}
.modal-btns{display:flex;gap:10px;justify-content:center;margin-top:14px;}
.btn-cancel {background:var(--bg3);border:1px solid var(--border);color:var(--gas);padding:6px 16px;border-radius:4px;cursor:pointer;font-size:11px;}
.btn-confirm{background:rgba(231,76,60,0.1);border:1px solid var(--red);color:var(--red);padding:6px 16px;border-radius:4px;cursor:pointer;font-size:11px;}
.btn-cancel:hover {background:rgba(255,215,0,0.15);}
.btn-confirm:hover{background:rgba(231,76,60,0.25);}

/* PAGE WRAPPERS */
.page-wrap{flex:1;min-height:0;overflow:hidden;display:flex;flex-direction:column;}
#page2{overflow-y:auto;}

/* PAGE 1 GRID -- left indicators | centre (Arthur, full width) | right status */
.main{flex:1;display:grid;grid-template-columns:200px 1fr 260px;gap:7px;padding:7px 7px 5px;overflow:hidden;min-height:0;}
.col{display:flex;flex-direction:column;gap:7px;overflow:hidden;min-height:0;}

/* CARDS */
.card{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:7px 9px;overflow:hidden;min-height:0;}
.card-title{font-size:9px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin-bottom:5px;padding-bottom:4px;border-bottom:1px solid var(--border);flex-shrink:0;}
.card-title.gas{color:var(--gas);border-color:var(--gas);}

/* TREND BADGES */
.trend-badge{font-size:16px;font-weight:700;text-align:center;padding:4px 8px;border-radius:5px;margin-bottom:4px;letter-spacing:1px;}
.trend-long   {background:rgba(46,204,113,0.12);color:var(--green);border:1px solid var(--green);}
.trend-short  {background:rgba(231,76,60,0.12); color:var(--red);  border:1px solid var(--red);}
.trend-neutral{background:rgba(243,156,18,0.12);color:var(--amber);border:1px solid var(--amber);}

/* INDICATOR ROWS */
.ind-row{display:flex;justify-content:space-between;align-items:center;padding:2px 0;border-bottom:1px solid var(--bg3);font-size:11px;}
.ind-row:last-child{border-bottom:none;}
.ind-label{color:var(--muted);}
.ind-val{font-weight:600;}
.bull{color:var(--green);}.bear{color:var(--red);}.neut{color:var(--amber);}.gas{color:var(--gas);}

/* LIQUIDITY PERIOD */
.phase-badge{display:inline-block;padding:3px 9px;border-radius:3px;font-size:12px;font-weight:700;letter-spacing:0.5px;}
.liq-ASIAN   {background:rgba(243,156,18,0.12);color:var(--amber);}
.liq-LONDON  {background:rgba(52,152,219,0.15);color:#3498db;}
.liq-OVERLAP {background:rgba(46,204,113,0.12);color:var(--green);}
.liq-NEW_YORK{background:rgba(46,204,113,0.15);color:var(--green);}
.liq-CLOSING {background:rgba(230,126,34,0.15);color:#e67e22;}
.liq-CLOSED  {background:rgba(85,85,85,0.15);color:#666;}
.liq-note{color:var(--muted);font-size:10px;line-height:1.4;margin-top:6px;}
.countdown{font-family:monospace;font-size:20px;font-weight:700;color:var(--gas);}
.countdown.amber{color:var(--amber);}
.countdown.green{color:var(--green);}
.last-updated{color:var(--muted);font-size:10px;margin-top:4px;}

/* DECISION -- full width centre card */
.decision-big{font-size:30px;font-weight:800;text-align:center;padding:10px;border-radius:7px;letter-spacing:3px;margin-bottom:8px;}
.dec-long {background:rgba(46,204,113,0.1);color:var(--green);border:2px solid var(--green);}
.dec-short{background:rgba(231,76,60,0.1); color:var(--red);  border:2px solid var(--red);}
.dec-hold {background:rgba(255,215,0,0.1);color:var(--gas); border:2px solid var(--gas);}
.dec-stay {background:rgba(136,136,136,0.1);color:var(--muted);border:2px solid var(--border);}
.dec-meta{text-align:center;color:var(--muted);font-size:12px;margin-bottom:9px;}
.dec-meta span{color:var(--text);font-weight:600;}
.reasoning{background:var(--bg3);border-left:3px solid var(--gas);padding:10px 14px;border-radius:0 5px 5px 0;font-size:13px;line-height:1.55;margin-bottom:7px;}
.block-reason{background:rgba(231,76,60,0.07);border-left:3px solid var(--red);padding:10px 14px;border-radius:0 5px 5px 0;font-size:13px;line-height:1.55;color:var(--red);margin-bottom:7px;}
.warnings{display:flex;flex-direction:column;gap:4px;margin-top:5px;}
.warn-item{background:rgba(243,156,18,0.08);border:1px solid rgba(243,156,18,0.3);border-radius:3px;padding:4px 9px;font-size:11px;color:var(--amber);}
.dec-assess{color:var(--muted);font-size:10px;margin-top:6px;line-height:1.5;}
.dec-checklist{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px;}
.dec-chk{font-size:10px;padding:2px 7px;border-radius:3px;}
.dec-chk-pass{background:rgba(46,204,113,0.1);color:var(--green);border:1px solid rgba(46,204,113,0.35);}
.dec-chk-fail{background:rgba(231,76,60,0.08);color:var(--red);border:1px solid rgba(231,76,60,0.35);}

/* PERFORMANCE */
.score-bar{background:var(--bg3);border-radius:3px;height:6px;flex:1;}
.score-fill{height:100%;border-radius:3px;transition:width 0.4s;}
.score-high{background:var(--green);}.score-med{background:var(--amber);}.score-low{background:var(--red);}
.perf-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 2px;}
.perf-win{background:var(--green);}.perf-loss{background:var(--red);}

/* POSITION */
.pos-card{background:var(--bg3);border-radius:5px;padding:7px;font-size:11px;}
.pos-long {border-left:3px solid var(--green);}
.pos-short{border-left:3px solid var(--red);}
.pos-none {border-left:3px solid var(--border);color:var(--muted);text-align:center;padding:9px;}
.pos-row{display:flex;justify-content:space-between;padding:2px 5px;}

/* CHECK ITEMS */
.check-item{display:flex;align-items:center;gap:6px;padding:2px 0;border-bottom:1px solid var(--bg3);font-size:11px;}
.check-item:last-child{border-bottom:none;}
.check-pass{color:var(--green);font-weight:700;min-width:30px;font-size:10px;}
.check-fail{color:var(--red);  font-weight:700;min-width:30px;font-size:10px;}
.check-na  {color:var(--muted);font-weight:700;min-width:30px;font-size:10px;}
.check-lbl {color:var(--text);}

/* SYSTEM STATUS */
.sys-row{display:flex;justify-content:space-between;padding:2px 0;font-size:11px;border-bottom:1px solid var(--bg3);}
.sys-row:last-child{border-bottom:none;}
.sys-lbl{color:var(--muted);}

/* STAY OUT QUALITY */
.soq-summary{font-size:12px;font-weight:600;color:var(--text);padding:2px 0 4px;}
.soq-counts{font-size:11px;color:var(--muted);padding:1px 0;}
.soq-rows{margin-top:6px;border-top:1px solid var(--bg3);}
.soq-row{display:flex;justify-content:space-between;gap:8px;padding:2px 0;font-size:11px;border-bottom:1px solid var(--bg3);}
.soq-row:last-child{border-bottom:none;}

/* KILL STATUS */
.kill-ok    {background:rgba(46,204,113,0.08);border:1px solid rgba(46,204,113,0.3);border-radius:4px;padding:3px 8px;color:var(--green);font-size:10px;text-align:center;flex-shrink:0;}
.kill-active{background:rgba(231,76,60,0.1); border:1px solid var(--red);          border-radius:4px;padding:4px 8px;color:var(--red);  font-size:10px;font-weight:700;text-align:center;flex-shrink:0;}

/* PAGE 2 */
.p2-content{padding:8px 12px 20px;display:flex;flex-direction:column;gap:10px;}
.p2-card{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:12px 16px;}
.p2-card .card-title{font-size:9px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border);}
.p2-account-bar{display:grid;grid-template-columns:repeat(7,1fr);gap:6px 6px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:10px 16px;text-align:center;}
.acc-lbl{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:2px;}
.acc-val{font-size:14px;font-weight:700;}
.acc-bal{color:var(--gas);font-size:16px;}
.win{color:var(--green);font-weight:600;}.loss{color:var(--red);font-weight:600;}
.dir-long{color:var(--green);font-weight:700;}.dir-short{color:var(--red);font-weight:700;}
.p2-stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-bottom:10px;}
.p2-stat-box{background:var(--bg3);border-radius:5px;padding:9px 12px;text-align:center;}
.p2-stat-label{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;}
.p2-stat-val{font-size:16px;font-weight:700;}
.p2-stat-sub{font-size:10px;color:var(--muted);margin-top:3px;}
.p2-section-hdr{font-size:9px;font-weight:600;letter-spacing:1.2px;text-transform:uppercase;color:var(--muted);margin:10px 0 5px;padding-bottom:3px;border-bottom:1px solid var(--bg3);}
.p2-table{width:100%;border-collapse:collapse;font-size:12px;}
.p2-table th{text-align:left;padding:5px 8px;font-size:9px;letter-spacing:1px;text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--border);}
.p2-table td{padding:5px 8px;border-bottom:1px solid var(--bg3);font-family:monospace;}
.p2-table tr:last-child td{border-bottom:none;}
.p2-table tr.tr-win td{background:rgba(46,204,113,0.04);}
.p2-table tr.tr-loss td{background:rgba(231,76,60,0.04);}
.month-best td{background:rgba(46,204,113,0.09)!important;}
.month-worst td{background:rgba(231,76,60,0.07)!important;}
.cons-warn{margin-top:8px;padding:5px 9px;background:rgba(231,76,60,0.1);border:1px solid var(--red);border-radius:3px;font-size:10px;color:var(--red);font-weight:700;}
</style>
</head>
<body>

<!-- SHUTDOWN MODAL -->
<div class="modal-overlay" id="shutdownModal">
  <div class="modal">
    <h3>Shut Down GasTrader AI?</h3>
    <p>This will stop the trading engine and close the dashboard.</p>
    <div class="modal-trade-warn" id="tradeWarn" style="display:none">
      WARNING: A position is currently OPEN!<br>
      You must manually close this position via Capital.com.<br>
      The system will NOT close it automatically.
    </div>
    <p>Are you sure you want to shut down?</p>
    <div class="modal-btns">
      <button class="btn-cancel"  onclick="closeModal()">Cancel &mdash; Keep Running</button>
      <button class="btn-confirm" onclick="confirmShutdown()">Yes &mdash; Shut Down</button>
    </div>
  </div>
</div>

<!-- SHARED HEADER -->
<div class="header">
  <div class="header-brand">
    <div>
      <div class="logo-line"><div class="logo">NATURALGAS<span>TRADER</span> A.I.</div><span class="hdr-version" id="hdrVersion">__VERSION_STRING__</span></div>
      <div class="subtitle">Natural Gas &mdash; Capital.com</div>
    </div>
  </div>
  <div class="header-price">
    <div class="hdr-price-lbl">Natural Gas (USD)</div>
    <div class="hdr-price-val" id="hdrPrice">--</div>
  </div>
  <div class="header-right">
    <div class="excalibur-status" id="excaliburStatus">Excalibur: --</div>
    <button class="nav-btn" id="btnToP2" onclick="showPage(2)">P&amp;L &rarr;</button>
    <button class="nav-btn" id="btnToP1" onclick="showPage(1)" style="display:none;">&larr; Trading</button>
    <button class="shutdown-btn" onclick="openModal()">&#9211; Shutdown</button>
    <div class="clock" id="clock">--:--:-- UTC</div>
  </div>
</div>

<!-- PAGE 1: TRADING DASHBOARD -->
<div id="page1" class="page-wrap">
  <div class="main" id="main-grid">
    <div style="grid-column:1/-1;color:var(--muted);padding:40px;text-align:center">Loading GasTrader AI...</div>
  </div>
</div>

<!-- PAGE 2: PERFORMANCE & P&L -->
<div id="page2" class="page-wrap" style="display:none;">
  <div class="p2-content">
    <div class="p2-account-bar" id="p2-account-bar">
      <div style="color:var(--muted);font-size:11px;grid-column:1/-1;text-align:center;">Loading...</div>
    </div>
    <div class="p2-card" id="p2-perf-detail">
      <div class="card-title gas">Arthur Self-Performance &mdash; Detail</div>
      <div style="color:var(--muted);font-size:11px;">Loading...</div>
    </div>
    <div class="p2-card" id="p2-monthly">
      <div class="card-title">Monthly Breakdown</div>
      <div style="color:var(--muted);font-size:11px;">Loading...</div>
    </div>
    <div class="p2-card" id="p2-trades">
      <div class="card-title">Gas Trade History</div>
      <div style="color:var(--muted);font-size:11px;">Loading...</div>
    </div>
  </div>
</div>

<script>
var _currentPage = 1;
var hasOpenPosition = false;
var _newsData = null;

/* -- Clock ---------------------------------------------------------------- */
function updateClock(){
  var t = new Date();
  document.getElementById('clock').textContent =
    String(t.getUTCHours()).padStart(2,'0') + ':' +
    String(t.getUTCMinutes()).padStart(2,'0') + ':' +
    String(t.getUTCSeconds()).padStart(2,'0') + ' UTC';
}
setInterval(updateClock, 1000);
updateClock();

/* -- Countdown to next liquidity-period boundary -------------------------- */
function updateLiquidityCountdown(){
  var el = document.getElementById('countdown');
  if(!el) return;
  var now = new Date();
  var nowSec = now.getUTCHours()*3600 + now.getUTCMinutes()*60 + now.getUTCSeconds();
  var boundaries = [
    {t: 8*3600,  name:'LONDON'},
    {t: 13*3600, name:'OVERLAP'},
    {t: 17*3600, name:'NEW YORK'},
    {t: 22*3600, name:'CLOSED'}
  ];
  var next = null;
  for(var i=0;i<boundaries.length;i++){ if(boundaries[i].t > nowSec){ next = boundaries[i]; break; } }
  if(!next){ next = {t: 24*3600, name:'ASIAN'}; }   // after 22:00 -> Asia open 00:00 tomorrow
  var rem = next.t - nowSec;
  var h = Math.floor(rem/3600);
  var m = Math.floor((rem%3600)/60);
  var s = rem%60;
  var txt = (h>0 ? h + ':' + String(m).padStart(2,'0') : String(m)) + ':' + String(s).padStart(2,'0');
  el.textContent = next.name + ' in ' + txt;
  el.className = 'countdown' + (rem <= 60 ? ' green' : rem <= 300 ? ' amber' : '');
}
setInterval(updateLiquidityCountdown, 1000);

/* -- Page switching ------------------------------------------------------- */
function showPage(n){
  var p1 = document.getElementById('page1');
  var p2 = document.getElementById('page2');
  var b1 = document.getElementById('btnToP1');
  var b2 = document.getElementById('btnToP2');
  if(n === 2){
    p1.style.display = 'none';
    p2.style.display = 'flex';
    b1.style.display = 'inline-block';
    b2.style.display = 'none';
  } else {
    p1.style.display = 'flex';
    p2.style.display = 'none';
    b1.style.display = 'none';
    b2.style.display = 'inline-block';
  }
  _currentPage = n;
}

/* -- Shutdown modal ------------------------------------------------------- */
function openModal(){
  document.getElementById('tradeWarn').style.display = hasOpenPosition ? 'block' : 'none';
  document.getElementById('shutdownModal').classList.add('open');
}
function closeModal(){
  document.getElementById('shutdownModal').classList.remove('open');
}
function confirmShutdown(){
  fetch('/api/shutdown', {method:'POST'})
    .then(function(){
      document.body.innerHTML = '<div style="display:flex;height:100vh;align-items:center;justify-content:center;background:#0d0d0d;color:#228B22;font-family:monospace;font-size:18px;">GasTrader AI shut down. You may close this window.</div>';
    })
    .catch(function(){ closeModal(); });
}

/* -- Formatting helpers --------------------------------------------------- */
function fmt(v, dp){
  dp = (dp === undefined) ? 2 : dp;
  if(v === null || v === undefined || v !== v) return '--';
  return parseFloat(v).toFixed(dp);
}
function fmtPnl(v){
  if(v === null || v === undefined || v !== v) return '--';
  var n = parseFloat(v);
  return (n >= 0 ? '+' : '') + n.toFixed(2);
}
function fmtUsd(v){
  if(v === null || v === undefined || v !== v) return '--';
  return '$' + parseFloat(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
}
function trendClass(t){
  if(!t) return 'trend-neutral'; t = t.toUpperCase();
  if(t.indexOf('LONG') >= 0 || t.indexOf('BULL') >= 0) return 'trend-long';
  if(t.indexOf('SHORT') >= 0 || t.indexOf('BEAR') >= 0) return 'trend-short';
  return 'trend-neutral';
}
function trendLabel(t){
  if(!t) return 'NEUTRAL'; t = t.toUpperCase();
  if(t.indexOf('LONG') >= 0 || t.indexOf('BULL') >= 0) return 'LONG';
  if(t.indexOf('SHORT') >= 0 || t.indexOf('BEAR') >= 0) return 'SHORT';
  return 'NEUTRAL';
}
function decClass(d){
  if(!d) return 'dec-stay';
  if(d.indexOf('LONG') >= 0) return 'dec-long';
  if(d.indexOf('SHORT') >= 0) return 'dec-short';
  if(d === 'HOLD') return 'dec-hold';
  return 'dec-stay';
}
function indCls(v, thresh){
  thresh = thresh || 0; var n = parseFloat(v);
  if(isNaN(n)) return 'neut';
  return n > thresh ? 'bull' : n < thresh ? 'bear' : 'neut';
}
function sslCls(v){ return v ? 'bull' : 'bear'; }
function sslLbl(v){ return v ? 'BULL' : 'BEAR'; }
function liqNote(p){
  var notes = {
    'ASIAN':'Thin & choppy -- low volume, whippy price',
    'LONDON':'London open -- trends start to form',
    'OVERLAP':'London/NY overlap -- best conditions',
    'NEW_YORK':'New York -- highest volume of the day',
    'CLOSING':'Closing -- avoid new entries',
    'CLOSED':'Market closed -- no trading'
  };
  return notes[p] || '--';
}

/* -- Left column: Daily / 1-Hour / 5-Minute + Liquidity cards ------------- */
function buildLeftCol(trend1d, trend1h, signal5m, ind1d, ind1h, ind5m, liq, updatedAt){
  var liqLabel = (liq || 'CLOSED').replace(/_/g,' ');
  return '<div class="col">' +
    '<div class="card" style="flex-shrink:0"><div class="card-title gas">Daily Trend</div>' +
    '<div class="trend-badge ' + trendClass(trend1d) + '">' + trendLabel(trend1d) + '</div>' +
    '<div class="ind-row"><span class="ind-label">SSL</span><span class="ind-val ' + sslCls(ind1d.ssl_bull) + '">' + sslLbl(ind1d.ssl_bull) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">RSI</span><span class="ind-val ' + indCls(ind1d.rsi,50) + '">' + fmt(ind1d.rsi,1) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">Filter</span><span class="ind-val ' + (trendLabel(trend1d)==='LONG'?'bull':trendLabel(trend1d)==='SHORT'?'bear':'neut') + '">' +
    (trendLabel(trend1d)==='LONG'?'LONG only':trendLabel(trend1d)==='SHORT'?'SHORT only':'Both') + '</span></div>' +
    '</div>' +
    '<div class="card" style="flex-shrink:0"><div class="card-title gas">1-Hour Trend</div>' +
    '<div class="trend-badge ' + trendClass(trend1h) + '">' + trendLabel(trend1h) + '</div>' +
    '<div class="ind-row"><span class="ind-label">SSL Cloud</span><span class="ind-val ' + sslCls(ind1h.ssl_bull) + '">' + sslLbl(ind1h.ssl_bull) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">RSI</span><span class="ind-val ' + indCls(ind1h.rsi,50) + '">' + fmt(ind1h.rsi,1) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">MACD</span><span class="ind-val ' + indCls(ind1h.macd) + '">' + fmt(ind1h.macd,2) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">TMO</span><span class="ind-val ' + indCls(ind1h.tmo_main) + '">' + fmt(ind1h.tmo_main,3) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">Chande MO</span><span class="ind-val ' + indCls(ind1h.chande_mo) + '">' + fmt(ind1h.chande_mo,1) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">Money Flow</span><span class="ind-val ' + indCls(ind1h.money_flow) + '">' + fmt(ind1h.money_flow,4) + '</span></div>' +
    '</div>' +
    '<div class="card" style="flex-shrink:0"><div class="card-title gas">5-Minute Signal</div>' +
    '<div class="trend-badge ' + trendClass(signal5m) + '">' + trendLabel(signal5m) + '</div>' +
    '<div class="ind-row"><span class="ind-label">SSL Cloud</span><span class="ind-val ' + sslCls(ind5m.ssl_bull) + '">' + sslLbl(ind5m.ssl_bull) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">RSI</span><span class="ind-val ' + indCls(ind5m.rsi,50) + '">' + fmt(ind5m.rsi,1) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">MACD</span><span class="ind-val ' + indCls(ind5m.macd) + '">' + fmt(ind5m.macd,2) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">TMO</span><span class="ind-val ' + indCls(ind5m.tmo_main) + '">' + fmt(ind5m.tmo_main,3) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">Chande MO</span><span class="ind-val ' + indCls(ind5m.chande_mo) + '">' + fmt(ind5m.chande_mo,1) + '</span></div>' +
    '<div class="ind-row"><span class="ind-label">Money Flow</span><span class="ind-val ' + indCls(ind5m.money_flow) + '">' + fmt(ind5m.money_flow,4) + '</span></div>' +
    '</div>' +
    '<div class="card" style="flex:1"><div class="card-title gas">Liquidity Period</div>' +
    '<div class="phase-badge liq-' + (liq||'CLOSED') + '">' + liqLabel + '</div>' +
    '<div class="liq-note">' + liqNote(liq) + '</div>' +
    '<div id="countdown" class="countdown" style="margin-top:8px;">-- in --:--</div>' +
    '<div class="last-updated">Last updated: ' + (updatedAt || '--') + '</div>' +
    '</div>' +
    '</div>';
}

/* -- Performance card (Page 1, compact) ----------------------------------- */
function renderPerfCard(perf){
  var total = perf ? (perf.total_trades || 0) : 0;
  if(total === 0){
    return '<div class="card"><div class="card-title gas">Arthur Self-Performance</div>' +
      '<div style="color:var(--muted);font-size:11px;text-align:center;padding:8px 0">No trades yet -- system ready</div></div>';
  }
  var score  = perf.confidence_score || 50;
  var level  = perf.confidence_level || 'MEDIUM';
  var sc     = level==='HIGH' ? 'score-high' : (level==='LOW'||level==='VERY_LOW') ? 'score-low' : 'score-med';
  var lc     = level==='HIGH' ? 'bull'       : (level==='LOW'||level==='VERY_LOW') ? 'bear'      : 'neut';
  var stType = perf.streak_type  || '';
  var stCnt  = perf.streak_count || 0;
  var stCol  = stType==='WIN' ? 'var(--green)' : stType==='LOSS' ? 'var(--red)' : 'var(--muted)';
  var stStr  = stCnt > 0 ? (stCnt + ' ' + (stType==='WIN'?'WIN':'LOSS') + (stCnt>1?'S':'')) : '--';
  var r5     = perf.recent_5 || [];
  var dots   = r5.map(function(r){ return '<span class="perf-dot ' + (r==='WIN'?'perf-win':'perf-loss') + '"></span>'; }).join('');
  var cons   = perf.conservative
    ? '<div style="margin-top:4px;padding:3px 6px;background:rgba(231,76,60,0.1);border:1px solid var(--red);border-radius:3px;font-size:10px;color:var(--red);font-weight:700;">CONSERVATIVE MODE -- STAY OUT</div>'
    : '';
  return '<div class="card"><div class="card-title gas">Arthur Self-Performance</div>' +
    '<div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">' +
    '<span style="font-size:10px;color:var(--muted);min-width:60px">Confidence</span>' +
    '<div class="score-bar"><div class="score-fill ' + sc + '" style="width:' + score + '%"></div></div>' +
    '<span class="' + lc + '" style="font-size:12px;font-weight:700;min-width:80px;text-align:right">' + score + '/100 ' + level + '</span></div>' +
    '<div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">' +
    '<span style="font-size:10px;color:var(--muted);min-width:60px">Last ' + r5.length + '</span>' +
    (dots || '<span style="color:var(--muted);font-size:10px">No trades</span>') + '</div>' +
    '<div style="display:flex;gap:14px;font-size:11px;color:var(--muted);">' +
    '<span>Streak: <strong style="color:' + stCol + '">' + stStr + '</strong></span>' +
    '<span>Trades: <strong style="color:var(--gas)">' + total + '</strong></span>' +
    '<span>WR: <strong style="color:var(--text)">' + fmt(perf.win_rate,1) + '%</strong></span>' +
    '</div>' + cons + '</div>';
}

/* -- STAY OUT QUALITY panel ------------------------------------------------ */
function renderStayOutQuality(d){
  var q = d.stay_out_quality || {};
  var title = '<div class="card-title gas">STAY OUT QUALITY</div>';
  if(q.status !== 'ok'){
    return '<div class="card" style="flex-shrink:0">' + title +
      '<div style="color:var(--muted);font-size:11px;">Awaiting first decisions</div></div>';
  }
  var decisions = q.decisions || [];
  var qs = (q.quality_score == null) ? 0 : q.quality_score;
  var netSaved  = (q.net_saved  == null) ? 0 : q.net_saved;
  var netMissed = (q.net_missed == null) ? 0 : q.net_missed;

  var summary =
    '<div class="soq-summary">Last 10 decisions &nbsp; Quality: <span class="gas">' + qs + '%</span></div>' +
    '<div class="soq-counts">✅ Correct: ' + (q.correct||0) + ' &nbsp; ❌ Wrong: ' + (q.wrong||0) + ' &nbsp; ➖ Neutral: ' + (q.neutral||0) + '</div>' +
    '<div class="soq-counts">Net Saved: <span class="bull">+&pound;' + fmt(netSaved,2) + '</span> &nbsp; Net Missed: <span class="bear">-&pound;' + fmt(Math.abs(netMissed),2) + '</span></div>';

  var rowsHTML = '';
  if(decisions.length > 0){
    rowsHTML = '<div class="soq-rows">' + decisions.slice(0,10).map(function(r){
      var v = r.verdict || '';
      var icon = (v === 'CORRECT') ? '✅' : (v === 'WRONG') ? '❌' : '➖';
      var cls  = (v === 'CORRECT') ? 'bull' : (v === 'WRONG') ? 'bear' : 'neut';
      var pnl  = parseFloat(r.pnl_1hr);
      var pnlTxt = isNaN(pnl) ? '--' : (pnl >= 0 ? '+' : '') + fmt(pnl,2);
      var ts = r.timestamp || r.time || '';
      return '<div class="soq-row"><span class="' + cls + '">' + icon + ' ' + (v||'--') + '</span>' +
             '<span style="color:var(--muted)">' + ts + '</span>' +
             '<span class="' + cls + '">' + pnlTxt + '</span></div>';
    }).join('') + '</div>';
  }

  return '<div class="card" style="flex-shrink:0">' + title + summary + rowsHTML + '</div>';
}

/* -- GUINEVERE NEWS panel (polls /api/news every 60s) --------------------- */
function renderNewsCard(n){
  var title = '<div class="card-title gas">GUINEVERE NEWS</div>';
  if(!n){
    return '<div class="card" id="newsCard" style="flex-shrink:0">' + title +
      '<div style="color:var(--muted);font-size:11px;">Loading gas news...</div></div>';
  }
  var sent  = n.sentiment || 'NEUTRAL';
  var scls  = sent === 'BULLISH' ? 'bull' : sent === 'BEARISH' ? 'bear' : 'neut';
  var score = (n.score === undefined || n.score === null) ? 0 : n.score;
  var scoreTxt = (score > 0 ? '+' : '') + score;
  var reason = n.reason || '';
  var noKey  = (reason.indexOf('No API key') >= 0);

  var body;
  if(noKey){
    body = '<div style="color:var(--muted);font-size:11px;line-height:1.5;">' + reason + '</div>';
  } else {
    var hl = n.headlines || [];
    var hlHTML = '';
    if(hl.length > 0){
      hlHTML = '<div class="soq-rows">' + hl.slice(0,5).map(function(h){
        var hs   = (h.score === undefined || h.score === null) ? 0 : h.score;
        var hcls = hs > 0 ? 'bull' : hs < 0 ? 'bear' : 'neut';
        var hst  = (hs > 0 ? '+' : '') + hs;
        var ttl  = (h.title || '').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        return '<div class="soq-row"><span class="check-lbl" style="flex:1;overflow:hidden;text-overflow:ellipsis;">' + ttl + '</span>' +
               '<span class="' + hcls + '">' + hst + '</span></div>';
      }).join('') + '</div>';
    } else {
      hlHTML = '<div style="color:var(--muted);font-size:10px;margin-top:4px;">' + (reason || 'No recent headlines') + '</div>';
    }
    body =
      '<div class="soq-summary">Sentiment: <span class="' + scls + '">' + sent + '</span>' +
      ' &nbsp; Score: <span class="' + scls + '">' + scoreTxt + '</span></div>' +
      '<div class="last-updated">Updated: ' + (n.updated_at || '--') + '</div>' +
      hlHTML;
  }
  var eiaCol  = n.eia_active ? 'var(--amber)' : 'var(--muted)';
  var eiaTxt  = n.eia_active ? (n.eia_reason || 'EIA inventory window active') : 'No EIA inventory window';
  var eiaHTML = '<div class="last-updated" style="margin-top:6px;color:' + eiaCol + '">EIA: ' + eiaTxt + '</div>';

  return '<div class="card" id="newsCard" style="flex-shrink:0">' + title + body + eiaHTML + '</div>';
}

function pollNews(){
  fetch('/api/news')
    .then(function(r){ return r.json(); })
    .then(function(n){
      _newsData = n;
      var el = document.getElementById('newsCard');
      if(el){ el.outerHTML = renderNewsCard(n); }
    })
    .catch(function(e){ console.error('News poll error:', e); });
}

/* -- Right panel: system status, pre-checks/checklist, calendar ----------- */
function renderRightPanel(d){
  var mode = d.panel_mode || 'pre_checks';

  var connOk = d.connector_status === 'capitalcom';
  var killHTML = d.kill_switch
    ? '<div class="kill-active">KILL SWITCH ACTIVE<br><small>Tier ' + (d.kill_tier||1) + '</small></div>'
    : '<div class="kill-ok">System OK -- Trading Active</div>';

  var sysHTML = '<div class="card" style="flex-shrink:0"><div class="card-title gas">System Status</div>' +
    '<div class="sys-row"><span class="sys-lbl">Mode</span><span class="gas">' + (d.mode||'--') + '</span></div>' +
    '<div class="sys-row"><span class="sys-lbl">Version</span><span>' + (d.version||'--') + '</span></div>' +
    '<div class="sys-row"><span class="sys-lbl">Connector</span><span class="' + (connOk?'bull':'neut') + '">' + (connOk?'Capital.com':'Yahoo (fallback)') + '</span></div>' +
    '<div class="sys-row"><span class="sys-lbl">GBPUSD</span><span>' + fmt(d.gbpusd_rate,4) + '</span></div>' +
    '<div style="margin-top:6px">' + killHTML + '</div>' +
    '</div>';

  var panelHTML = '';
  if(mode === 'claude'){
    var cl = d.checklist || {}; var items = Object.keys(cl);
    panelHTML = '<div class="card" style="flex:1;display:flex;flex-direction:column;">' +
      '<div class="card-title gas">Arthur Checklist</div>' +
      (items.length > 0
        ? items.map(function(k){
            var v = cl[k];
            return '<div class="check-item"><span class="' + (v ? 'check-pass' : 'check-fail') + '">' +
              (v ? 'PASS' : 'FAIL') + '</span><span class="check-lbl">' + k.replace(/_/g,' ') + '</span></div>';
          }).join('')
        : '<div style="color:var(--muted);font-size:11px;">No checklist yet</div>') +
      '</div>';
  } else {
    var checks = d.pre_checks || {}; var keys = Object.keys(checks);
    var chtml = keys.map(function(k){
      var v = checks[k]; var cls, icon;
      if(v === true){cls='check-pass';icon='PASS';}
      else if(v === false){cls='check-fail';icon='FAIL';}
      else{cls='check-na';icon='N/A';}
      return '<div class="check-item"><span class="' + cls + '">' + icon + '</span><span class="check-lbl">' + k.replace(/_/g,' ') + '</span></div>';
    }).join('');
    panelHTML = '<div class="card" style="flex:1;display:flex;flex-direction:column;">' +
      '<div class="card-title gas">Lancelot -- Pre-Checks</div>' +
      (chtml || '<div style="color:var(--muted);font-size:11px;">Waiting for first tick...</div>') +
      '</div>';
  }

  var calText = d.calendar || 'Loading...';
  var calHTML = '<div class="card" style="flex-shrink:0"><div class="card-title gas">Guinevere -- Gas Calendar</div>' +
    '<div style="color:var(--text);font-size:11px;line-height:1.5;">' + calText + '</div></div>';

  return sysHTML + renderStayOutQuality(d) + renderNewsCard(_newsData) + panelHTML + calHTML;
}

/* -- Page 1: trading dashboard -------------------------------------------- */
function renderPage1(d){
  var trend1d  = d.trend_1d   || 'NEUTRAL';
  var trend1h  = d.trend_1h   || 'NEUTRAL';
  var signal5m = d.signal_5m  || 'NEUTRAL';
  var decision = (d.decision && d.decision.decision) || 'STAY_OUT';
  var dec      = d.decision || {};
  var pos      = d.current_trade || null;
  var ind1h    = d.indicators_1h || {};
  var ind5m    = d.indicators_5m || {};
  var ind1d    = d.indicators_1d || {};
  var warnings = dec.warnings || [];
  var mode     = d.panel_mode || 'pre_checks';

  hasOpenPosition = !!(d.in_trade && pos);

  var hdrEl = document.getElementById('hdrPrice');
  if(hdrEl){ hdrEl.textContent = fmtUsd(d.gas_price_usd || 0); }

  var excaliburEl = document.getElementById('excaliburStatus');
  if(excaliburEl){
    if(d.connector_status === 'capitalcom'){
      excaliburEl.textContent = 'Excalibur: Capital.com';
      excaliburEl.style.color = 'var(--green)';
    } else {
      excaliburEl.textContent = 'Excalibur: Yahoo Finance (fallback)';
      excaliburEl.style.color = 'var(--amber)';
    }
  }

  var decText = decision.replace('ENTER_','').replace('EXIT_','EXIT ').replace(/_/g,' ');
  if(decision === 'STAY_OUT') decText = 'STAY OUT';

  var reasoning   = dec.reasoning || 'Waiting for next analysis cycle...';
  var blockReason = (d.pre_checks_reason) || '';
  var reasonBox = (blockReason && mode === 'pre_checks')
    ? '<div class="block-reason">' + blockReason + '</div>'
    : '<div class="reasoning">' + reasoning + '</div>';

  var warnHTML = (warnings.length > 0)
    ? '<div class="warnings">' + warnings.map(function(w){ return '<div class="warn-item">'+w+'</div>'; }).join('') + '</div>'
    : '';

  var assessHTML = '';
  if(dec.liquidity_assessment || dec.calendar_assessment){
    assessHTML = '<div class="dec-assess">' +
      (dec.liquidity_assessment ? 'Liquidity: ' + dec.liquidity_assessment + '<br>' : '') +
      (dec.calendar_assessment  ? 'Calendar: ' + dec.calendar_assessment : '') +
      '</div>';
  }

  var chkHTML = '';
  var dcl = dec.checklist || {};
  var dclKeys = Object.keys(dcl);
  if(dclKeys.length > 0){
    chkHTML = '<div class="dec-checklist">' + dclKeys.map(function(k){
      var v = dcl[k];
      return '<span class="dec-chk ' + (v?'dec-chk-pass':'dec-chk-fail') + '">' + (v?'✓ ':'✗ ') + k.replace(/_/g,' ') + '</span>';
    }).join('') + '</div>';
  }

  var metaExtra = '';
  if(dec.tokens_used || dec.timestamp){
    metaExtra = '<div class="dec-assess" style="text-align:center">' +
      (dec.tokens_used ? 'Tokens: ' + dec.tokens_used + ' ' : '') +
      (dec.timestamp ? '&nbsp; ' + dec.timestamp : '') + '</div>';
  }

  function buildPosHTML(p, currentPrice){
    if(!p) return '<div class="pos-card pos-none">No open position<br><span style="font-size:10px">Watching for setup...</span></div>';
    var direction = p.direction || '--';
    var pc = direction==='LONG' ? 'pos-long' : 'pos-short';
    var dc = direction==='LONG' ? 'bull' : 'bear';
    var entry = parseFloat(p.entry_price);
    var cur   = parseFloat(currentPrice);
    var dir   = (p.direction||'').toUpperCase();
    var points = (isNaN(entry)||isNaN(cur)||cur===0) ? null : (dir==='SHORT' ? entry-cur : cur-entry);
    var stake = parseFloat(p.stake);
    var rate  = parseFloat(p.gbpusd_rate);
    var fgbp = (points===null||isNaN(stake)) ? null : points*stake;
    var fusd = (fgbp===null||isNaN(rate)) ? null : fgbp*rate;
    var pnlcls = (fgbp===null) ? '' : (fgbp >= 0 ? 'bull' : 'bear');
    var pointsTxt = (points===null ? '---' : (points>=0?'+':'')+points.toFixed(1));
    return '<div class="pos-card ' + pc + '">' +
      '<div class="pos-row"><span class="' + dc + '" style="font-weight:700">' + direction + '</span>' +
      '<span style="color:var(--muted)">' + (p.entry_time||'') + '</span></div>' +
      '<div class="pos-row"><span style="color:var(--muted)">Entry</span><span>' + fmtUsd(p.entry_price) + '</span></div>' +
      '<div class="pos-row"><span style="color:var(--muted)">Stop</span><span class="bear">' + fmtUsd(p.stop_loss) + '</span></div>' +
      '<div class="pos-row"><span style="color:var(--muted)">Target</span><span class="bull">' + fmtUsd(p.take_profit) + '</span></div>' +
      (p.exit_price ? '<div class="pos-row"><span style="color:var(--muted)">Exit</span><span>' + fmtUsd(p.exit_price) + '</span></div>' : '') +
      '<div class="pos-row"><span style="color:var(--muted)">Size</span><span>' + fmt(p.size_oz,2) + ' oz</span></div>' +
      '<div class="pos-row"><span style="color:var(--muted)">Stake</span><span>&pound;' + fmt(p.stake,4) + '/pt</span></div>' +
      '<div class="pos-row"><span style="color:var(--muted)">Points</span><span class="' + pnlcls + '">' + pointsTxt + '</span></div>' +
      '<div class="pos-row"><span style="color:var(--muted)">P&amp;L (USD)</span><span class="' + pnlcls + '">' + (fusd===null ? '---' : fmtPnl(fusd)) + '</span></div>' +
      '<div class="pos-row"><span style="color:var(--muted)">P&amp;L (GBP)</span><span class="' + pnlcls + '">&pound;' + (fgbp===null ? '---' : fmtPnl(fgbp)) + '</span></div>' +
      '<div class="pos-row"><span style="color:var(--muted)">GBPUSD</span><span>' + fmt(p.gbpusd_rate,4) + '</span></div>' +
      '<div class="pos-row"><span style="color:var(--muted)">Liquidity</span><span>' + (p.liquidity_period||'--') + '</span></div>' +
      '</div>';
  }

  var leftCol = buildLeftCol(trend1d, trend1h, signal5m, ind1d, ind1h, ind5m, d.liquidity_period, d.updated_at);

  var centreCol = '<div class="col">' +
    '<div class="card" style="flex-shrink:0"><div class="card-title gas">Arthur &mdash; AI Decision</div>' +
    '<div class="decision-big ' + decClass(decision) + '">' + decText + '</div>' +
    '<div class="dec-meta">Confidence: <span>' + (dec.confidence||'--') + '</span> &nbsp;|&nbsp; Liquidity Bias: <span>' + (dec.liquidity_bias||'--') + '</span></div>' +
    reasonBox + warnHTML + assessHTML + chkHTML + metaExtra +
    '</div>' +
    renderPerfCard(d.perf || {}) +
    '<div class="card" style="flex:1"><div class="card-title gas">Open Position</div>' +
    buildPosHTML(pos, d.gas_price_usd) +
    '</div>' +
    '</div>';

  var rightCol = '<div class="col">' + renderRightPanel(d) + '</div>';

  document.getElementById('main-grid').innerHTML = leftCol + centreCol + rightCol;
}

/* -- Page 2: P&L and performance ------------------------------------------ */
function renderPage2(d){
  var acc      = d.account       || {};
  var perf     = d.perf          || {};
  var trades   = d.trades        || [];
  var monthly  = d.monthly_stats || [];
  var breakdown= perf.breakdown  || {};
  var dirStats = breakdown.direction || {};
  var liqStats = breakdown.liquidity || {};
  var pnl      = acc.total_pnl   || 0;
  var dpnl     = acc.daily_pnl   || 0;

  document.getElementById('p2-account-bar').innerHTML =
    '<div><div class="acc-lbl">Balance</div>' +
    '<div class="acc-val acc-bal">&pound;' + (acc.capital||1000).toLocaleString('en-GB',{minimumFractionDigits:2}) + '</div></div>' +
    '<div><div class="acc-lbl">Total P&amp;L</div>' +
    '<div class="acc-val ' + (pnl>=0?'win':'loss') + '">&pound;' + fmtPnl(pnl) + '</div></div>' +
    '<div><div class="acc-lbl">Return</div>' +
    '<div class="acc-val ' + (pnl>=0?'win':'loss') + '">' + (acc.total_return>=0?'+':'') + fmt(acc.total_return) + '%</div></div>' +
    '<div><div class="acc-lbl">Today P&amp;L</div>' +
    '<div class="acc-val ' + (dpnl>=0?'win':'loss') + '">&pound;' + fmtPnl(dpnl) + '</div></div>' +
    '<div><div class="acc-lbl">Trades</div>' +
    '<div class="acc-val gas">' + (acc.total_trades||0) + '</div></div>' +
    '<div><div class="acc-lbl">W / L</div>' +
    '<div class="acc-val"><span class="win">' + (acc.winners||0) + '</span> / <span class="loss">' + (acc.losers||0) + '</span></div></div>' +
    '<div><div class="acc-lbl">Win Rate</div>' +
    '<div class="acc-val ' + ((acc.win_rate||0)>=50?'win':'loss') + '">' + fmt(acc.win_rate,1) + '%</div></div>';

  var total = perf.total_trades || 0;
  var perfHTML = '';
  if(total === 0){
    perfHTML = '<div style="color:var(--muted);font-size:12px;padding:16px 0;text-align:center">No trades yet -- system ready</div>';
  } else {
    var score  = perf.confidence_score || 50;
    var level  = perf.confidence_level || 'MEDIUM';
    var sc     = level==='HIGH' ? 'score-high' : (level==='LOW'||level==='VERY_LOW') ? 'score-low' : 'score-med';
    var lc     = level==='HIGH' ? 'bull'       : (level==='LOW'||level==='VERY_LOW') ? 'bear'      : 'neut';
    var stType = perf.streak_type  || '';
    var stCnt  = perf.streak_count || 0;
    var stCol  = stType==='WIN' ? 'var(--green)' : stType==='LOSS' ? 'var(--red)' : 'var(--muted)';
    var stStr  = stCnt > 0 ? (stCnt + ' ' + (stType==='WIN'?'WIN':'LOSS') + (stCnt>1?'S':'')) : '--';

    perfHTML += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">' +
      '<span style="font-size:11px;color:var(--muted);min-width:80px">Confidence</span>' +
      '<div class="score-bar"><div class="score-fill ' + sc + '" style="width:' + score + '%"></div></div>' +
      '<span class="' + lc + '" style="font-size:14px;font-weight:700;min-width:110px;text-align:right">' + score + '/100 ' + level + '</span></div>';

    var last10 = trades.slice(0, 10);
    var dots10 = last10.map(function(t){
      return '<span class="perf-dot ' + (t.pnl_class==='win'?'perf-win':'perf-loss') + '"></span>';
    }).join('');
    perfHTML += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">' +
      '<span style="font-size:11px;color:var(--muted);min-width:80px">Last ' + last10.length + '</span>' +
      (dots10 || '<span style="color:var(--muted);font-size:11px">No trades</span>') + '</div>';

    perfHTML += '<div style="display:flex;gap:24px;font-size:12px;color:var(--muted);margin-bottom:14px;flex-wrap:wrap;">' +
      '<span>Streak: <strong style="color:' + stCol + '">' + stStr + '</strong></span>' +
      '<span>Total trades: <strong style="color:var(--gas)">' + total + '</strong></span>' +
      '<span>Win rate: <strong style="color:var(--text)">' + fmt(perf.win_rate,1) + '%</strong></span>' +
      '</div>';

    var dirKeys = Object.keys(dirStats);
    if(dirKeys.length > 0){
      perfHTML += '<div class="p2-section-hdr">Win Rate by Direction (LONG vs SHORT)</div><div class="p2-stat-grid">';
      dirKeys.forEach(function(dk){
        var ds  = dirStats[dk];
        var dcl = dk==='LONG' ? 'bull' : 'bear';
        var wcl = ds.win_rate >= 50 ? 'bull' : 'bear';
        perfHTML += '<div class="p2-stat-box">' +
          '<div class="p2-stat-label ' + dcl + '">' + dk + '</div>' +
          '<div class="p2-stat-val ' + wcl + '">' + ds.win_rate + '%</div>' +
          '<div class="p2-stat-sub">' + ds.wins + ' W / ' + (ds.trades-ds.wins) + ' L -- ' + ds.trades + ' trades</div>' +
          '</div>';
      });
      perfHTML += '</div>';
    }

    var liqKeys = Object.keys(liqStats);
    if(liqKeys.length > 0){
      perfHTML += '<div class="p2-section-hdr">Win Rate by Liquidity Period</div><div class="p2-stat-grid">';
      liqKeys.forEach(function(sk){
        var ss  = liqStats[sk];
        var wcl = ss.win_rate >= 50 ? 'bull' : 'bear';
        perfHTML += '<div class="p2-stat-box">' +
          '<div class="p2-stat-label">' + sk.replace(/_/g,' ') + '</div>' +
          '<div class="p2-stat-val ' + wcl + '">' + ss.win_rate + '%</div>' +
          '<div class="p2-stat-sub">' + ss.wins + ' W / ' + (ss.trades-ss.wins) + ' L -- ' + ss.trades + ' trades</div>' +
          '</div>';
      });
      perfHTML += '</div>';
    }

    if(perf.conservative){
      perfHTML += '<div class="cons-warn">CONSERVATIVE MODE ACTIVE -- System staying out pending improved performance</div>';
    }
  }

  document.getElementById('p2-perf-detail').innerHTML =
    '<div class="card-title gas">Arthur Self-Performance -- Detail</div>' + perfHTML;

  var monthHTML = '';
  if(monthly.length === 0){
    monthHTML = '<div style="color:var(--muted);font-size:12px;padding:14px 0;text-align:center">No trade data yet</div>';
  } else {
    var allPnls  = monthly.map(function(m){ return m.pnl; });
    var bestPnl  = Math.max.apply(null, allPnls);
    var worstPnl = Math.min.apply(null, allPnls);
    monthHTML = '<table class="p2-table"><thead><tr>' +
      '<th>Month</th><th>Trades</th><th>Wins</th><th>Win Rate</th><th>P&amp;L</th>' +
      '</tr></thead><tbody>';
    monthly.slice().reverse().forEach(function(m){
      var rowCls = '';
      if(monthly.length > 1){
        if(m.pnl === bestPnl)       rowCls = ' class="month-best"';
        else if(m.pnl === worstPnl) rowCls = ' class="month-worst"';
      }
      monthHTML += '<tr' + rowCls + '>' +
        '<td>' + m.month + '</td>' +
        '<td>' + m.trades + '</td>' +
        '<td>' + m.wins + '</td>' +
        '<td><span class="' + (m.win_rate>=50?'win':'loss') + '">' + m.win_rate + '%</span></td>' +
        '<td><span class="' + (m.pnl>=0?'win':'loss') + '">&pound;' + fmtPnl(m.pnl) + '</span></td>' +
        '</tr>';
    });
    monthHTML += '</tbody></table>';
  }
  document.getElementById('p2-monthly').innerHTML =
    '<div class="card-title">Monthly Breakdown</div>' + monthHTML;

  var tradeHTML = '';
  if(trades.length === 0){
    tradeHTML = '<div style="color:var(--muted);font-size:12px;text-align:center;padding:14px 0">No trades yet -- watching for setups</div>';
  } else {
    tradeHTML = '<table class="p2-table"><thead><tr>' +
      '<th>Dir</th><th>Entry Time</th><th>Entry $</th>' +
      '<th>Exit Time</th><th>Exit $</th><th>Points</th>' +
      '<th>P&amp;L USD</th><th>P&amp;L GBP</th><th>GBPUSD</th><th>Liquidity</th><th>Reason</th>' +
      '</tr></thead><tbody>';
    tradeHTML += trades.map(function(t){
      var rowCls = t.pnl_class==='win' ? ' class="tr-win"' : ' class="tr-loss"';
      return '<tr' + rowCls + '>' +
        '<td class="dir-' + t.direction.toLowerCase() + '">' + t.direction + '</td>' +
        '<td>' + t.entry_time + '</td>' +
        '<td>$' + t.entry_price + '</td>' +
        '<td>' + t.exit_time + '</td>' +
        '<td>$' + t.exit_price + '</td>' +
        '<td>' + t.points + '</td>' +
        '<td class="' + t.pnl_class + '">$' + t.pnl_usd + '</td>' +
        '<td class="' + t.pnl_class + '">&pound;' + t.pnl_gbp + '</td>' +
        '<td>' + t.gbpusd + '</td>' +
        '<td style="color:var(--muted)">' + t.liquidity + '</td>' +
        '<td style="color:var(--muted)">' + t.reason + '</td>' +
        '</tr>';
    }).join('');
    tradeHTML += '</tbody></table>';
  }
  document.getElementById('p2-trades').innerHTML =
    '<div class="card-title">Gas Trade History</div>' + tradeHTML;
}

/* -- Main refresh loop ---------------------------------------------------- */
function refreshDashboard(){
  fetch('/api/state')
    .then(function(r){ return r.json(); })
    .then(function(d){
      renderPage1(d);
      renderPage2(d);
      updateLiquidityCountdown();
    })
    .catch(function(e){ console.error('Refresh error:', e); });
}

refreshDashboard();
setInterval(refreshDashboard, 5000);

pollNews();
setInterval(pollNews, 60000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    page = HTML.replace("__VERSION_STRING__", VERSION_STRING)
    return Response(page, mimetype="text/html")


@app.route("/api/state")
def api_state():
    s = get_state()
    trade = s.get("current_trade")
    if trade is not None and hasattr(trade, "__dict__"):
        trade = {k: str(v) for k, v in trade.__dict__.items()}

    account = load_account_stats()
    trades  = load_trades()
    monthly = load_monthly_stats()
    perf    = s.get("perf") or {}

    return jsonify({
        "mode":             s.get("mode", "PAPER"),
        "version":          s.get("version", APP_VERSION),
        "liquidity_period": s.get("liquidity_period", "CLOSED"),
        "gas_price_usd":   s.get("gas_price_usd", 0.0),
        "connector_status": s.get("connector_status", "yahoo"),
        "capital":          s.get("capital", STARTING_CAPITAL),
        "daily_pnl":        s.get("daily_pnl", 0.0),
        "total_trades":     s.get("total_trades", 0),
        "win_rate":         s.get("win_rate", 0.0),
        "in_trade":         s.get("in_trade", False),
        "current_trade":    trade,
        "decision":         s.get("decision"),
        "panel_mode":       s.get("panel_mode", "pre_checks"),
        "pre_checks":       s.get("pre_checks"),
        "checklist":        s.get("checklist", {}),
        "trend_1d":         s.get("trend_1d", "NEUTRAL"),
        "trend_1h":         s.get("trend_1h", "NEUTRAL"),
        "signal_5m":        s.get("signal_5m", "NEUTRAL"),
        "indicators_1d":    s.get("indicators_1d", {}),
        "indicators_1h":    s.get("indicators_1h", {}),
        "indicators_5m":    s.get("indicators_5m", {}),
        "perf":             perf,
        "calendar":         s.get("calendar", ""),
        "kill_switch":      s.get("kill_switch", False),
        "kill_tier":        s.get("kill_tier", 0),
        "gbpusd_rate":      s.get("gbpusd_rate", 0.0),
        "updated_at":       s.get("updated_at", "--"),
        "account":          account,
        "trades":           trades,
        "monthly_stats":    monthly,
        "stay_out_quality": get_stay_out_quality(),
        "version_string":   VERSION_STRING,
    })


@app.route("/api/update", methods=["POST"])
def api_update():
    """Receive state push from main engine process."""
    try:
        new_state = request.get_json(force=True, silent=True) or {}
        push_state(new_state)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})


@app.route("/api/news")
def api_news():
    """Guinevere gas news sentiment + EIA calendar status for the dashboard panel."""
    try:
        data = guinevere_news.fetch_gas_sentiment() or {}
        ts = data.get("timestamp")
        payload = {
            "sentiment":  data.get("sentiment", "NEUTRAL"),
            "score":      data.get("score", 0),
            "headlines":  data.get("headlines", []),
            "reason":     data.get("reason", ""),
            "updated_at": ts.strftime("%H:%M:%S UTC") if hasattr(ts, "strftime") else "--",
        }
        try:
            eia_active, eia_reason = guinevere_news.get_eia_gas_calendar_status()
        except Exception:
            eia_active, eia_reason = False, ""
        payload["eia_active"] = bool(eia_active)
        payload["eia_reason"] = eia_reason
        return jsonify(payload)
    except Exception as exc:
        return jsonify({
            "sentiment": "NEUTRAL", "score": 0, "headlines": [],
            "reason": "News error: " + str(exc), "updated_at": "--",
            "eia_active": False, "eia_reason": "",
        })


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Write shutdown flag for main trader, then kill this dashboard process."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        SHUTDOWN_FLAG.write_text("shutdown requested\n", encoding="utf-8")
        log.info("Shutdown flag written -- main trader will exit on next check")
    except Exception as e:
        log.warning("Could not write shutdown flag: %s", e)

    def _kill():
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_kill, daemon=True).start()
    return jsonify({"status": "shutting_down"})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("GasTrader AI Dashboard starting on http://localhost:%d", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
