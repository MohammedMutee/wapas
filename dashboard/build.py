"""Render results/summary.json into one self-contained HTML page.

Deliberately not a web application. There is no server, no build step and no
node_modules: `make dashboard` writes a single file that opens in any browser,
works offline, and can be committed next to the report it was generated from.
A judge with no toolchain can open it, and so can a laptop with no network on
the morning of a demo.

It reads the JSON the evaluation emits rather than parsing the markdown report,
so a chart cannot quietly disagree with the prose beside it or show last week's
number.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wapas.money import format_inr

ARMS = ["treatment", "baseline_rules", "baseline_naive", "baseline_blast", "control"]
LABEL = {
    "treatment": "Wapas agent",
    "baseline_rules": "Keyword rules",
    "baseline_naive": "Fixed retry ladder",
    "baseline_blast": "Contact everything",
    "control": "Untouched control",
}
# Fixed slot order from the validated categorical palette. Assigned by entity,
# never by rank, so a re-sorted chart does not repaint the arms.
SLOT = {"treatment": 1, "baseline_rules": 2, "baseline_naive": 3,
        "baseline_blast": 4, "control": 5}


def esc(text: object) -> str:
    return html.escape(str(text))


def bar_chart(
    rows: list[tuple[str, float, str]], *, max_value: float, unit: str = "",
    height: int = 26, gap: int = 12, label_width: int = 150, value_width: int = 108,
) -> str:
    """Horizontal bars. Every bar is directly labelled, so identity and value
    never depend on colour alone — which is also what discharges the contrast
    warning on the lighter slots."""
    plot = 420
    out = [f'<div class="chart" style="--label-w:{label_width}px;--value-w:{value_width}px">']
    for name, value, formatted in rows:
        width = max(2.0, (value / max_value * plot) if max_value else 2.0)
        slot = SLOT.get(name, 1)
        out.append(
            f'<div class="row">'
            f'<span class="rlabel">{esc(LABEL.get(name, name))}</span>'
            f'<span class="track"><span class="bar s{slot}" style="width:{width:.1f}px"></span></span>'
            f'<span class="rvalue">{esc(formatted)}{esc(unit)}</span>'
            f"</div>"
        )
    out.append("</div>")
    return "\n".join(out)


def grouped_accuracy(head: dict) -> str:
    """The chart that carries the argument: one bucket per group, two
    classifiers per bucket, with the single-episode ceiling drawn behind."""
    buckets = [("seen wording", "Wording seen before"),
               ("new wording", "Wording never seen"),
               ("no signal", "Text says nothing")]
    plot = 380
    out = ['<div class="chart grouped">']
    for key, title in buckets:
        model = head["model"][key]
        rules = head["rules"][key]
        n = model["n"]
        out.append(f'<div class="bucket"><div class="btitle">{esc(title)} '
                   f'<span class="muted">n={n:,}</span></div>')
        for who, cls, datum in (("Wapas agent", "s1", model), ("Keyword rules", "s2", rules)):
            pct = datum["accuracy"] or 0.0
            out.append(
                f'<div class="row">'
                f'<span class="rlabel small">{esc(who)}</span>'
                f'<span class="track"><span class="bar {cls}" '
                f'style="width:{max(2.0, pct * plot):.1f}px"></span></span>'
                f'<span class="rvalue">{pct:.1%}</span>'
                f"</div>"
            )
        out.append("</div>")
    out.append("</div>")
    return "\n".join(out)


def build(summary: dict, generated: str) -> str:
    arms = summary["arms"]
    treat = arms["treatment"]
    control = arms["control"]
    head = summary.get("head_to_head")
    model = summary.get("model")

    incremental = (treat["gross_per_episode"] - control["gross_per_episode"]) * treat["n"]
    net = incremental - treat["externalities_per_episode"] * treat["n"]

    present = [a for a in ARMS if a in arms]
    max_gross = max(arms[a]["gross_per_episode"] for a in present)
    max_harm = max(arms[a]["forbidden_retries_per_1000"] for a in present) or 1
    max_contacts = max(arms[a]["contacts_per_episode"] for a in present) or 1

    recovery_rows = [(a, arms[a]["recovery_rate"] * 100, f"{arms[a]['recovery_rate']:.1%}")
                     for a in present]
    gross_rows = [(a, arms[a]["gross_per_episode"], format_inr(int(arms[a]["gross_per_episode"])))
                  for a in present]
    net_rows = [(a, max(0.0, arms[a]["net_after_ext_per_episode"]),
                 format_inr(int(arms[a]["net_after_ext_per_episode"]))) for a in present]
    harm_rows = [(a, arms[a]["forbidden_retries_per_1000"],
                  f"{arms[a]['forbidden_retries_per_1000']:.0f}") for a in present]
    contact_rows = [(a, arms[a]["contacts_per_episode"], f"{arms[a]['contacts_per_episode']:.2f}")
                    for a in present]

    routing = ""
    if model:
        total = model["from_history"] + model["deterministic"] + model["to_model"] or 1
        segs = [("Answered from history", model["from_history"], "s3"),
                ("Outage detector or base rates", model["deterministic"], "s4"),
                ("Sent to the model", model["to_model"], "s1")]
        bars = "".join(
            f'<span class="seg {cls}" style="width:{v / total * 100:.2f}%" '
            f'title="{esc(name)}: {v:,}"></span>' for name, v, cls in segs
        )
        legend = "".join(
            f'<span class="key"><i class="dot {cls}"></i>{esc(name)} '
            f'<b>{v:,}</b> <span class="muted">({v / total:.0%})</span></span>'
            for name, v, cls in segs
        )
        routing = (f'<div class="stack">{bars}</div><div class="legend">{legend}</div>'
                   f'<p class="note">The model is consulted on '
                   f'<b>{model["to_model"] / total:.0%}</b> of episodes. A wording resolved '
                   f'before is answered by lookup; text that identifies nothing is answered '
                   f'by the outage detector or by base rates. Both are optimal on their own '
                   f'ground and both are free.</p>')

    accuracy = grouped_accuracy(head) if head else "<p class='note'>Rules-only run.</p>"
    overall = ""
    if head:
        def tot(name: str) -> float:
            got = sum(v["correct"] for v in head[name].values())
            n = sum(v["n"] for v in head[name].values())
            return got / n if n else 0.0
        overall = (f'<div class="pair"><div><span class="big">{tot("model"):.1%}</span>'
                   f'<span class="cap">Wapas agent</span></div>'
                   f'<div><span class="big muted-num">{tot("rules"):.1%}</span>'
                   f'<span class="cap">Keyword rules</span></div>'
                   f'<div><span class="big muted-num">{summary["oracle"]["overall"]:.1%}</span>'
                   f'<span class="cap">Single-episode ceiling</span></div></div>')

    audit = summary["audit"]
    return f"""<title>Wapas — recovery evidence</title>
