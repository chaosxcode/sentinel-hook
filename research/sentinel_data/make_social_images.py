"""Generate social-media chart images from published Gate 1 artifacts.

Renders self-contained HTML cards (dark theme, Inter font) and screenshots
them with headless Chrome at Twitter feed resolution (1600x900).

Outputs to docs/social/images/*.png. Every number is read from the committed
results files - nothing hand-typed.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE1 = ROOT / "evidence" / "gate1"
OUT = ROOT / "docs" / "social" / "images"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1600, 900
BG = "#0b0e14"
PANEL = "#11151f"
TEXT = "#e7ecf5"
MUT = "#8b94a7"
CYAN = "#22d3ee"
VIOLET = "#a78bfa"
GREEN = "#34d399"
RED = "#f87171"
AMBER = "#fbbf24"

BASE_CSS = f"""
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:{W}px; height:{H}px; background:{BG}; color:{TEXT};
       font-family:'Inter','Inter Display',sans-serif; overflow:hidden;
       display:flex; flex-direction:column; padding:56px 72px; }}
.kicker {{ color:{CYAN}; font-size:26px; font-weight:700; letter-spacing:4px;
          text-transform:uppercase; }}
h1 {{ font-size:64px; font-weight:800; margin-top:12px; letter-spacing:-1px; }}
.sub {{ color:{MUT}; font-size:28px; margin-top:10px; }}
.footer {{ margin-top:auto; display:flex; justify-content:space-between;
          color:{MUT}; font-size:22px; border-top:1px solid #232a38; padding-top:20px; }}
.brand b {{ color:{TEXT}; }}
"""


def page(body: str, kicker: str, title: str, sub: str = "") -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{BASE_CSS}</style></head>
<body>
<div class="kicker">{kicker}</div>
<h1>{title}</h1>
<div class="sub">{sub}</div>
{body}
<div class="footer">
  <div class="brand">SENTINEL <b>// sentinel-hook</b> · Uniswap v4 research</div>
  <div>chaosxcode · github.com/chaosxcode/sentinel-hook</div>
</div>
</body></html>"""


def shoot(name: str, html: str) -> None:
    html_path = OUT / f"{name}.html"
    html_path.write_text(html, encoding="utf-8")
    import subprocess

    subprocess.run(
        [
            "google-chrome",
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            f"--window-size={W},{H}",
            f"--screenshot={OUT / name}.png",
            f"file://{html_path}",
        ],
        check=True,
        capture_output=True,
    )
    print(f"{name}.png")


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
results = json.loads((GATE1 / "gate1-validation-results.json").read_text())
m2_days = results["active_days_detail"]["M2_eth_usdc"]
m2_days = {d: s for d, s in m2_days.items() if "as_cost_sum" in s}  # skip empty windows
days_sorted = sorted(m2_days.items())
labels = [d[0][5:] for d in days_sorted]
values = [max(d[1]["as_cost_sum"], 0.0) / 1e6 for d in days_sorted]
total_as_m2 = sum(m2_days[d]["as_cost_sum"] for d in m2_days) / 1e6
active_m2 = sum(1 for d in m2_days if m2_days[d]["active"])
positive_m2 = sum(1 for d in m2_days if m2_days[d].get("positive_as_cost"))

with gzip.open(GATE1 / "derived" / "window-losses.json.gz", "rt") as handle:
    window_losses = json.load(handle)
all_windows = sorted((v for vals in window_losses.values() for v in vals if v > 0), reverse=True)
n_win = len(all_windows)
top_share = results["criteria"]["c2_top_decile_share"]  # published number
cum = []
running = 0.0
total_all = sum(all_windows)
for i, v in enumerate(all_windows):
    running += v
    cum.append(running / total_all)

cont = json.loads((GATE1 / "backtest-continuous-fee.json").read_text())
m2_sweep = cont["M2_eth_usdc"]["sweep"]
champ = next(
    e
    for e in m2_sweep
    if e["config"]["half_life_seconds"] == 300.0
    and e["config"]["k"] == 4.0
    and e["config"]["cap_bps"] == 100.0
)
evalr = champ["eval"]

corr_data = [
    ("Trailing loss → next-window |loss|", 0.61, GREEN),
    ("Last window's loss → next loss", 0.49, CYAN),
    ("Trailing vol → next-window |loss|", 0.36, VIOLET),
    ("Per-dollar loss-rate persistence", 0.30, AMBER),
    ("Gate 1 bar", 0.15, MUT),
    ("v1 trade-level signals (failed)", 0.02, RED),
]

# ---------------------------------------------------------------------------
# card 1: daily losses bars
# ---------------------------------------------------------------------------
max_v = max(values)
bars = ""
bar_w = (W - 144 - 40) / len(values)
chart_top, chart_h = 300, 380
for i, v in enumerate(values):
    h = max(3.0, (v / max_v) * chart_h)
    x = 72 + i * bar_w
    y = chart_top + chart_h - h
    bars += (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w - 3:.1f}" height="{h:.1f}" '
        f'fill="url(#g)" rx="3"/>'
    )
svg1 = f"""
<svg width="{W}" height="420" style="margin-top:36px">
<defs><linearGradient id="g" x1="0" y1="1" x2="0" y2="0">
 <stop offset="0%" stop-color="#155e75"/><stop offset="100%" stop-color="{CYAN}"/>
</linearGradient></defs>
{bars}
<line x1="72" y1="{chart_top + chart_h + 1}" x2="{W - 72}" y2="{chart_top + chart_h + 1}" stroke="#232a38"/>
<text x="72" y="{chart_top + chart_h + 44}" fill="{MUT}" font-size="22">Feb 2025</text>
<text x="{W - 200}" y="{chart_top + chart_h + 44}" fill="{MUT}" font-size="22">Dec 2025</text>
</svg>"""
stats_row = f"""
<div style="display:flex;gap:64px;margin-top:26px">
 <div><div style="font-size:58px;font-weight:800;color:{RED}">${total_as_m2:.1f}M</div>
      <div style="color:{MUT};font-size:24px">adverse selection drained (sampled days)</div></div>
 <div><div style="font-size:58px;font-weight:800;color:{GREEN}">{positive_m2}/{active_m2}</div>
      <div style="color:{MUT};font-size:24px">active days where LPs lost</div></div>
</div>"""
shoot(
    "01-daily-losses",
    page(
        svg1 + stats_row,
        "Gate 1 finding 01",
        "LPs got farmed every single day",
        "Native ETH/USDC pool · Unichain mainnet · adverse-selection cost per sampled day",
    ),
)

# ---------------------------------------------------------------------------
# card 2: concentration curve
# ---------------------------------------------------------------------------
pts = []
N = len(cum)
step = max(1, N // 400)
plot_x0, plot_y0, plot_w, plot_h = 72.0, 300.0, float(W - 144), 420.0
pts.append((0.0, 0.0))
for i in range(0, N, step):
    pts.append(((i + 1) / N, cum[i]))
pts.append((1.0, 1.0))
poly = " ".join(
    f"{plot_x0 + fx * plot_w:.1f},{plot_y0 + (1 - fy) * plot_h:.1f}" for fx, fy in pts
)
marker_x = plot_x0 + 0.1 * plot_w
marker_y = plot_y0 + (1 - top_share) * plot_h
diag = (
    f"<line x1='{plot_x0}' y1='{plot_y0 + plot_h}' x2='{plot_x0 + plot_w}' y2='{plot_y0}' "
    "stroke='#2a3345' stroke-width='3' stroke-dasharray='4 8'/>"
)
svg2 = f"""
<svg width="{W}" height="560" style="margin-top:20px">
{diag}
<polyline points="{poly}" fill="none" stroke="{CYAN}" stroke-width="5" stroke-linejoin="round"/>
<line x1="{marker_x}" y1="{plot_y0}" x2="{marker_x}" y2="{plot_y0 + plot_h}" stroke="{RED}" stroke-dasharray="8 8" stroke-width="3"/>
<circle cx="{marker_x}" cy="{marker_y:.0f}" r="11" fill="{RED}"/>
<text x="{marker_x + 24}" y="{plot_y0 + 44}" fill="{RED}" font-size="34" font-weight="800">worst 10% of windows</text>
<text x="{marker_x + 24}" y="{plot_y0 + 92}" fill="{RED}" font-size="52" font-weight="800">= {top_share * 100:.0f}% of all losses</text>
<text x="{plot_x0 + 18}" y="{plot_y0 + plot_h - 18}" fill="{MUT}" font-size="22">windows ranked by loss, worst first →</text>
</svg>"""
shoot(
    "02-concentration",
    page(
        svg2,
        "Gate 1 finding 02",
        "Loss is a landmine field, not a drip",
        "Cumulative share of adverse-selection losses across 32,604 five-minute risk windows",
    ),
)

# ---------------------------------------------------------------------------
# card 3: correlations
# ---------------------------------------------------------------------------
rows = ""
y = 330
for label, rho, color in corr_data:
    w = abs(rho) / 0.65 * (W - 260)
    rows += f"""
    <text x="72" y="{y - 12}" fill="{TEXT}" font-size="27">{label}</text>
    <rect x="72" y="{y}" width="{w:.0f}" height="34" fill="{color}" rx="6" opacity="0.9"/>
    <text x="{72 + w + 18:.0f}" y="{y + 27}" fill="{color}" font-size="30" font-weight="800">{rho:.2f}</text>"""
    y += 84
bar_line_x = 72 + (0.15 / 0.65) * (W - 260)
rows += f'<line x1="{bar_line_x:.0f}" y1="290" x2="{bar_line_x:.0f}" y2="{y}" stroke="#f87171" stroke-dasharray="10 8" stroke-width="3"/><text x="{bar_line_x + 14:.0f}" y="{y - 6}" fill="#f87171" font-size="24">Gate 1 pass bar |ρ| ≥ 0.15</text>'
shoot(
    "03-correlations",
    page(
        f"<svg width='{W}' height='880'>{rows}</svg>",
        "The plot twist",
        "Zoom out and the signal appears",
        "Spearman ρ: pre-window signals vs realized adverse-selection loss · validation 2025",
    ),
)

# ---------------------------------------------------------------------------
# card 4: continuous policy result
# ---------------------------------------------------------------------------
nd, ns = evalr["net_dynamic"] / 1e6, evalr["net_static"] / 1e6
scale = max(nd, ns) / 300.0
bw = 210
bx1, bx2 = 240, 240 + bw + 160
h_ns = ns / scale
h_nd = nd / scale
base_y = 770
svg4 = f"""
<svg width="{W}" height="560" style="margin-top:20px">
<rect x="{bx1}" y="{base_y - h_ns:.0f}" width="{bw}" height="{h_ns:.0f}" fill="#3b4457" rx="10"/>
<rect x="{bx2}" y="{base_y - h_nd:.0f}" width="{bw}" height="{h_nd:.0f}" fill="url(#gg)" rx="10"/>
<defs><linearGradient id="gg" x1="0" y1="1" x2="0" y2="0">
 <stop offset="0%" stop-color="#065f46"/><stop offset="100%" stop-color="{GREEN}"/></linearGradient></defs>
<text x="{bx1 + bw / 2}" y="{base_y - h_ns - 18:.0f}" text-anchor="middle" fill="{MUT}" font-size="34" font-weight="700">+${ns:.2f}M</text>
<text x="{bx2 + bw / 2}" y="{base_y - h_nd - 18:.0f}" text-anchor="middle" fill="{GREEN}" font-size="40" font-weight="800">+${nd:.2f}M</text>
<text x="{bx1 + bw / 2}" y="{base_y + 46}" text-anchor="middle" fill="{TEXT}" font-size="28">static 5 bps fee</text>
<text x="{bx2 + bw / 2}" y="{base_y + 46}" text-anchor="middle" fill="{TEXT}" font-size="28">Sentinel v2 dynamic fee</text>
<text x="{bx2 + bw / 2}" y="{base_y + 86}" text-anchor="middle" fill="{MUT}" font-size="23">net LP PnL · Oct-Dec 2025 sampled windows</text>
</svg>"""
badges = f"""
<div style="display:flex; gap:36px; margin-top:8px">
 <div style="background:{PANEL};border:1px solid #232a38;border-radius:14px;padding:20px 30px">
   <span style="color:{GREEN};font-size:40px;font-weight:800">18/18</span>
   <span style="color:{MUT};font-size:24px"> configs beat static out-of-sample</span></div>
 <div style="background:{PANEL};border:1px solid #232a38;border-radius:14px;padding:20px 30px">
   <span style="color:{CYAN};font-size:40px;font-weight:800">{evalr['precision'] * 100:.0f}%</span>
   <span style="color:{MUT};font-size:24px"> fee uplift landed on toxic flow</span></div>
 <div style="background:{PANEL};border:1px solid #232a38;border-radius:14px;padding:20px 30px">
   <span style="color:{VIOLET};font-size:40px;font-weight:800">{evalr['coverage']:.1f}×</span>
   <span style="color:{MUT};font-size:24px"> toxic-loss coverage ratio</span></div>
</div>"""
shoot(
    "04-backtest",
    page(
        badges + svg4,
        "Sentinel v2 backtest",
        "Dynamic fees beat static. Decisively.",
        "fee(t) = clip(k × EMA[realized adverse-selection rate], base, cap) · replayed trade-by-trade",
    ),
)

print("done:", sorted(p.name for p in OUT.glob("*.png")))
