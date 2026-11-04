"""Rebuild the DC working paper in plain academic style from its own extracted text.
Body text comes from dc_blocks.json (extracted from the published PDF); tables are typed
from the paper and checked digit-by-digit against the original by check_dc.py."""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from reportlab.platypus import Paragraph, Spacer, PageBreak, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
import academic as A

OUT = "."
plt.rcParams.update({"font.family": "serif", "font.serif": ["Nimbus Roman", "Liberation Serif", "DejaVu Serif"],
                     "font.size": 9, "axes.linewidth": 0.6})

# ------------------------------------------------------------------ figures (values as printed in the paper)
fig, ax = plt.subplots(figsize=(6.6, 2.7)); ax.axis("off"); ax.set_xlim(0, 10); ax.set_ylim(0, 4.2)
stages = [("Stage 0", "Legacy replication", "Short-window parity;\nfull-history failure", "-96.5% / -96.7%\nlegacy returns"),
          ("Stages 1-2", "Event study + ML", "Fixed thresholds;\ndynamic EV model", "Dynamic model failed\n2024 validation"),
          ("Stages 3-4", "Regime maps", "Absolute then\nvol-normalized", "Regime map worse\nthan fixed baseline"),
          ("Stage 5", "Exit revision", "1.5R target;\ncost diagnostics", "+4.93 bp pre-cost;\n-8.97 bp full-cost"),
          ("H1-H2", "Bounded filters", "Impulse and BTC\nlead-lag", "No filter passed\nprogram gates")]
for i, (a, b, c_, d) in enumerate(stages):
    x = 0.15 + i * 2.0
    ax.add_patch(FancyBboxPatch((x, 2.35), 1.7, 1.45, boxstyle="round,pad=0.02,rounding_size=0.06", fc="white", ec="black", lw=0.8))
    ax.text(x + 0.85, 3.55, a, ha="center", fontsize=8.5, weight="bold"); ax.text(x + 0.85, 3.27, b, ha="center", fontsize=8.5, weight="bold")
    ax.text(x + 0.85, 2.8, c_, ha="center", va="center", fontsize=7.5)
    ax.annotate("", xy=(x + 0.85, 1.45), xytext=(x + 0.85, 2.33), arrowprops=dict(arrowstyle="->", lw=0.7))
    ax.text(x + 0.85, 1.15, d, ha="center", va="center", fontsize=7.5, style="italic")
    if i < 4:
        ax.annotate("", xy=(x + 2.0, 3.07), xytext=(x + 1.72, 3.07), arrowprops=dict(arrowstyle="->", lw=0.7))
ax.text(5, 0.25, "Conclusion: no statistically established, cost-surviving standalone edge for the tested specification",
        ha="center", fontsize=8.5, weight="bold")
fig.tight_layout(); fig.savefig(f"{OUT}/dc_fig1.png", dpi=220); plt.close(fig)

fig, ax = plt.subplots(figsize=(6.4, 2.9))
groups = ["Phase 4\nfixed k", "Phase 4\nregime map", "Phase 5\nfixed k", "Phase 5\nregime map"]
sig = [4.07, -0.57, 4.86, -6.16]; net = [-9.77, -14.42, -8.97, -20.04]
xs = range(4); w = 0.36
ax.bar([i - w / 2 for i in xs], sig, w, color="0.75", edgecolor="0.2", lw=0.5, label="Signal path before execution cost")
ax.bar([i + w / 2 for i in xs], net, w, color="0.25", label="Full-cost net")
for i in xs:
    ax.text(i - w / 2, sig[i] + (0.5 if sig[i] >= 0 else -1.6), f"{sig[i]:+.2f}", ha="center", fontsize=7.5)
    ax.text(i + w / 2, net[i] - 1.6, f"{net[i]:+.2f}", ha="center", fontsize=7.5)
ax.axhline(0, color="black", lw=0.6); ax.set_xticks(list(xs)); ax.set_xticklabels(groups)
ax.set_ylabel("Mean basis points per trade"); ax.set_ylim(-23, 8); ax.legend(frameon=False, fontsize=8, loc="lower left")
ax.spines[["top", "right"]].set_visible(False); fig.tight_layout(); fig.savefig(f"{OUT}/dc_fig2.png", dpi=220); plt.close(fig)