<style>
  .viz-root {{
    color-scheme: light;
    --surface-1:#fcfcfb; --surface-2:#f4f4f2; --line:#e2e2dd;
    --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#78766f;
    --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4;
    --good:#008300;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) .viz-root {{
      color-scheme: dark;
      --surface-1:#1a1a19; --surface-2:#232322; --line:#35352f;
      --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#96958c;
      --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
    }}
  }}
  :root[data-theme="dark"] .viz-root {{
    color-scheme: dark;
    --surface-1:#1a1a19; --surface-2:#232322; --line:#35352f;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#96958c;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
  }}
  body {{ margin:0; background:var(--surface-1); }}
  .viz-root {{
    background:var(--surface-1); color:var(--text-primary);
    font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    /* A dashboard is scanned, not read top to bottom, so charts and tables get
       the width and prose does not. Capping the whole page at 980px left a
       wide monitor mostly empty; letting it run to the window edge would set
       running text at 200 characters a line. The container is wide, and the
       few blocks that are actually prose are constrained separately below. */
    max-width:1440px; margin:0 auto; padding:40px clamp(20px, 4vw, 56px) 72px;
  }}
  /* Reading measure, applied only to text that is read as sentences. */
  .sub, .note, .cap, .btitle {{ max-width:74ch; }}
  h1 {{ font-size:27px; margin:0 0 4px; letter-spacing:-.02em; }}
  h2 {{ font-size:17px; margin:44px 0 6px; letter-spacing:-.01em; }}
  .sub {{ color:var(--text-secondary); margin:0 0 8px; }}
  .note {{ color:var(--text-secondary); font-size:13.5px; margin:10px 0 0; }}
  .muted {{ color:var(--text-muted); font-weight:400; }}
  .hero {{ display:flex; flex-wrap:wrap; gap:14px; margin:22px 0 8px; }}
  .tile {{ flex:1 1 200px; background:var(--surface-2); border:1px solid var(--line);
           border-radius:10px; padding:16px 18px; }}
  .tile .n {{ font-size:26px; font-weight:640; letter-spacing:-.02em; display:block; }}
  .tile .c {{ color:var(--text-secondary); font-size:12.5px; display:block; margin-top:3px; }}
  .ok {{ color:var(--good); }}
  .chart {{ margin-top:10px; overflow-x:auto; }}
  .tablewrap {{ overflow-x:auto; }}
  .row {{ display:flex; align-items:center; gap:10px; margin:5px 0; }}
  .rlabel {{ width:var(--label-w,150px); flex:none; color:var(--text-secondary);
             font-size:13px; text-align:right; }}
  .rlabel.small {{ width:112px; }}
  /* Fluid. Fixed at 420px the bars ignored every pixel the page gained, which
     is what made the layout look empty rather than merely narrow. min-width:0
     lets a flex child actually shrink below its content on small screens. */
  .track {{ flex:1 1 auto; min-width:0; }}
  .bar {{ display:block; height:15px; border-radius:0 4px 4px 0; }}
  .rvalue {{ width:var(--value-w,108px); flex:none; font-variant-numeric:tabular-nums;
             font-size:13px; color:var(--text-primary); }}
  .s1{{background:var(--s1)}} .s2{{background:var(--s2)}} .s3{{background:var(--s3)}}
  .s4{{background:var(--s4)}} .s5{{background:var(--s5)}}
  .bucket {{ margin:14px 0; }}
  .btitle {{ font-size:13.5px; color:var(--text-primary); margin-bottom:3px; font-weight:560; }}
  .stack {{ display:flex; height:26px; border-radius:6px; overflow:hidden; margin-top:12px;
            gap:2px; background:var(--surface-1); }}
  .seg {{ display:block; height:100%; }}
  .legend {{ display:flex; flex-wrap:wrap; gap:18px; margin-top:10px; font-size:13px;
             color:var(--text-secondary); }}
  .key {{ display:flex; align-items:center; gap:7px; }}
  .dot {{ width:10px; height:10px; border-radius:3px; display:inline-block; }}
  .pair {{ display:flex; gap:34px; margin:16px 0 4px; flex-wrap:wrap; }}
  .big {{ font-size:31px; font-weight:640; letter-spacing:-.02em; display:block; }}
  .muted-num {{ color:var(--text-secondary); }}
  .cap {{ font-size:12.5px; color:var(--text-secondary); }}
  table {{ border-collapse:collapse; width:100%; margin-top:12px; font-size:13.5px; }}
  th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--text-secondary); font-weight:560; }}
  td.num {{ font-variant-numeric:tabular-nums; }}
  footer {{ margin-top:48px; padding-top:16px; border-top:1px solid var(--line);
            color:var(--text-muted); font-size:12.5px; }}
