#!/usr/bin/env python3
"""Build the professional PDF version of reports/quant_research_2026-10-16.md.

ReportLab route (kimi-pdf): cover, one-page executive summary, TOC, numbered sections,
three-line academic tables, highlighted key parameters, charts from the saved backtest
run, full APA references with URLs, and a closing risk-disclaimer page.

Usage: python reports/build_report_pdf.py
Output: reports/quant_research_2026-10-16.pdf
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

REPO = Path(__file__).resolve().parent.parent
RUN = REPO / "reports" / "eod_reversal_20261016_061710"
OUT = REPO / "reports" / "quant_research_2026-10-16.pdf"
CHART_DIR = REPO / "reports" / "_pdf_charts"
CHART_DIR.mkdir(exist_ok=True)

BODY = HexColor("#1a1a1a")
NAVY = HexColor("#1f3864")
GRAY = HexColor("#666666")
LIGHT = HexColor("#f5f5f5")

styles = getSampleStyleSheet()
S = {
    "body": ParagraphStyle("body", fontName="Times-Roman", fontSize=10, leading=14.5,
                           textColor=BODY, spaceAfter=6, alignment=4),
    "small": ParagraphStyle("small", fontName="Times-Roman", fontSize=8.6, leading=11.5,
                            textColor=BODY, spaceAfter=4),
    "cell": ParagraphStyle("cell", fontName="Times-Roman", fontSize=8.2, leading=10.5, textColor=BODY),
    "cellb": ParagraphStyle("cellb", fontName="Times-Bold", fontSize=8.2, leading=10.5, textColor=BODY),
    "h1": ParagraphStyle("h1", fontName="Times-Bold", fontSize=15, leading=19, textColor=NAVY,
                         spaceBefore=16, spaceAfter=8, keepWithNext=1),
    "h2": ParagraphStyle("h2", fontName="Times-Bold", fontSize=11.5, leading=15, textColor=NAVY,
                         spaceBefore=10, spaceAfter=5, keepWithNext=1),
    "bullet": ParagraphStyle("bullet", fontName="Times-Roman", fontSize=10, leading=14.5,
                             textColor=BODY, spaceAfter=4, leftIndent=14, bulletIndent=4),
    "ref": ParagraphStyle("ref", fontName="Times-Roman", fontSize=9, leading=12.5,
                          leftIndent=20, firstLineIndent=-20, spaceAfter=3, textColor=BODY),
    "cap": ParagraphStyle("cap", fontName="Times-Italic", fontSize=8.6, leading=11,
                          textColor=GRAY, spaceBefore=3, spaceAfter=8),
    "toc0": ParagraphStyle("toc0", fontName="Times-Bold", fontSize=10.5, leading=16, textColor=BODY),
    "toc1": ParagraphStyle("toc1", fontName="Times-Roman", fontSize=9.5, leading=14,
                           leftIndent=16, textColor=BODY),
}

REFS = [
    "Gao, L., Han, Y., Li, S. Z., & Zhou, G. (2018). Market intraday momentum. <i>Journal of Financial Economics, 129</i>(2), 394–414. https://doi.org/10.1016/j.jfineco.2018.05.009",
    "Baltussen, G., Da, Z., Lammers, S., & Martens, M. (2021). Hedging demand and market intraday momentum. <i>Journal of Financial Economics, 142</i>(1), 377–403.",
    "Baltussen, G., Da, Z., & Soebhag, A. (2025). <i>End-of-day reversal</i> [Working paper, April 2025]. Erasmus University Rotterdam / University of Notre Dame. https://academicweb.nd.edu/~zda/EOD.pdf",
    "Zarattini, C., Barbon, A., & Aziz, A. (2024). A profitable day trading strategy for the U.S. equity market. <i>SSRN Working Paper 4729284</i> (Swiss Finance Institute RP 24-98). https://ssrn.com/abstract=4729284",
    "Zarattini, C., Aziz, A., & Barbon, A. (2024). Beat the market: An effective intraday momentum strategy for S&amp;P500 ETF (SPY). <i>SSRN Working Paper 4824172</i> (SFI RP 24-97). https://ssrn.com/abstract=4824172",
    "Zarattini, C., & Aziz, A. (2023). Can day trading really be profitable? <i>SSRN Working Paper 4416622</i>. https://ssrn.com/abstract=4416622",
    "Heston, S. L., Korajczyk, R. A., & Sadka, R. (2010). Intraday patterns in the cross-section of stock returns. <i>Journal of Finance, 65</i>(4), 1369–1407.",
    "Bogousslavsky, V. (2016). Infrequent rebalancing, return autocorrelation, and seasonality. <i>Journal of Finance, 71</i>(6), 2967–3006.",
    "Bogousslavsky, V. (2021). The cross-section of intraday and overnight returns. <i>Journal of Financial Economics, 141</i>(1), 172–194.",
    "Lou, D., Polk, C., & Skouras, S. (2019). A tug of war: Overnight versus intraday expected returns. <i>Journal of Financial Economics, 134</i>(1), 192–213. https://doi.org/10.1016/j.jfineco.2019.03.011",
    "Berkman, H., Koch, P. D., Tuttle, L., & Zhang, Y. J. (2012). Paying attention: Overnight returns and the hidden cost of buying at the open. <i>Journal of Financial and Quantitative Analysis, 47</i>(4), 715–741.",
    "Novy-Marx, R., & Velikov, M. (2016). A taxonomy of anomalies and their trading costs. <i>Review of Financial Studies, 29</i>(1), 104–147. https://doi.org/10.1093/rfs/hhv042",
    "Aleti, S., Bollerslev, T., & Siggaard, M. (2025). Intraday market return predictability culled from the factor zoo. <i>Management Science, 71</i>(9), 7731–7751. https://doi.org/10.1287/mnsc.2023.01657",
    "Mesfin, M. (2026). Structural limits of OHLCV-based intraday signals in MNQ futures. <i>arXiv:2605.04004</i> [q-fin.TR] (v3, September 2026). https://arxiv.org/abs/2605.04004",
    "Holmberg, U., Lönnbark, C., & Lundström, C. (2013). Assessing the profitability of intraday opening range breakout strategies. <i>Umeå Economic Studies 845</i>, Umeå University. https://swopec.hhs.se/umnees/abs/umnees0845.htm",
    "Lundström, C. (2014). <i>On the returns of trend-following trading strategies</i> (Doctoral thesis, Umeå Economic Studies 948, Umeå University). Paper II: ORB returns across volatility states.",
    "Vortex Capital Group. (2026, July 11). The off-radar reversion illusion: The 73% fade that's really 35% [Practitioner research note; not peer-reviewed]. https://www.vortexcapitalgroup.com/insights/the-off-radar-reversion-illusion-the-73-fade-thats-really-35",
    "Vortex Capital Group. (2026, May 13–14). ORB statistical framework: Why the long-short asymmetry has inverted; Opening range breaks that actually pay [Prop-desk marketing content; not peer-reviewed]. https://www.vortexcapitalgroup.com/insights",
    "Cboe. (2023). <i>0DTE index options and market volatility: How large is the impact?</i> [Cboe research publication]. https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf",
    "Ni, S. X., Pearson, N. D., Poteshman, A. M., & White, J. (2021). Does option trading have a pervasive impact on underlying stock prices? <i>Review of Financial Studies, 34</i>, 1952–1986.",
    "Barbon, A., Beckmeyer, H., Buraschi, A., & Moerke, M. (2021). <i>Liquidity provision to rebalancing flows from leveraged ETFs and equity options</i> [Working paper]. https://www.abarbon.com/assets/Liquidity_Provision_to_Rebalancing_Flows_from_Leveraged_ETFs_and_Equity_Options.pdf",
    "Bailey, D. H., Borwein, J., López de Prado, M., & Zhu, Q. J. (2014). The deflated Sharpe ratio: Correcting for selection bias, backtest overfitting, and non-normality. <i>Journal of Portfolio Management, 40</i>(5), 94–107.",
    "Alpaca Securities LLC. (2026). <i>Brokerage fee schedule</i>. https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf",
    "QuantConnect. (2024). Opening range breakout for stocks in play [Independent replication of ref. 4]. https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/p1",
]


def cite(*ns: int) -> str:
    return "<super>[" + ",".join(str(n) for n in ns) + "]</super>"


def three_line(data, colw, header_bold=True, fontsize=None, bg=None):
    t = Table(data, colWidths=colw, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Times-Roman"),
        ("LINEABOVE", (0, 0), (-1, 0), 1.5, HexColor("#000000")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, HexColor("#000000")),
        ("LINEBELOW", (0, -1), (-1, -1), 1.5, HexColor("#000000")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if fontsize:
        style.append(("FONTSIZE", (0, 0), (-1, -1), fontsize))
    if bg:
        style.append(("BACKGROUND", (0, 0), (-1, -1), bg))
    t.setStyle(TableStyle(style))
    return t


_UNI = {"−": "-", "→": "->", "≈": "~", "≤": "<=", "≥": ">=", "σ": "sigma", "√": "sqrt"}


def san(s: str) -> str:
    """Make text safe for the ReportLab XML paragraph parser and WinAnsi fonts."""
    s = re.sub(r"&(?!amp;|lt;|gt;|#)", "&amp;", s)
    s = re.sub(r"<(?=[\s0-9$])", "&lt;", s)
    for k, v in _UNI.items():
        s = s.replace(k, v)
    return s


def P(text, style="body"):
    return Paragraph(san(text), S[style])


def make_charts():
    days, pnl = [], []
    with open(RUN / "daily.csv") as f:
        for row in csv.DictReader(f):
            days.append(row["day"])
            pnl.append(float(row["net_pnl"]))
    cum, run = [], 0.0
    for x in pnl:
        run += x
        cum.append(run)
    fig, ax = plt.subplots(figsize=(8.6, 3.4))
    ax.plot(range(len(cum)), cum, color="#1f3864", lw=1.4)
    ax.axhline(0, color="#999999", lw=0.8)
    ax.set_title("Cumulative net P&L, EOD-reversal long-loser strategy ($100k start, net of 6 bps round-trip costs)",
                 fontsize=10)
    ax.set_xlabel(f"Trading session (2026-02-12 → 2026-10-15, {len(cum)} sessions)")
    ax.set_ylabel("Cumulative net P&L ($)")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    p1 = CHART_DIR / "equity.png"
    fig.savefig(p1, dpi=150)
    plt.close(fig)

    summ = json.loads((RUN / "summary.json").read_text())
    months = list(summ["per_month"].keys())
    exp = [summ["per_month"][m]["expectancy_bps"] for m in months]
    fig, ax = plt.subplots(figsize=(8.6, 3.0))
    colors = ["#1f3864" if e >= 0 else "#9e2a2b" for e in exp]
    ax.bar(months, exp, color=colors)
    ax.axhline(0, color="#999999", lw=0.8)
    ax.set_title("Per-month expectancy (net bps per trade) — no stable positive month", fontsize=10)
    ax.set_ylabel("bps per trade")
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    p2 = CHART_DIR / "monthly.png"
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    return p1, p2, summ


class Doc(BaseDocTemplate):
    def afterFlowable(self, fl):
        if isinstance(fl, Paragraph):
            st = fl.style.name
            if st == "h1":
                self.notify("TOCEntry", (0, fl.getPlainText(), self.page))
            elif st == "h2":
                self.notify("TOCEntry", (1, fl.getPlainText(), self.page))


def header_footer(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFont("Times-Roman", 8.5)
    canvas.setFillColor(GRAY)
    canvas.drawCentredString(w / 2, h - 1.3 * cm,
                             "Quantitative Day Trading Strategy: Research & Specification Report — v1.0, 2026-10-16")
    canvas.setStrokeColor(HexColor("#bbbbbb"))
    canvas.line(2.6 * cm, h - 1.55 * cm, w - 2.6 * cm, h - 1.55 * cm)
    canvas.drawCentredString(w / 2, 1.1 * cm, f"Page {doc.page}")
    canvas.restoreState()


def cover(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(2)
    canvas.line(4 * cm, h - 6.4 * cm, w - 4 * cm, h - 6.4 * cm)
    canvas.setFillColor(NAVY)
    canvas.setFont("Times-Bold", 24)
    canvas.drawCentredString(w / 2, h - 7.8 * cm, "Quantitative Day Trading Strategy:")
    canvas.drawCentredString(w / 2, h - 8.9 * cm, "Research & Specification Report")
    canvas.setLineWidth(0.8)
    canvas.line(4 * cm, h - 9.6 * cm, w - 4 * cm, h - 9.6 * cm)
    canvas.setFont("Times-Roman", 13)
    canvas.setFillColor(BODY)
    canvas.drawCentredString(w / 2, h - 11.0 * cm, "End-of-Day Reversal — Long the Intraday Losers (Baltussen, Da & Soebhag 2025)")
    canvas.setFont("Times-Roman", 11)
    canvas.drawCentredString(w / 2, h - 12.2 * cm, "An evidence-based survey of intraday strategy families, a fully specified DSL v0.1 strategy,")
    canvas.drawCentredString(w / 2, h - 12.8 * cm, "and a net-of-cost validation on the llamaOS / Alpaca paper-trading system")
    canvas.setFont("Times-Roman", 11)
    canvas.drawCentredString(w / 2, 5.4 * cm, "Report version 1.0")
    canvas.drawCentredString(w / 2, 4.8 * cm, "Date: 2026-10-16")
    canvas.drawCentredString(w / 2, 4.2 * cm, "Prepared for: llamaOS strategy registry (Alpaca US equities paper trading)")
    canvas.setFont("Times-Italic", 9)
    canvas.setFillColor(GRAY)
    canvas.drawCentredString(w / 2, 2.6 * cm, "For informational and educational purposes only — not investment advice. See the risk disclaimer on the final page.")
    canvas.restoreState()


def build():
    p_eq, p_mo, summ = make_charts()
    doc = Doc(str(OUT), pagesize=A4, topMargin=2.2 * cm, bottomMargin=2.0 * cm,
              leftMargin=2.6 * cm, rightMargin=2.6 * cm,
              title="Quantitative Day Trading Strategy: Research & Specification Report",
              author="Senior quantitative research (automated), prompt quant_research@v1",
              subject="End-of-day reversal day-trading strategy: research, specification, validation")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="cover", frames=[frame], onPage=cover),
                          PageTemplate(id="body", frames=[frame], onPage=header_footer)])

    story = []
    story.append(NextPageTemplate("body"))
    story.append(Spacer(1, 1))  # cover content drawn by onPage
    story.append(PageBreak())

    # ---------------- executive summary (one page)
    story.append(P("Executive summary", "h1"))
    for txt in [
        f"<b>Selected strategy.</b> Buy at ~15:30 ET, in size-risked 0.25%-of-equity units, any of 8 mega-cap technology "
        f"stocks whose return from the previous close to 15:00 ET (\"ROD3\") is at least 0.25 × their 14-day ATR "
        f"<i>down</i>; hold to the 15:55 forced flatten. This is the long side of the end-of-day reversal documented on "
        f"TAQ/CRSP 1993–2019: cross-sectionally, intraday losers bounce in the last half hour (value-weighted "
        f"loser-minus-winner 3.78 bps/day, t = 10.69; largest-quintile stocks 3.41 bps/day, t = 10.61){cite(3)}. The "
        f"mechanism is end-of-day retail buy-the-dip buying plus a more than threefold drop in new short positions on "
        f"losers into the close{cite(3, 11)}. The effect is entirely on the loser side, so the evidence-based implementation is long-only.",
        f"<b>Why this strategy over the alternatives.</b> It is the strongest verified, mechanism-based, untapped result the "
        f"system can express. Opening-range breakout{cite(4, 24)} and market intraday momentum{cite(1, 2)} are already deployed; "
        f"VWAP fading fails net of costs in the best available practitioner measurement{cite(17)}; factor-zoo machine learning{cite(13)} "
        f"is not expressible in DSL 0.1; overnight-clientele strategies{cite(10)} require holding overnight, which this system forbids.",
        f"<b>Own validation (net of costs).</b> On this repository's stored 1-minute bars (169 sessions, 2026-02-12 → 2026-10-15; "
        f"8 symbols; 320 trades): gross edge <b>+2.9 bps/trade</b> — close to the paper's mega-cap magnitude — but a realistic "
        f"~6 bps round trip yields <b>−3.08 bps/trade net (t = −1.12), daily Sharpe −1.12, net −$969.63 on $100,000</b>. The "
        f"chronological holdout is worse than in-sample (−5.59 vs −1.38 bps); cost stress at 2×/3× gives −9.1/−15.0 bps; block "
        f"bootstrap P(mean daily return ≤ 0) = 0.87; three pre-registered parameter variants give −3.48/−3.08/−3.18 bps.",
        f"<b>Conclusion.</b> The literature effect appears present in our data at roughly the predicted magnitude, but it does "
        f"<b>not</b> survive realistic retail transaction costs on an 8-name universe with marketable-limit entries and market "
        f"flattens — an independent equity-market reproduction of the verified MNQ falsification study's central finding{cite(14)} "
        f"and of the general cost theorem of Novy-Marx & Velikov{cite(12)}.",
        f"<b>Recommendation: do not approve for paper trading as a return-seeking strategy.</b> The YAML artifact "
        f"(<font face='Courier' size='9'>strategies/eod_reversal_losers_long.v1.yaml</font>) is validated and internally consistent; "
        f"Section 10 lists the five conditions that would change this decision (cheaper execution first).",
    ]:
        story.append(P(txt))
    story.append(PageBreak())

    # ---------------- TOC
    story.append(P("Table of contents", "h1"))
    toc = TableOfContents()
    toc.levelStyles = [S["toc0"], S["toc1"]]
    story.append(toc)
    story.append(PageBreak())

    # ---------------- 1 literature
    story.append(P("1. Literature survey and verification", "h1"))
    story.append(P(
        "Every work cited in this report was fetched and checked during this research session. Table 1 records the "
        "verification outcome. Two attributions in the commissioning brief were imprecise (the \"Notre Dame gamma-hedging "
        "paper\" is Baltussen, Da, Lammers & Martens 2021 — Zhi Da is at Notre Dame; \"Boller et al., Duke 2025\" is Aleti, "
        "Bollerslev & Siggaard 2025). The \"Stockholm School of Economics ORB papers\" are Umeå University working papers "
        "hosted on the Stockholm-hosted Swopec server. The two \"Vortex Capital\" items exist but are a proprietary trading "
        "firm's content, not peer-reviewed research; they are used only as practitioner evidence. No cited academic work "
        "was found to be fabricated, and no statistics from unverifiable sources are used anywhere in this report."))
    vt = [[P("#", "cellb"), P("Work", "cellb"), P("Status", "cellb")]]
    vrows = [
        ("1", "Gao, Han, Li & Zhou (2018), JFE 129(2), 394–414", "Verified"),
        ("2", "Baltussen, Da, Lammers & Martens (2021), JFE 142(1), 377–403", "Verified"),
        ("3", "Baltussen, Da & Soebhag (2025), End-of-Day Reversal (wp)", "Verified — full text fetched"),
        ("4", "Zarattini, Barbon & Aziz (2024), SSRN 4729284 / SFI 24-98", "Verified"),
        ("5", "Zarattini, Aziz & Barbon (2024), SSRN 4824172 / SFI 24-97", "Verified"),
        ("6", "Zarattini & Aziz (2023), SSRN 4416622", "Verified"),
        ("7", "Heston, Korajczyk & Sadka (2010), JF 65(4)", "Verified"),
        ("8", "Bogousslavsky (2016) JF 71(6); (2021) JFE 141(1)", "Verified"),
        ("9", "Lou, Polk & Skouras (2019), JFE 134(1), 192–213", "Verified — abstract fetched"),
        ("10", "Berkman, Koch, Tuttle & Zhang (2012), JFQA", "Verified"),
        ("11", "Novy-Marx & Velikov (2016), RFS 29(1), 104–147", "Verified"),
        ("12", "Aleti, Bollerslev & Siggaard (2025), Mgmt Sci 71(9)", "Verified — abstract fetched"),
        ("13", "Mesfin (2026), arXiv:2605.04004 v3 (falsification study)", "Verified — abstract fetched"),
        ("14", "Holmberg, Lönnbark & Lundström (2013); Lundström (2014), Umeå", "Verified — brief misattributed the institution"),
        ("15", "\"Vortex Capital\" ORB asymmetry notes (May 2026)", "Exists — prop-desk marketing, not research"),
        ("16", "VCG \"Off-Radar Reversion Illusion\" (Jul 2026)", "Exists — practitioner note, documented method, not peer-reviewed"),
        ("17", "Cboe (2023), 0DTE options and market volatility", "Verified"),
        ("18", "Ni, Pearson, Poteshman & White (2021), RFS 34; Barbon et al. (2021)", "Verified"),
        ("19", "Bailey, Borwein, López de Prado & Zhu (2014), JPM 40(5)", "Verified"),
    ]
    for r in vrows:
        vt.append([P(r[0], "cell"), P(r[1], "cell"), P(r[2], "cell")])
    story.append(three_line(vt, [0.9 * cm, 9.6 * cm, 5.2 * cm]))
    story.append(P("Table 1 — Citation verification. URLs are in the References section.", "cap"))

    story.append(P("1.1 Opening Range Breakout (ORB)", "h2"))
    story.append(P(
        f"Zarattini, Barbon & Aziz (2024) report the 5-minute ORB on 7,000+ US stocks 2016–2023 restricted to \"Stocks in "
        f"Play\" (RVOL > 1, price > $5, ATR(14) > $0.50): a top-20 portfolio earned Sharpe 2.81, 36% annualized alpha, "
        f"> 1,600% total net of costs{cite(4)}; an independent QuantConnect replication reaches Sharpe ≈ 2.4 with a 97% "
        f"probabilistic Sharpe{cite(24)}. The Umeå studies show ORB returns vary by volatility state by ~150 bps/day (S&P "
        f"futures) to ~200 bps/day (crude oil){cite(15, 16)} — strong regime dependence. Counter-evidence: in the verified "
        f"falsification study the MNQ ORB-long family clears the friction floor but fails statistical criteria (out-of-sample "
        f"net T = 0.88, year-unstable){cite(14)}. The edge is concentrated in news-driven small/mid caps with 4× leverage — "
        f"neither available here — and the family is <b>already deployed</b> in this repository "
        f"(<font face='Courier' size='9'>orb5_stocks_in_play_long/short.v1.yaml</font>). The claimed 2026 long/short ORB "
        f"asymmetry study (Vortex) is practitioner content, not evidence{cite(18)}."))
    story.append(P("1.2 Intraday momentum (market level)", "h2"))
    story.append(P(
        f"Gao, Han, Li & Zhou (2018): the previous-close→10:00 return predicts SPY's last half hour (1993–2013), more "
        f"strongly on volatile, high-volume, recession and macro-news days, and in ten other liquid ETFs{cite(1)}. "
        f"Baltussen, Da, Lammers & Martens (2021) attribute it to end-of-day hedging demand — index-option gamma hedging "
        f"and leveraged-ETF rebalancing{cite(2, 19, 20, 21)}. Best-established market-level intraday effect; effect size is "
        f"small (R² ≈ 2%) and cost-sensitive. <b>Already deployed</b> "
        f"(<font face='Courier' size='9'>intraday_momentum_last_half_hour_long/short.v1.yaml</font>)."))
    story.append(P("1.3 VWAP-based strategies", "h2"))
    story.append(P(
        f"No peer-reviewed study supports mechanical VWAP fading. The most careful measurement available (practitioner "
        f"grade, 833 trades on 29 mid-caps vs 8 mega-caps, Mar–Jul 2026) shows the naive \"72.6% of stretched names revert\" "
        f"statistic is an accounting artifact: only 35.2% reach VWAP before a 1σ stop, gross expectancy +1.7 bps, "
        f"<b>net −24 bps</b> after measured spreads; 76% of the apparent reversion is VWAP drifting toward the price{cite(17)}. "
        f"Weakest family; several variants are already deployed here; no new VWAP file is warranted."))
    story.append(P("1.4 The falsification study (arXiv:2605.04004)", "h2"))
    story.append(P(
        f"Real (v3, September 2026). Fourteen OHLCV signal families on 947 days of 5-minute MNQ futures (2021–2025) under "
        f"expanding-window walk-forward had to pass five criteria (out-of-sample net T ≥ 2.0; ≥ 30 trades per fold; positive "
        f"net after friction; direction stable across 2023/2024/2025; permutation p &lt; 0.001 where applicable). "
        f"<b>None passed all five.</b> Eleven families die on friction (gross 0.07–1.50 points vs a 2.0-point floor); ORB long "
        f"(T = 0.88) and the VVG-classifier reversal (T = 1.26) clear friction but are year-unstable; gap continuation short "
        f"(gross +16.53 points, T = 1.46) fails stability and sample minimums. Two positive controls (T = 3.11 and 4.30) prove "
        f"the methodology detects genuine edge{cite(14)}. Implication adopted here: a candidate must target a documented "
        f"<i>flow</i>, trade where friction is smallest, and still expect a single-digit-bps gross edge — costs decide everything."))
    story.append(P("1.5 Factor-based intraday predictability", "h2"))
    story.append(P(
        f"Aleti, Bollerslev & Siggaard (2025, Management Science): sizeable out-of-sample Sharpe and alphas net of costs "
        f"trading liquid ETFs on ML-regularized predictions from the lagged high-frequency cross-section of the factor zoo; "
        f"performance concentrates in high-uncertainty periods; tail-risk and liquidity factors dominate; mechanism is "
        f"slow-moving capital{cite(13)}. <b>Not implementable</b> in DSL 0.1 (requires the factor cross-section and a trained "
        f"regularized regression — primitives that do not exist in the language)."))
    story.append(P("1.6 End-of-day reversal (selected family)", "h2"))
    story.append(P(
        f"Baltussen, Da & Soebhag (2025){cite(3)}: ROD3 (previous close → 15:00) <b>negatively</b> predicts the 15:30–16:00 "
        f"return cross-sectionally (panel t &gt; 10; present in nearly every 3-year rolling window). VW loser-minus-winner "
        f"3.78 bps/day (t = 10.69); EW 6.38 bps/day (t = 17.30); ≈ 9.5%/17.3% annualized on capital deployed 30 min/day. "
        f"Robust in the <b>largest, most liquid</b> stocks (largest-quintile alpha 3.41 bps/day, t = 10.61) and to skipping the "
        f"last 5 minutes of the day (15:30–15:55 horizon, t = −8.76) — exactly this system's flatten time. The reversal is "
        f"<b>entirely driven by intraday losers</b>: end-of-day buy-the-dip retail purchases (attention-driven) plus a > 3× "
        f"stronger drop in new short positions on losers (overnight risk management). The pressure is transitory — it reverts "
        f"the next day — so the strategy is liquidity provision, not information trading. The authors' own caveat: the effect "
        f"\"might not be exploitable by many investors after accounting for transaction costs.\" It complements rather than "
        f"contradicts market-level momentum: individual stocks show time-series momentum but cross-sectional reversal via "
        f"cross-stock autocovariance{cite(1, 2, 3)}."))

    # ---------------- 2 selection
    story.append(P("2. Strategy selection and justification", "h1"))
    story.append(P(
        f"<b>Selected: end-of-day reversal, long the intraday losers, on eight mega-cap technology names.</b> "
        f"(i) <i>Evidence:</i> 27 years of TAQ/CRSP data, t &gt; 10, robust across windows, subsamples, quote-midpoint returns "
        f"and closing-price effects{cite(3)}. (ii) <i>Mechanism:</i> predictable, recurring one-directional flows (retail "
        f"buy-the-dip; short-seller overnight risk management) on the loser side — we are paid for liquidity provision into "
        f"transitory pressure, a rationale rather than a pattern. (iii) <i>Instrument fit:</i> the effect holds in the largest "
        f"names, which have ~1 bp half-spreads — the only place a 3–4 bps gross effect can survive. (iv) <i>System fit:</i> "
        f"25-minute hold, flat 15:55, price-only rules (clean point-in-time backtests), no leverage or derivatives. "
        f"(v) <i>Not duplicative:</i> stock-level cross-sectional reversal vs the deployed market-level continuation file; "
        f"close-window liquidity provision vs the deployed morning-breakout files."))
    story.append(P(
        f"<b>Why it might survive where the MNQ study's families failed — and why it does not.</b> In its favor: a flow-based "
        f"anomaly with t &gt; 10 in 27 years, traded at ~40× tighter spreads than the study's friction assumptions, on the "
        f"documented side. Against it, and decisive in my own test: the mega-cap gross edge (~3 bps) is about half the "
        f"round-trip toll (~6 bps). My backtest reproduces the study's outcome on equities: gross ≈ +2.9 bps, net ≈ −3.1 bps"
        f"{cite(12, 14)}. Mechanism alone does not create capacity to pay the toll."))

    # ---------------- 3 specification
    story.append(P("3. The fully specified trading system", "h1"))
    story.append(P(
        "File: <font face='Courier' size='9'>strategies/eod_reversal_losers_long.v1.yaml</font> — passes the repository "
        "validator with zero errors and zero warnings against the effective account risk controls (hash prefix "
        "22795fd3401d2cb7). Table 2 highlights every numerical parameter; all times are US Eastern; all rules evaluate on "
        "1-minute bars; the account flattens by 15:55."))
    spec = [
        [P("Component", "cellb"), P("Exact specification", "cellb")],
        [P("Universe", "cell"), P("AAPL, AMD, AMZN, GOOGL, META, MSFT, NVDA, TSLA; filters prior close ≥ $5 and ADV(20) ≥ $1B. "
                                  "Deepest books and highest retail attention in the US market; typical quoted spread ≈ $0.01 (~1 bp); "
                                  "the only names with full stored 1-minute history (169 sessions each)", "cell")],
        [P("Predictor", "cell"), P("rod3 = close_at(330) / prior_close − 1 — previous close → 15:00 bar close; exactly the paper's ROD3", "cell")],
        [P("Entry trigger", "cell"), P("rod3 ≤ −0.25 × (ATR_d(14) / prior_close) — the move to 15:00 must be ≥ a quarter of the stock's "
                                       "own daily ATR down (≈ −0.6% for a 2.5%-ATR name); volatility-scaled, regime-adaptive", "cell")],
        [P("Entry window / fill", "cell"), P("Decision on the 15:29/15:30 bar close; marketable-limit fill at the next bar's open + slippage "
                                             "(system fill model, core/exchange.py)", "cell")],
        [P("Selection cap", "cell"), P("≤ 3 positions and ≤ 3 trades/day; backtest takes the lowest rod3 first (extreme-decile intent)", "cell")],
        [P("Sizing", "cell"), P("qty = floor(0.0025 × equity / (0.3 × ATR_d(14))), capped at 10% of equity notional (mirrors core/engine.py); "
                                "average deployed notional ≈ $9.8k on $100k", "cell")],
        [P("Stop / target", "cell"), P("stop = entry − 0.3 × ATR_d(14) (hit 15/320); target = entry + 3 × risk (never hit) — protective bracket only", "cell")],
        [P("Time exit", "cell"), P("Forced flatten 15:55 at market — the intended exit (305/320 trades)", "cell")],
        [P("Do-not-trade", "cell"), P("rod3 above threshold; missing 15:00 bar or < 21 prior sessions; price < $5; ADV < $1B; outside "
                                      "15:29–15:30; any risk-control state (kill switch, daily-loss breach)", "cell")],
        [P("Costs (backtest)", "cell"), P("Commission $0 (Alpaca retail API is commission-free for US equities; sell-side regulatory fees "
                                          "≈ 0.14 bp/side, negligible) + 2 bps slippage/side + 1 bp half-spread/side ≈ 6 bps round trip; "
                                          "stressed at 2×/3×" + cite(17, 23), "cell")],
    ]
    story.append(three_line(spec, [3.2 * cm, 12.5 * cm], bg=LIGHT))
    story.append(P("Table 2 — Highlighted strategy parameters (identical in the YAML, the backtest script, and this report).", "cap"))
    story.append(P(
        "Declared gaps versus the paper (in the file's <i>assumptions</i>): an absolute ATR-scaled threshold approximates the "
        "cross-sectional loser decile (DSL 0.1 has no rank builtin); next-bar-open fills replace a resting 15:30 order; the "
        "15:55 flatten gives up the last 5 minutes (covered by the paper's robustness check); 0.25% risk, no leverage; long "
        "only — the account currently permits shorts, but the evidence does not support the winner side, so no short file is shipped."))

    # ---------------- 4 performance
    story.append(P("4. Expected performance — literature vs own backtest", "h1"))
    story.append(P(
        f"<b>Literature-reported</b> (authors' figures, gross unless noted; TAQ/CRSP 1993–2019){cite(3)}: VW L−W 3.78 bps/day "
        f"(t = 10.69); EW 6.38 bps/day (t = 17.30); largest-quintile L−S alpha 3.41 bps/day (t = 10.61); annualized 9.5%/17.3% "
        f"on 30-min capital. The long side alone is not separately reported; the L−S being loser-driven implies roughly half "
        f"the spread (~2–4 bps/day) for a mega-cap long-loser sleeve."))
    perf = [
        [P("Metric (own backtest, net)", "cellb"), P("Value", "cellb"), P("Metric", "cellb"), P("Value", "cellb")],
        [P("Sessions / trades", "cell"), P("169 / 320 (1.89/day)", "cell"), P("Daily Sharpe / daily t", "cell"), P("−1.12 / −0.92", "cell")],
        [P("Win rate", "cell"), P("47.8%", "cell"), P("Profit factor", "cell"), P("0.84", "cell")],
        [P("Expectancy", "cell"), P("−3.08 bps (−$3.03)/trade", "cell"), P("Max drawdown (daily)", "cell"), P("−1.86%", "cell")],
        [P("Gross expectancy", "cell"), P("+2.9 bps/trade", "cell"), P("Net P&L on $100k", "cell"), P("−$969.63 (gross +$914.92)", "cell")],
        [P("In-sample (→ 2026-07-10)", "cell"), P("191 tr., −1.38 bps, SR −0.46", "cell"), P("Holdout (2026-07-13 →)", "cell"), P("129 tr., −5.59 bps, SR −2.55", "cell")],
        [P("Cost stress ×2 / ×3", "cell"), P("−9.09 / −15.01 bps", "cell"), P("Bootstrap P(mean ≤ 0)", "cell"), P("0.87 (10k, block 5, seed 7)", "cell")],
    ]
    story.append(three_line(perf, [4.1 * cm, 4.2 * cm, 3.9 * cm, 3.5 * cm]))
    story.append(P("Table 3 — Performance summary, saved run reports/eod_reversal_20261016_061710 (data fingerprint 40fb3cb5…).", "cap"))
    story.append(Image(str(p_eq), width=15.6 * cm, height=6.2 * cm))
    story.append(P("Figure 1 — Cumulative net P&L over the stored window.", "cap"))
    story.append(Image(str(p_mo), width=15.6 * cm, height=5.4 * cm))
    story.append(P("Figure 2 — Per-month net expectancy; no stable positive month.", "cap"))
    story.append(P(
        "<b>Regime splits (descriptive):</b> high-volatility days +1.26 bps (169 trades) vs low-volatility −7.93 bps (151) — "
        "directionally consistent with the mechanism, but turning it into a filter now would be in-sample selection; "
        "basket-down days −3.93 bps (217) vs basket-up −1.28 bps (103). <b>Symbol dispersion:</b> TSLA +10.1 bps (52) and "
        "AMD +4.1 (48) vs NVDA −14.0 (44) and GOOGL −10.5 (34); at ~40 trades per name this is noise-grade and is reported, "
        "not acted on."))
    story.append(P(
        "<b>Pre-registered sensitivity</b> (the three values declared in the file's suggestions): drop_atr 0.15 → −3.48 bps "
        "(t = −1.38, 362 trades); 0.25 → −3.08 (t = −1.12, 320); 0.40 → −3.18 (t = −1.03, 258). A flat neighbourhood: the "
        "negative result is not a tuned artifact, and there is no adjacent rescuer."))
    story.append(P(
        f"<b>Trial log (multiple-testing honesty).</b> Four strategy variants were evaluated in total (the three above plus an "
        f"initial rvol-filtered structure, −6.01 bps on 41 trades, abandoned for the data-provenance reason in §5), each with "
        f"three cost-stress reruns. The best absolute t-statistic anywhere is 1.38, far below 2.0 even before deflation; a "
        f"Bonferroni / deflated-Sharpe adjustment over four trials only widens the gap{cite(22)}. Annualized figures are "
        f"deliberately not quoted: eight months, one year, one regime."))

    # ---------------- 5 validation
    story.append(P("5. Validation methodology", "h1"))
    for txt in [
        f"<b>Data.</b> The repository's own stored 1-minute bars, ingested from Alpaca by the paper broker (the same feed the "
        f"system trades): 169 sessions × 8 symbols × 390 bars; daily bars for prior history (6+ years). No Alpaca Market Data "
        f"keys exist in this checkout's .env, so a direct download was unavailable; yfinance was not used because its 1-minute "
        f"history covers only roughly the last 30 days (~8 days per request) — it cannot span the stored window. A data "
        f"fingerprint (sha256 over symbol/day/bar-count/timestamps/close = 40fb3cb5517ffd01…) is saved with every run.",
        f"<b>Execution accounting.</b> Next-bar-open fills (never on the decision bar), marketable-limit entry + 2 bps, "
        f"half-spread 1 bp/side, broker-side bracket with stop-first on ambiguous bars and the fresh-fill-bar rule, targets "
        f"without extra adverse slippage, 15:55 flatten at the bar close − costs — the conventions of docs/backtesting.md and "
        f"core/exchange.py. Commissions $0{cite(23)}; sell-side regulatory fees ~0.14 bp/side noted and immaterial.",
        f"<b>Holdout and walk-forward.</b> Multi-year walk-forward is infeasible — stored intraday data spans a single year "
        f"(2026) — and is stated plainly rather than faked. A chronological 60/40 split (in-sample to 2026-07-10; holdout from "
        f"2026-07-13) plus per-month attribution; the holdout is worse than the in-sample period.",
        f"<b>Statistics.</b> Per-trade t-stat on net bps; daily-return t-stat; daily Sharpe (√252); block bootstrap of daily "
        f"returns (block 5, 10,000 resamples, saved seed 7) with percentile intervals and P(≤ 0); profit factor; expectancy in "
        f"$ and bps; max drawdown on daily closes; regime and per-symbol attribution; cost stress ×1/×2/×3.",
        f"<b>Pitfalls.</b> No look-ahead (values as of bar close; daily history strictly prior sessions; the 15:00 anchor never "
        f"moves); the fixed 8-name list avoids intra-sample survivorship but is itself a selection bias (stated); multiple "
        f"testing disclosed via the trial log{cite(22)}; parameters pre-committed to the paper's predictor and the file's declared "
        f"suggestion grid; no regime-monoculture claim — one year of data is the strongest remaining limitation.",
        f"<b>Data-provenance finding (infrastructure-grade).</b> Stored bar <b>volume changes units on/after 2026-06-13</b>: "
        f"per-day sums of stored 1-minute volume drop from 65–80% of the daily table's volume to ~3% (AAPL 2026-06-12: 30.4M vs "
        f"39.0M; 2026-06-15: 1.57M vs 45.9M), while prices are continuous and correct. The DSL's rvol() — today's bar volume vs "
        f"the daily-table average — degenerates to ≈ 0.03 after that date, silently blocking any rvol-gated entry, in backtest "
        f"<i>and live</i>. The strategy's volume filter was therefore removed (the paper does not condition on volume; the "
        f"ATR-scaled move is itself the attention trigger) and the break is flagged for repair (feed/units provenance columns "
        f"on the bars table).",
    ]:
        story.append(P(txt))

    # ---------------- 6 infrastructure
    story.append(P("6. Infrastructure and feature gap analysis", "h1"))
    gap = [[P("Capability", "cellb"), P("Typical retail (ToS / TradingView / Python+Alpaca SDK)", "cellb"), P("This repository", "cellb")]]
    grows = [
        ("Real-time 1-min data", "Native streaming", "services/broker/paper.py polls Alpaca bars/news into Postgres; supported. Gap: no per-row feed/units provenance (§5)"),
        ("Opening-range calculation", "Native", "or_high/or_low/close_at builtins; supported (used as the 15:00 anchor)"),
        ("Break-level execution", "Stop / stop-limit native", "Marketable limit at next bar open, 2-bar cancel; broker-side bracket exits; no resting stop-entry orders (documented difference vs ORB papers)"),
        ("Regime filter (VIX/ATR)", "Trivial", "vol_rank / atr_d / beta_d builtins; no VIX series (needs a market-input feed, listed as DSL 0.2 future work)"),
        ("Position sizing", "Manual / scripted", "Deterministic in core/engine.py: floor(equity × risk / risk-per-share), capped by max_position"),
        ("Stop/target brackets", "Native (OCO)", "Broker-side brackets at Alpaca; stop-first in backtests; supported"),
        ("End-of-day liquidation", "Broker flags", "flatten_by 15:55 risk control + per-strategy flatten_at, early-close clamped; supported"),
        ("Logging / tracking", "CSV exports", "Hash-chained audit, decision records, trades/fills tables, equity history; better than retail"),
        ("Walk-forward backtesting", "Manual in Python", "Workspace: holdout replay, rolling fixed-rule windows, cost stress, seeded bootstrap, fingerprints; formal walk-forward fitting and multiple-testing significance listed as future work"),
        ("Cross-sectional ranking", "pandas groupby", "Missing in DSL 0.1 (no rank/quantile builtin) — the paper's decile sort is approximated by a threshold; add rank_xs() to dsl/ to close"),
        ("Volume provenance", "Vendor-tagged feeds", "Missing: bar volume changed units 2026-06-13 with no marker; add feed/units columns to bars"),
    ]
    for a, b, c in grows:
        gap.append([P(a, "cell"), P(b, "cell"), P(c, "cell")])
    story.append(three_line(gap, [3.3 * cm, 4.6 * cm, 7.8 * cm]))
    story.append(P("Table 4 — Capability map: retail baseline vs this repository.", "cap"))

    # ---------------- 7 improvements
    story.append(P("7. Improvements beyond the academic baseline", "h1"))
    story.append(P("Ranked by expected edge per added degree of freedom; fewer parameters preferred."))
    for txt in [
        "<b>1. Execution-cost reduction (no new parameters).</b> Route the 15:55 exit as a limit order at/inside the bid, or "
        "use the closing auction (MOC); saving 1–2 bps/side moves the strategy from −3 to ≈ 0 bps/trade. Executor change, "
        "not a strategy-file change; highest value.",
        "<b>2. Widen the cross-section (no new parameters).</b> Add ORCL, NFLX, AVGO and other high-attention large caps once "
        "intraday ingestion covers them; a cross-sectional anomaly gives more days with a genuine extreme loser.",
        "<b>3. Repair volume provenance, then re-test the pre-registered attention filter</b> (rvol ≥ 1.0; currently untestable, §5).",
        "<b>4. High-volatility regime gate (NOT adopted).</b> Descriptively +1.26 bps on high-vol days here; adopting it now "
        "would be in-sample selection. Pre-register vol_rank(10,60) ≥ 0.5 and evaluate on future stored data only.",
        "<b>Deprioritized:</b> time-stop variants (practitioner evidence says exit shape moves the tail, not the edge — "
        "consistent with my bracket barely binding" + cite(17) + "); discretionary retest/pullback entries (rejected); the short "
        "side (no evidence — the paper's asymmetry finding" + cite(3) + ").",
    ]:
        story.append(P(txt, "bullet"))

    # ---------------- 8 artifacts
    story.append(P("8. Artifacts created this session", "h1"))
    art = [[P("File", "cellb"), P("Purpose / verification", "cellb")]]
    arows = [
        ("strategies/eod_reversal_losers_long.v1.yaml", "Deployable DSL 0.1 strategy; validator ok, zero errors/warnings, hash 22795fd3401d2cb7"),
        ("scripts/research_eod_reversal_backtest.py", "Standalone rerunnable backtest (stdlib + psycopg; reuses dsl.evaluator.VOLUME_CUM)"),
        ("reports/eod_reversal_20261016_061710/", "Headline run: trades.csv (320), daily.csv (169), summary.json, config.json, results.md"),
        ("reports/eod_reversal_sens_drop015/ , _drop040/", "Pre-registered sensitivity runs"),
        ("reports/eod_reversal_20261016_061000/", "Abandoned rvol-filtered structure (trial log)"),
        ("reports/quant_research_2026-10-16.md", "Full research report (working document)"),
        ("reports/quant_research_2026-10-16.pdf", "This document"),
    ]
    for a, b in arows:
        art.append([P(f"<font face='Courier' size='8'>{a}</font>", "cell"), P(b, "cell")])
    story.append(three_line(art, [7.4 * cm, 8.3 * cm]))
    story.append(P("Table 5 — Artifacts. No existing files were modified; no secrets read or printed; no Docker; no orders.", "cap"))
    story.append(P(
        "<b>Verification evidence.</b> Validator: <font face='Courier' size='8'>validate(text, load_risk()) → True [] [] "
        "22795fd3401d2cb7</font>. Fast tests: <font face='Courier' size='8'>pytest tests/test_dsl.py tests/test_dsl_features.py "
        "tests/test_dsl_open_builtins.py -q → 56 passed</font>. Backtest runs end-to-end and its outputs match Section 4. "
        "Parameter consistency across YAML, script and report was checked item by item (rod_minutes 330; window 15:29–15:30; "
        "flatten 15:55; drop_atr 0.25; stop 0.3×ATR_d(14); target 3R; risk 0.25%; ≤3 positions/trades; $5/$1B universe filters; "
        "2+1 bps/side costs, $0 commission)."))

    # ---------------- 9 deploy
    story.append(P("9. Deploy instructions", "h1"))
    for txt in [
        "<b>Where the file lives.</b> strategies/eod_reversal_losers_long.v1.yaml enters the normal lifecycle at Draft "
        "(validator already passing against the effective account risk controls — Postgres overrides over config/risk.yaml).",
        "<b>How to re-run the research backtest.</b> python scripts/research_eod_reversal_backtest.py --dsn &lt;paper DB DSN&gt; "
        "(defaults equal the YAML; --drop-atr, --rvol-min, --cost-stress, --bootstrap, --outdir for variants). The dashboard "
        "Backtesting workspace can additionally replay the registered version with holdout and cost stress.",
        "<b>How approval would work.</b> Dashboard → Strategies → this version → review assumptions and the independent "
        "back-translation → approve the file's SHA-256; the registry starts at paper_manual with per-trade approvals.",
        "<b>Recommendation: DO NOT approve now</b> (negative net expectancy out-of-sample, Section 4). If approved anyway for "
        "observation, run paper_manual with zero return expectation and compare realized paper fills against the 6 bps cost "
        "model before concluding anything.",
        "<b>Before any real-money consideration (all five must hold):</b> (i) the execution improvement of §7.1 measured live "
        "at ≤ 3 bps round trip over ≥ 60 paper sessions; (ii) a pre-registered retest on ≥ 2 years of clean, "
        "provenance-tagged data showing net per-trade t ≥ 2 with holdout ≥ in-sample; (iii) the volume-units break repaired "
        "and rvol re-validated; (iv) risk controls at factory settings (the paper account's current 5% max risk / 100% max "
        "position are observation settings, not production settings); (v) owner review of the risk disclaimer.",
    ]:
        story.append(P(txt, "bullet"))

    # ---------------- references
    story.append(PageBreak())
    story.append(P("References", "h1"))
    for i, r in enumerate(REFS, 1):
        story.append(Paragraph(san(f"[{i}] {r}"), S["ref"]))

    # ---------------- disclaimer page
    story.append(PageBreak())
    story.append(P("Risk disclaimer", "h1"))
    story.append(P(
        "This research is for informational and educational purposes and is not investment advice or a solicitation to buy "
        "or sell any security. Intraday trading carries substantial risk of loss; simulated and historical results do not "
        "guarantee future performance; backtests are vulnerable to overfitting, regime change, and transaction-cost error. "
        "Any strategy must be validated out-of-sample and run on paper before real capital is considered."))
    story.append(P(
        "Literature-reported figures in this document are the cited authors' own results on their data and samples; "
        "repository backtest figures are simulations on stored data with stated cost assumptions; the two are never blended. "
        "The strategy file shipped with this report is a research artifact with a negative measured net expectancy and a "
        "explicit do-not-deploy recommendation."))

    doc.multiBuild(story)
    print(f"[pdf] {OUT}")


if __name__ == "__main__":
    build()