fig, ax = plt.subplots(figsize=(6.4, 2.8))
lab = ["Signal path", "Funding", "Fees", "Slippage + tick", "Net"]; val = [4.86, 0.07, -11.00, -2.90, -8.97]
cols = ["0.7", "0.7", "0.35", "0.35", "0.1"]
ax.bar(range(5), val, color=cols, width=0.6)
for i, v in enumerate(val):
    ax.text(i, v + (0.35 if v >= 0 else -1.0), f"{v:+.2f}", ha="center", fontsize=8)
ax.axhline(0, color="black", lw=0.6); ax.set_xticks(range(5)); ax.set_xticklabels(lab)
ax.set_ylabel("Mean basis points per trade"); ax.set_ylim(-13, 6.5)
ax.text(4.45, 5.4, "Fees: 5.5 bp per fill; average full drag 13.90 bp per trade", ha="right", fontsize=7.5)
ax.spines[["top", "right"]].set_visible(False); fig.tight_layout(); fig.savefig(f"{OUT}/dc_fig3.png", dpi=220); plt.close(fig)

FIGS = {"Figure 1.": (f"{OUT}/dc_fig1.png", 460, 188), "Figure 2.": (f"{OUT}/dc_fig2.png", 430, 195),
        "Figure 3.": (f"{OUT}/dc_fig3.png", 430, 188)}