</style>

<div class="viz-root">
<h1>Wapas — revenue that came back</h1>
<p class="sub">Seed <code>{esc(summary['seed'])}</code> · {summary['episodes']:,} episodes ·
{'LLM agent' if summary.get('llm') else 'rules-only'} ·
generated {esc(generated)} by <code>make dashboard</code></p>

<div class="hero">
  <div class="tile"><span class="n">{format_inr(int(net))}</span>
    <span class="c">Net incremental recovery, after the cost of every opt-out</span></div>
  <div class="tile"><span class="n">{control['recovery_rate']:.0%}</span>
    <span class="c">Recovered with no intervention at all — subtracted, not claimed</span></div>
  <div class="tile"><span class="n ok">{treat['forbidden_retries_per_1000']:.0f}</span>
    <span class="c">Forbidden retries per 1,000 episodes
      (fixed ladder: {arms['baseline_naive']['forbidden_retries_per_1000']:.0f})</span></div>
  <div class="tile"><span class="n ok">{'intact' if audit['intact'] else 'BROKEN'}</span>
    <span class="c">Audit chain, {audit['entries']:,} entries verified</span></div>
</div>

<h2>Diagnosis is three problems, not one</h2>
<p class="sub">Both classifiers over the same {summary['episodes']:,} episodes. A lookup over
resolved history is <em>optimal</em> on text it has seen — the model earns its place on the
column where the wording is new.</p>
{overall}
{accuracy}