# ------------------------------------------------------------------ tables (transcribed; verified by check_dc.py)
TABLES = {
"Table 1.": (["Series", "Frequency", "Coverage used", "Rows", "Role"], [
    ["ETHUSDT perpetual", "1 minute", "2021-03-15 to 2024-12-31", "1,998,720", "Primary target"],
    ["XRPUSDT perpetual", "1 minute", "2021-05-13 to 2024-12-31", "1,913,186", "Primary target"],
    ["BTCUSDT perpetual", "1 minute", "2021-01-01 to 2024-12-31", "2,103,840", "H2 lead-lag feature"],
    ["ETH/XRP funding", "Settlement events", "Historical file coverage", "11,743 in initial full archive audit", "Perpetual-funding adjustment"],
    ["Instrument metadata", "Point-in-time files", "Saved during source acquisition", "3 files", "Tick and quantity rules"]],
    [92, 72, 112, 90, 102]),
"Table 2.": (["Component", "Rule"], [
    ["Signal", "Directional change confirmed at one-minute close"],
    ["Entry", "Next one-minute open; adverse market slippage and tick rounding"],
    ["Stop", "Detector stream's running extreme at confirmation"],
    ["Target", "Initially 1.0 risk unit; final registered revision 1.5 risk units"],
    ["Maximum hold", "240 minutes; unresolved trades exit at the deadline"],
    ["Intrabar ambiguity", "Stop first when stop and target are both touched in one minute"],
    ["Position limit", "One open position per symbol and policy stream"],
    ["Fees", "5.5 bp taker fee per fill, applied to each fill notional"],
    ["Other costs", "Adverse slippage, tick rounding, and historical funding"],
    ["Open-regime transition", "Do not reprice or force-close; retain entry-time bracket"]], [120, 348]),
"Table 3.": (["Stage", "Main question", "Comparison family", "Decision"], [
    ["0 Legacy audit", "Does the original complete strategy survive full history?", "Frozen Pine-equivalent strategy", "Rejected"],
    ["1 Fixed event study", "Does any unconditional fixed threshold show post-cost expectancy?", "2,640 configurations", "No established edge"],
    ["2 Dynamic ML", "Can market state select a threshold by predicted EV?", "Two models and registered hurdle; diagnostic hurdles", "Candidate failed 2024"],
    ["3 Absolute regime map", "Do causal regimes identify stable percentage thresholds?", "Regime-by-threshold cells", "Unstable; all policies lost"],
    ["4 Normalized regime map", "Do six regimes identify volatility-scaled k?", "54 cells with max-t control", "No cell passed"],
    ["5 RR revision", "Does 1.5R rescue the frozen design?", "Same 54-cell family", "No cell passed"],
    ["H1 impulse filter", "Can confirmation strength concentrate the effect?", "Single preregistered price filter", "Failed"],
    ["H2 BTC filter", "Does aligned BTC impulse improve continuation?", "Nine k streams with joint correction", "Failed"]],
    [95, 175, 118, 80]),
"Table 4.": (["Metric", "ETHUSDT", "XRPUSDT"], [
    ["Coverage", "2021-03-15 to 2026-08-22", "2021-05-13 to 2026-08-22"],
    ["Economic setups", "2,116", "2,433"], ["Setup win rate", "38.52%", "36.54%"],
    ["Profit factor", "0.747", "0.762"], ["PnL before commission", "-$250.66", "-$199.70"],
    ["Commission", "-$714.70", "-$767.49"], ["Final value from $1,000", "$34.84", "$32.96"],
    ["Month-block 95% interval", "[-0.252%, -0.102%]", "[-0.171%, -0.091%]"]], [160, 154, 154]),
"Table 5.": (["Test", "Trades", "Mean net", "PF", "Lower-bound evidence", "Outcome"], [
    ["Logit, 5 bp hurdle (2022-23 WF)", "2,175", "-7.611 bp", "0.908", "Weekly 95% lower -14.446", "Fail"],
    ["HGB, 5 bp hurdle (2022-23 WF)", "1,067", "-0.257 bp", "0.997", "Weekly 95% lower -11.632", "Fail"],
    ["HGB, 50 bp diagnostic", "230", "+10.615 bp", "1.083", "Bootstrap interval [-19.494, +46.002]", "Frozen candidate"],
    ["Candidate A, 2024", "232", "-18.982 bp", "0.868", "Clustered lower -61.709", "Reject"]],
    [125, 42, 58, 38, 135, 70]),
"Table 6.": (["Policy", "Trades", "Signal path", "Full-cost mean", "PF", "Positive folds"], [
    ["Evidence-gated regime map", "0", "-", "-", "-", "-"],
    ["Forced six-regime map", "3,923", "-0.57 bp", "-14.42 bp", "0.751", "0/4"],
    ["Fixed k = 1.25", "5,667", "+4.07 bp", "-9.77 bp", "0.820", "0/4"]], [140, 55, 70, 80, 50, 73]),
"Table 7.": (["Hypothesis", "Trades", "Pre-cost / signal path", "Full-cost mean", "PF", "Time stability", "Result"], [
    ["H1 confirmation impulse (k = 1.25)", "1,816", "+7.13 bp", "-6.69 bp", "0.892", "1/4 half-years positive", "Fail"],
    ["H2 BTC lead-lag (k = 1.25 row)", "1,797", "+6.02 bp", "-7.74 bp", "0.875", "1/4 half-years positive", "Fail"],
    ["H2 all nine streams", "39,214 filtered trades across rows", "No stream established", "All means negative", "All < 1", "No robust fold pattern", "Fail"]],
    [110, 62, 62, 58, 38, 90, 48]),
"Table A1.": (["Risk", "Safeguard", "Residual limitation"], [
    ["False or synthetic data", "Manifest hashes, CRC, schema, chronology, duplicate and OHLC checks", "Request ledger retains hashes, not every raw response body"],
    ["Look-ahead signal", "Signal at bar close; entry at next open; causal trailing features", "Future path is used only for labels"],
    ["Nested thresholds", "Independent state machine and position path for each threshold", "Pivot and reversal threshold remain coupled in Phase 4"],
    ["Intrabar ambiguity", "Stop-first if both barriers touch within one minute", "Trade-level replay would be more precise"],
    ["Serial dependence", "Calendar-week clustering and four-week moving-block bootstrap", "Block length remains a modeling choice"],
    ["Multiple comparisons", "BH in Phase 3; max-t in Phase 4/5 and nine-k audit; alpha spending in H1-H2", "Early-stage judgments were not all prospectively registered"],
    ["Execution optimism", "Taker fees, adverse slippage, tick rounding, funding; maker only as sensitivity", "No historical queue/fill model"],
    ["Post-hoc subgroups", "Long/short and asset splits labeled diagnostic", "Researcher exposure still informs future priors"]],
    [110, 195, 163]),
}