<h2>Where each episode is answered</h2>
{routing}

<h2>Recovery rate</h2>
{bar_chart(recovery_rows, max_value=100.0)}

<h2>Gross recovered per episode</h2>
<p class="sub">The fixed ladder leads here. The next two charts are why that is not the
whole story.</p>
{bar_chart(gross_rows, max_value=max_gross)}

<h2>Forbidden retries per 1,000 episodes</h2>
<p class="sub">Re-presenting a payment against a dead card, a risk decline or a revoked
mandate. The ladder buys its recovery with behaviour a payments team could not defend
to an acquirer.</p>
{bar_chart(harm_rows, max_value=max_harm, value_width=70)}

<h2>Contacts per episode</h2>
{bar_chart(contact_rows, max_value=max_contacts, value_width=70)}

<h2>Net per episode, after the modelled cost of opt-outs</h2>
{bar_chart(net_rows, max_value=max(r[1] for r in net_rows) or 1)}

<h2>Every number above, as a table</h2>
<div class="tablewrap">
<table>
  <tr><th>Arm</th><th>n</th><th>Recovery</th><th>Gross / ep</th>
      <th>Net after ext. / ep</th><th>Contacts / ep</th><th>Opt-outs</th>
      <th>Forbidden retries / 1,000</th></tr>
  {"".join(
      f"<tr><td>{esc(LABEL.get(a, a))}</td><td class='num'>{arms[a]['n']:,}</td>"
      f"<td class='num'>{arms[a]['recovery_rate']:.1%}</td>"
      f"<td class='num'>{format_inr(int(arms[a]['gross_per_episode']))}</td>"
      f"<td class='num'>{format_inr(int(arms[a]['net_after_ext_per_episode']))}</td>"
      f"<td class='num'>{arms[a]['contacts_per_episode']:.2f}</td>"
      f"<td class='num'>{arms[a]['opt_out_rate']:.1%}</td>"
      f"<td class='num'>{arms[a]['forbidden_retries_per_1000']:.1f}</td></tr>"
      for a in present)}
</table>
</div>

<footer>
  In-simulation results from published generative parameters the agent never reads
  (<code>sim/params.yaml</code>). Reproduce with <code>make eval-llm &amp;&amp; make dashboard</code>.
  Full report, statistical calibration, sensitivity sweep and adversarial suite are in
  <code>results/</code>.
</footer>
</div>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", default="results/summary.json")
    ap.add_argument("--out", default="results/dashboard.html")
    args = ap.parse_args()

    path = Path(args.summary)
    if not path.exists():
        print(f"{path} not found — run `make eval-llm` first", file=sys.stderr)
        return 1

    summary = json.loads(path.read_text(encoding="utf-8"))
    generated = _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    out = Path(args.out)
    out.write_text(build(summary, generated), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