EQ = ParagraphStyle("eq", parent=A.BODY, fontName=A.ITAL, alignment=TA_CENTER, spaceBefore=2, spaceAfter=8)

DECL = [("Funding", "This research received no external funding."),
        ("Conflicts of interest", "The author declares no conflict of interest."),
        ("Data and code availability",
         "The engines, frozen configurations and gate reports for every stage are public at "
         "https://github.com/Crayon99-droid/crypto-strategy-research (folder dc_study). Raw market data are not "
         "redistributed and can be reacquired from Bybit's public endpoints, under Bybit's terms, with the downloaders in "
         "the same repository. The derived SQLite event stores and trade-level outputs are available from the author on "
         "request."),
        ("Use of AI-assisted tools",
         "Generative AI tools assisted with code development, diagnostic framing, and manuscript drafting. They did not "
         "generate, impute, interpolate, or replace market data. All source files were obtained from exchange records, "
         "cryptographically checked, and processed by deterministic code; reported values were reconciled to frozen "
         "machine-readable outputs. The author reviewed the methods, results, and prose and accepts responsibility for the "
         "final manuscript."),
        ("Ethics and investment disclaimer",
         "The study used market and account-fee information and involved no human subjects. It is research, not investment "
         "advice. No policy reported in this manuscript passed the evidence gate for live deployment.")]


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


blocks = json.load(open("dc_blocks.json"))
for b in blocks:   # remove declaration text that was glued onto a reference during extraction
    if b["k"] == "ref" and " Data-download tools" in b["t"]:
        b["t"] = b["t"].split(" Data-download tools")[0]

S = A.title_block(
    "From Apparent Profit to a Reproducible Null Result: A Cost-Aware Evaluation of Directional-Change "
    "Reversal Trading in Cryptocurrency Perpetual Futures",
    "Rayan Tariq", "Working paper, September 2026 (revised November 2026)",
    esc(blocks[1]["t"]), esc(blocks[2]["t"].replace("Keywords:", "").strip()))

pending_fig = False
i = 3
while i < len(blocks):
    b = blocks[i]; k = b["k"]; t = esc(b.get("t", ""))
    if k == "h1":
        if t == "References":
            S.append(Paragraph("Declarations", A.H1))
            for h, body in DECL:
                S += [Paragraph(h, A.H2), Paragraph(esc(body), A.BODY)]
        if t.startswith("Appendix"):
            S.append(PageBreak())
        S.append(Paragraph(t, A.H1))
    elif k == "h2":
        S.append(Paragraph(t, A.H2))
    elif k == "body":
        if t.startswith(("downward change if", "theta_t(k) =")):
            S.append(Paragraph(t.replace("     ", "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"), EQ))
        else:
            S.append(Paragraph(t, A.BODY))
    elif k == "bullet":
        S.append(Paragraph(t, A.BULLET, bulletText="•"))
    elif k == "ref":
        S.append(Paragraph(t, A.REF))
    elif k == "image":
        pending_fig = True
    elif k == "cap":
        key = t.split(" ")[0] + " " + t.split(" ")[1] if t.startswith("Table A") else " ".join(t.split(" ")[:2])
        if t.startswith("Figure"):
            path, w, h = FIGS[key]
            S.append(A.figure(path, w, h, t)); pending_fig = False
        else:
            head, rows, widths = TABLES[key]
            note = None
            if i + 1 < len(blocks) and blocks[i + 1]["k"] == "note":
                note = "<i>Note.</i> " + esc(blocks[i + 1]["t"]); i += 1
            S.append(A.table(head, rows, widths, caption=t, note=note))
    i += 1

A.build(f"{OUT}/Directional_Change_Crypto_Null_Result_Tariq_2026.pdf", S,
        "From Apparent Profit to a Reproducible Null Result: A Cost-Aware Evaluation of Directional-Change Reversal Trading in Cryptocurrency Perpetual Futures",
        "Rayan Tariq", "Working paper, September 2026 (revised November 2026)",
        first_page_note=["Code, configurations and reports: github.com/Crayon99-droid/crypto-strategy-research.",
                         "Not investment advice. No tested policy was authorized for live trading."])
print("built")
