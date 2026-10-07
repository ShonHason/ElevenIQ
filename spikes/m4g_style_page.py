"""The style map page (plan 005 M4g1): rendering, hover cards and their tracing check.

Display only: it receives the PCA, the labels and the parquet cells from m4g_style_map and
computes no statistic beyond the projection itself. Never imports eleveniq.

Page (self-contained HTML, inline SVG and JS, no network):
- zoom (wheel / pinch), drag to pan, "Reset view"; name labels for visible points when
  zoomed in (greedy, no overlaps); a player search that highlights, zooms and pins the card;
- points coloured by fine role (m4g_common; FB-WB, DM-CM, AM-Winger), or only the mixed rows
  (CB, ST), with a toggle per role;
- a hover card in plain words: name · team · league · minutes · role; where the player sits
  on each axis ("leans toward …, strong / slight", or "near the middle"); the three largest
  pushes per axis as sentences. Internals (role minute shares, the DM-CM slot label) sit
  behind a "Details" toggle, open once a card is pinned by a click;
- an explainer panel: how to read the map, what each axis end means (its three strongest
  metrics), how much of the variance is shown, and what the colours mean.

Every number in a card names its source (data-id: season parquet; data-cfg: league label;
data-dna: player_dna.parquet pct_local; data-pca: the projection of those cells with the model
written into the page; data-role / data-lab: lineup minutes of this run). check_page re-derives
each one from the parquets after the page is written; a mismatch, or a digit outside a traced
span other than the fixed STATIC_TEXT, fails the run.

Wording rules (fixed here):
- percentile p (local, Hazen): p ≥ 90 → "Very high …, top X%" with X = ⌈100 − p⌉;
  50 ≤ p < 90 → "High …, above average"; 10 < p < 50 → "Low …, below average";
  p ≤ 10 → "Very low …, bottom X%" with X = max(1, ⌈p⌉); X is at least 1;
- axis names read as the map does: PC1 left → right, PC2 bottom → top (axis_title);
- position on an axis: z = score / SD of the plotted scores; |z| < 0.5 "near the middle",
  < 1.5 "slight", otherwise "strong";
- a push is loading × (weighted value − group mean); its sign names the end it pulls toward.
"""
from __future__ import annotations

import html
import html.parser
import json
import math
import re
from pathlib import Path

import polars as pl

import m4g_common as cm

W, H, PAD = 900, 640, 48
NEAR, STRONG = 0.5, 1.5              # |z| bands for the lean wording
# Display rule (not pre-registered; chosen after seeing the loadings, user 2026-10-07): an axis
# end is described by loadings at least this large, compared unrounded. It sits below 1/√k,
# the loading of an even spread over a group's k = 16–24 DNA metrics (0.20–0.25), so a weaker
# loading does not name an end. Page text only, never a score.
END_MIN_LOADING = 0.15
LABEL_ZOOM = 1.8                     # name labels appear from this zoom factor

GROUP_PLURAL = {"CB": "centre-backs", "FB-WB": "full-backs and wing-backs",
                "DM-CM": "central midfielders", "AM-Winger": "attacking midfielders and wingers",
                "ST": "strikers"}
# Roles coloured per group, in the categorical slot order; other non-mixed roles are neutral.
ROLE_COLOURS = {"FB-WB": ("LB", "RB", "LWB", "RWB"), "DM-CM": ("DM", "CM"),
                "AM-Winger": ("LW", "RW", "AM"), "CB": (), "ST": ()}
PIVOT_WORDS = {"single": "single pivot (one holding midfielder)",
               "double": "double pivot (two holding midfielders side by side)",
               "cm": "central-midfield slot", "mixed": "mixed"}
SLOT_WORDS = {"6": "6 (holding slot)", "8": "8 (central-midfield slot)", "mixed": "mixed"}
ROLE_RULE = ("A role is the lineup position where a player spent at least 70% of his minutes; "
             "otherwise he is shown as mixed, with his two main roles.")
SLOT_RULE = ("Within central midfield, a 6 spent at least 70% of those minutes in a "
             "defensive-midfield slot, an 8 at least 70% in a central-midfield slot.")
# Fixed text in a card that contains digits (checked: nothing else may).
STATIC_TEXT = ("PC1", "PC2", ROLE_RULE, SLOT_RULE, *SLOT_WORDS.values())

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e3e1da;--card:#ffffff;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--mix:#8d8b83;--neu:#2a78d6}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a3a19a;--line:#33322e;
--card:#1f1f1c;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--mix:#86847c;--neu:#3987e5}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:24px 16px 64px}
main{max-width:1100px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.muted,.g{color:var(--muted)}.g{font-size:11px}
.warn{border-left:3px solid var(--s2);padding:8px 12px;background:var(--card);margin:12px 0}
.explain{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:12px 0}
.explain summary{font-weight:600;cursor:pointer}.explain p,.explain li{margin:6px 0}
.hero{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}.hero div{background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:10px 14px}.hero b{font-size:22px;display:block}
.bar{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:10px 0}
.bar input[type=search]{font:inherit;padding:4px 8px;border:1px solid var(--line);border-radius:6px;
background:var(--card);color:var(--fg);margin-left:6px;width:220px}
.bar button{font:inherit;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);
color:var(--fg);cursor:pointer}
.chip{display:inline-flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;background:var(--card);margin:8px 0}
th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left}td.n{text-align:right;font-variant-numeric:tabular-nums}
svg.map{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:8px;
touch-action:none;cursor:grab;display:block}
svg text{fill:var(--fg);font-size:12px}svg .axis{stroke:var(--line)}svg .end{fill:var(--muted);font-size:12px;font-weight:600}
svg text.lbl,svg text.end,svg text.nm{paint-order:stroke;stroke:var(--card);stroke-width:4px;stroke-linejoin:round}
svg text.nm{font-size:11px}svg text,.face{pointer-events:none}
.mk{stroke:var(--card);stroke-width:1}.c1{fill:var(--s1)}.c2{fill:var(--s2)}.c3{fill:var(--s3)}.c4{fill:var(--s4)}
.cmix{fill:none;stroke:var(--mix);stroke-width:1.5}.cneu{fill:var(--neu);opacity:.45}
.face{fill:none;stroke:var(--fg);stroke-width:2}.hitbox{fill:transparent;stroke:none;pointer-events:all}
g[data-i]{cursor:pointer}g.dim{opacity:.1}g.hit .mk,g.hit .cmix{stroke:var(--fg);stroke-width:2.5}
svg.key{width:14px;height:14px;flex:none;background:none;border:0}
.tip{position:fixed;z-index:10;max-width:380px;background:var(--card);color:var(--fg);border:1px solid var(--line);
border-radius:8px;padding:10px 12px;font-size:12.5px;box-shadow:0 4px 18px rgba(0,0,0,.18);pointer-events:none}
.tip.pinned{pointer-events:auto}.tip ul{margin:2px 0 6px;padding-left:16px}.tip li{margin:2px 0}
.tip .ax{margin-top:8px;font-weight:600}.tip .x{position:absolute;top:4px;right:6px;border:0;background:none;
color:var(--muted);font-size:16px;cursor:pointer}.tip details{margin-top:6px}.tip summary{cursor:pointer;color:var(--muted)}
"""

JS = r"""
(()=>{const D=JSON.parse(document.getElementById('pts').textContent);
const svg=document.querySelector('svg.map'),tip=document.getElementById('tip');
const W=D.W,H=D.H,G=[...svg.querySelectorAll('g[data-i]')];
const ax=document.getElementById('axx'),ay=document.getElementById('axy'),NM=document.getElementById('names');
let k=1,tx=0,ty=0,pinned=null,hits=null;const hidden=new Set();
const norm=s=>s.normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase();
function sx(i){return D.xy[i][0]*k+tx}function sy(i){return D.xy[i][1]*k+ty}
function apply(){for(const g of G){const i=+g.dataset.i;
g.setAttribute('transform','translate('+sx(i).toFixed(1)+' '+sy(i).toFixed(1)+')');}
const ox=D.o[0]*k+tx,oy=D.o[1]*k+ty;ax.setAttribute('y1',oy);ax.setAttribute('y2',oy);
ay.setAttribute('x1',ox);ay.setAttribute('x2',ox);labels();if(pinned!==null)place(pinned);}
function labels(){NM.textContent='';if(k<D.labelZoom)return;const occ=[];
const order=G.map(g=>+g.dataset.i).filter(i=>!hidden.has(i)&&!D.face[i]&&(!hits||!hits.length||hits.includes(i)));
for(const i of order){const x=sx(i),y=sy(i);if(x<0||x>W||y<0||y>H)continue;
const w=D.short[i].length*6.2+6,r=[x+9,y-9,x+9+w,y+5];if(r[2]>W)continue;
if(occ.some(o=>r[0]<o[2]&&r[2]>o[0]&&r[1]<o[3]&&r[3]>o[1]))continue;occ.push(r);
const t=document.createElementNS('http://www.w3.org/2000/svg','text');t.setAttribute('class','nm');
t.setAttribute('x',(x+9).toFixed(1));t.setAttribute('y',(y+3).toFixed(1));t.textContent=D.short[i];
NM.appendChild(t);if(occ.length>=250)break;}}
function pt(e){const r=svg.getBoundingClientRect();return{x:(e.clientX-r.left)*W/r.width,y:(e.clientY-r.top)*H/r.height}}
function setView(nk,cx,cy,f){k=Math.min(40,Math.max(1,nk));tx=cx-(cx-tx)*f;ty=cy-(cy-ty)*f;
if(k===1){tx=0;ty=0}apply()}
function zoomAt(x,y,f){const nk=Math.min(40,Math.max(1,k*f));setView(nk,x,y,nk/k)}
function centreOn(bx,by,nk){k=Math.min(40,Math.max(1,nk));tx=W/2-bx*k;ty=H/2-by*k;apply()}
function card(i,pin){tip.innerHTML=D.tips[i]+(pin?'<button class="x" aria-label="Close">×</button>':'');
tip.classList.toggle('pinned',pin);tip.hidden=false}
function at(x,y){const r=tip.getBoundingClientRect();let L=x+14,T=y+14;
if(L+r.width>innerWidth-8)L=Math.max(8,x-14-r.width);if(T+r.height>innerHeight-8)T=Math.max(8,y-14-r.height);
tip.style.left=L+'px';tip.style.top=T+'px'}
function place(i){const r=G.find(g=>+g.dataset.i===i).getBoundingClientRect();at(r.right,r.bottom)}
function pin(i){pinned=i;card(i,true);place(i)}
function unpin(){pinned=null;tip.hidden=true}
function pick(e){const el=document.elementFromPoint(e.clientX,e.clientY);const g=el&&el.closest('g[data-i]');
return g&&!hidden.has(+g.dataset.i)?+g.dataset.i:null}
svg.addEventListener('wheel',e=>{e.preventDefault();const p=pt(e);zoomAt(p.x,p.y,Math.exp(-e.deltaY*0.0015))},{passive:false});
const P=new Map();let drag=null,pinch=null,moved=false;
svg.addEventListener('pointerdown',e=>{try{svg.setPointerCapture(e.pointerId)}catch(_){}P.set(e.pointerId,pt(e));moved=false;
if(P.size===1){const p=pt(e);drag={x:p.x,y:p.y,tx,ty}}
else if(P.size===2){const[a,b]=[...P.values()];pinch={d:Math.hypot(a.x-b.x,a.y-b.y)||1,k,tx,ty,
mx:(a.x+b.x)/2,my:(a.y+b.y)/2};drag=null}});
svg.addEventListener('pointermove',e=>{if(P.has(e.pointerId)){P.set(e.pointerId,pt(e));
if(P.size===2&&pinch){const[a,b]=[...P.values()];const nk=Math.min(40,Math.max(1,pinch.k*Math.hypot(a.x-b.x,a.y-b.y)/pinch.d));
const f=nk/pinch.k;k=nk;tx=pinch.mx-(pinch.mx-pinch.tx)*f;ty=pinch.my-(pinch.my-pinch.ty)*f;
if(k===1){tx=0;ty=0}moved=true;apply();return}
if(drag){const p=pt(e),dx=p.x-drag.x,dy=p.y-drag.y;if(Math.abs(dx)+Math.abs(dy)>4)moved=true;
if(moved){if(k>1){tx=drag.tx+dx;ty=drag.ty+dy;apply()}if(pinned===null)tip.hidden=true;return}}}
if(pinned!==null||e.pointerType!=='mouse')return;const i=pick(e);
if(i===null){tip.hidden=true;return}card(i,false);at(e.clientX,e.clientY)});
svg.addEventListener('pointerup',e=>{P.delete(e.pointerId);if(P.size<2)pinch=null;
if(P.size===0){drag=null;if(!moved){const i=pick(e);if(i===null)unpin();else pin(i)}}});
svg.addEventListener('pointerleave',()=>{if(pinned===null)tip.hidden=true});
tip.addEventListener('click',e=>{if(e.target.closest('.x'))unpin()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')unpin()});
document.getElementById('reset').addEventListener('click',()=>{unpin();k=1;tx=0;ty=0;apply()});
const q=document.getElementById('q'),cnt=document.getElementById('cnt');
function search(){const v=norm(q.value.trim());unpin();hits=[];
for(const g of G){const i=+g.dataset.i,h=!!v&&!hidden.has(i)&&norm(D.names[i]).includes(v);
g.classList.toggle('hit',h);g.classList.toggle('dim',!!v&&!h);if(h)hits.push(i)}
for(const i of hits){const g=G.find(x=>+x.dataset.i===i);g.parentNode.appendChild(g)}
cnt.textContent=v?(hits.length===1?'1 match':hits.length+' matches'):'';
if(!hits.length){if(!v){k=1;tx=0;ty=0;apply()}return}
const xs=hits.map(i=>D.xy[i][0]),ys=hits.map(i=>D.xy[i][1]);
const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
const nk=hits.length===1?Math.max(k,4):Math.min(40,0.85*Math.min(W/(x1-x0+60),H/(y1-y0+60)));
centreOn((x0+x1)/2,(y0+y1)/2,nk);if(hits.length===1)pin(hits[0])}
q.addEventListener('input',search);
for(const c of document.querySelectorAll('input[data-role]'))c.addEventListener('change',()=>{
const role=c.dataset.role;for(const g of G){const i=+g.dataset.i;if(D.role[i]!==role)continue;
if(c.checked)hidden.delete(i);else hidden.add(i);g.style.display=c.checked?'':'none'}
if(pinned!==null&&hidden.has(pinned))unpin();labels()});
apply()})();
"""


def esc(s: object) -> str:
    return html.escape(str(s))


def kref(key: tuple) -> str:
    return "|".join(str(v) for v in key)


def span(attr: str, ref_: str, text: str) -> str:
    return f'<span {attr}="{esc(ref_)}">{esc(text)}</span>'


# ── Wording (also used by the check) ─────────────────────────────────────────

def pct_words(p: float) -> tuple[str, str]:
    """(adjective, phrase) for a local percentile."""
    if not 0 <= p <= 100:
        cm.fail(f"percentile out of range: {p}")
    if p >= 90:
        return "Very high", f"top {max(1, math.ceil(100 - p - 1e-9))}%"
    if p >= 50:
        return "High", "above average"
    if p > 10:
        return "Low", "below average"
    return "Very low", f"bottom {max(1, math.ceil(p - 1e-9))}%"


def lc(label: str) -> str:
    """A label inside a sentence: first letter lowered unless it starts an acronym (xG)."""
    return label if label[1:2].isupper() else label[:1].lower() + label[1:]


def contributions(model: dict, pcts: dict[str, float], axis: int) -> list[tuple[str, float]]:
    """Each metric's push along the axis: loading × (weighted value − mean); they sum to the
    score. Sorted by |push|, ties by metric name."""
    out = [(m, c * (pcts[m] / 100.0 * math.sqrt(wm) - mu))
           for m, wm, mu, c in zip(model["metrics"], model["weights"], model["mean"],
                                   model["components"][axis])]
    return sorted(out, key=lambda t: (-abs(t[1]), t[0]))


def end_metrics(model: dict, axis: int, sign: int,
                threshold: float = END_MIN_LOADING) -> list[str]:
    """The (up to 3) metrics defining one end: loadings with sign·c ≥ threshold, compared
    unrounded (−0.1498 shows as −0.150 but does not reach 0.15)."""
    cs = sorted(zip(model["components"][axis], model["metrics"]), key=lambda t: (-sign * t[0], t[1]))
    return [m for c, m in cs[:3] if sign * c >= threshold]


def end_name(names, model: dict, labels: dict[str, str], axis: int, sign: int) -> str:
    """The user's name for an axis end, or a description by its metrics when unnamed."""
    if names[axis] is not None:
        return names[axis][0 if sign > 0 else 1]
    ms = end_metrics(model, axis, sign)
    if ms:
        return f"the PC{axis + 1} {'+' if sign > 0 else '−'} end (" + ", ".join(
            labels[m] for m in ms[:2]) + ")"
    other = end_metrics(model, axis, -sign)
    return (f"the PC{axis + 1} {'+' if sign > 0 else '−'} end (low "
            + ", ".join(labels[m] for m in other[:2]) + ")")


def axis_title(name: tuple[str, str]) -> str:
    """An axis name in the order the eye reads the map (user, 2026-10-07): PC1 left → right,
    PC2 bottom → top, i.e. the negative end first. Names are stored (positive, negative)."""
    return f"{name[1]} ↔ {name[0]}"


def lean(score: float, sd: float, names, model: dict, labels: dict[str, str], axis: int) -> str:
    z = score / sd
    if abs(z) < NEAR:
        if names[axis] is not None:
            return f"Near the middle of {axis_title(names[axis])}"
        return f"Near the middle of PC{axis + 1}"
    strength = "slight" if abs(z) < STRONG else "strong"
    return f"Leans toward {end_name(names, model, labels, axis, 1 if score > 0 else -1)} ({strength})"


# ── Card ─────────────────────────────────────────────────────────────────────

def tooltip(ctx: dict, i: int) -> str:
    sm, names, labels = ctx["sm"], ctx["names"], ctx["labels"]
    r = sm["rows"][i]
    k = kref(r["key"])
    ident = ctx["ident"][r["key"]]
    role = ctx["roles"][r["key"]]
    pcts = {m: r["cells"][m]["pct_local"] for m in sm["metrics"]}
    league = ctx["leagues"][r["key"][2]]
    minutes = f"{ident['minutes_played']:,.0f}"
    out = [f'<div><b>{span("data-id", k + "|player_name", ident["player_name"])}</b></div>',
           f'<div>{span("data-id", k + "|team_name", ident["team_name"])} · '
           f'{span("data-cfg", "league|" + str(r["key"][2]), league)} · '
           f'{span("data-id", k + "|minutes_played", minutes)} min</div>',
           f'<div>{span("data-role", k + "|role", cm.role_words(role))}</div>']
    for a in (0, 1):
        score = float(sm["scores"][i, a])
        where = lean(score, sm["sd"][a], names, sm["model"], labels, a)
        out.append(f'<div class="ax">{span("data-pca", f"{k}|pc{a + 1}|lean", where)} '
                   f'<span class="g">PC{a + 1} score '
                   f'{span("data-pca", f"{k}|pc{a + 1}", f"{score:+.3f}")}</span></div><ul>')
        for m, c in contributions(sm["model"], pcts, a)[:3]:
            adj, words = pct_words(pcts[m])
            toward = end_name(names, sm["model"], labels, a, 1 if c > 0 else -1)
            out.append(f'<li>{esc(adj)} {esc(lc(labels[m]))} '
                       f'— {span("data-dna", f"{k}|{m}|pct_local|words", words)} among '
                       f'{span("data-cfg", "league|" + str(r["key"][2]), league)} '
                       f'{esc(GROUP_PLURAL[sm["group"]])} → pulls toward '
                       f'{span("data-pca", f"{k}|pc{a + 1}|{m}|toward", toward)} '
                       f'<span class="g">push {span("data-pca", f"{k}|pc{a + 1}|{m}", f"{c:+.3f}")}'
                       f'</span></li>')
        out.append("</ul>")
    det = ["<details><summary>Details</summary><div>Minutes by role: "]
    det.append(", ".join(
        f'{span("data-role", f"{k}|share|{rl}", f"{s:.0%}")} {esc(cm.ROLE_WORDS[rl].lower())}'
        for rl, s in role["top"] if s >= 0.01) + ".</div>")
    det.append(f"<div class=\"g\">{esc(ROLE_RULE)}</div>")
    lab = ctx["lab"]
    if lab is not None:
        lb = lab[r["key"]]
        share6 = format(lb["share6"], ".0%")
        det.append(f'<div>Central-midfield slot: {span("data-lab", k + "|label", SLOT_WORDS[lb["label"]])}; '
                   f'{span("data-lab", k + "|share6", share6)} '
                   f'of his central-midfield minutes in a defensive-midfield slot; pivot: '
                   f'{span("data-lab", k + "|pivot", PIVOT_WORDS[lb["pivot"]])}.</div>'
                   f'<div class="g">{esc(SLOT_RULE)}</div>')
    det.append("</details>")
    out.extend(det)
    return "".join(out)


# ── Check: every number in every card, re-derived from the parquets ──────────

class _Spans(html.parser.HTMLParser):
    ATTRS = ("data-id", "data-cfg", "data-dna", "data-pca", "data-lab", "data-role")

    def __init__(self) -> None:
        super().__init__()
        self.spans: list[tuple[str, str, str]] = []
        self.loose: list[str] = []
        self._open: tuple[str, str] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hit = [x for x in self.ATTRS if x in a]
        if tag == "span" and hit:
            self._open, self._text = (hit[0], a[hit[0]]), []

    def handle_endtag(self, tag):
        if tag == "span" and self._open:
            self.spans.append((*self._open, "".join(self._text)))
            self._open = None

    def handle_data(self, data):
        (self._text if self._open else self.loose).append(data)


def _key(parts: list[str]) -> tuple:
    return tuple(int(x) if x.lstrip("-").isdigit() else x for x in parts[:5])


def check_page(path: Path, group: str, weights: dict[str, float], dna_parquet: Path,
               season_parquet: Path, key_cols: tuple[str, ...], leagues: dict[int, str],
               labels: dict[str, str], names, roles: dict[tuple, dict],
               lab: dict[tuple, dict] | None) -> int:
    """
    Re-derive every traced number in every card: identity and minutes from the season parquet,
    percentile wording from player_dna.parquet, every score, push, lean and pull direction by
    projecting the stored pct_local cells with the model written into the page (whose weights
    must be the M4f family weights; the pushes must sum to the plotted score), roles and slot
    labels from this run's lineup minutes. Returns the number of spans checked.
    """
    text = path.read_text()
    data = json.loads(text.split('<script type="application/json" id="pts">', 1)[1]
                      .split("</script>", 1)[0])
    model = data["model"]
    if model["weights"] != [weights[m] for m in model["metrics"]]:
        cm.fail(f"{path.name}: model weights are not the M4f family weights")
    dna = pl.read_parquet(dna_parquet).filter(pl.col("position_group") == group)
    pct = {(kref(tuple(r[c] for c in key_cols)), r["metric"]): r["pct_local"]
           for r in dna.iter_rows(named=True)}
    ident = {kref(tuple(r[c] for c in key_cols)): r for r in pl.read_parquet(season_parquet)
             .select(*key_cols, "player_name", "team_name", "minutes_played").iter_rows(named=True)}
    # Every plotted row's scores from the parquet cells, and the SD the lean wording uses.
    keys = data["keys"]
    pushes = {}
    for kk, s in zip(keys, data["scores"]):
        cells = {m: pct[(kk, m)] for m in model["metrics"]}
        pushes[kk] = [contributions(model, cells, a) for a in (0, 1)]
        for a in (0, 1):
            if abs(sum(c for _, c in pushes[kk][a]) - s[a]) > 1e-9:
                cm.fail(f"{path.name}: {kk} PC{a + 1} plotted at {s[a]}, recomputed differently")
    n_rows = len(keys)
    sd = []
    for a in (0, 1):
        v = [sum(c for _, c in pushes[kk][a]) for kk in keys]
        mu = sum(v) / n_rows
        sd.append(math.sqrt(sum((x - mu) ** 2 for x in v) / (n_rows - 1)))
    static = sorted(STATIC_TEXT + tuple(labels.values()) + tuple(lc(v) for v in labels.values()),
                    key=len, reverse=True)
    n = 0
    for card in data["tips"]:
        p = _Spans()
        p.feed(card)
        loose = "".join(p.loose)
        for s_ in static:
            loose = loose.replace(s_, "")
        if re.search(r"\d", loose):
            cm.fail(f"{path.name}: digits outside traced spans: {loose[:300]}")
        for attr, r_, shown in p.spans:
            parts = r_.split("|")
            k = "|".join(parts[:5])
            if attr == "data-id":
                v = ident[k][parts[5]]
                want = f"{v:,.0f}" if parts[5] == "minutes_played" else str(v)
            elif attr == "data-cfg":
                want = leagues[int(parts[1])]
            elif attr == "data-dna":
                want = pct_words(pct[(k, parts[5])])[1]
            elif attr == "data-role":
                role = roles[_key(parts)]
                want = (cm.role_words(role) if parts[5] == "role"
                        else f"{role['share'][parts[6]]:.0%}")
            elif attr == "data-lab":
                lb = lab[_key(parts)]
                want = {"label": SLOT_WORDS[lb["label"]], "share6": format(lb["share6"], ".0%"),
                        "pivot": PIVOT_WORDS[lb["pivot"]]}[parts[5]]
            else:
                a = int(parts[5][2:]) - 1
                push = dict(pushes[k][a])
                score = sum(push.values())
                top3 = [m for m, _ in pushes[k][a][:3]]
                if len(parts) == 6:
                    want = f"{score:+.3f}"
                elif parts[6] == "lean":
                    want = lean(score, sd[a], names, model, labels, a)
                else:
                    if parts[6] not in top3:
                        cm.fail(f"{path.name}: {k} {parts[6]} is not a top-3 push on PC{a + 1}")
                    c = push[parts[6]]
                    want = (end_name(names, model, labels, a, 1 if c > 0 else -1)
                            if len(parts) == 8 else f"{c:+.3f}")
            if shown != want:
                cm.fail(f"{path.name}: {attr} {r_} shows {shown!r}, source gives {want!r}")
            n += 1
    return n


# ── Map ──────────────────────────────────────────────────────────────────────

def _mark(cls_idx: int | None, mixed: bool, neutral: bool) -> str:
    if mixed:
        return '<circle class="cmix" r="4"/>'
    if neutral:
        return '<circle class="mk cneu" r="4"/>'
    c = f"c{cls_idx + 1}"
    return {0: f'<circle class="mk {c}" r="4.5"/>',
            1: f'<rect class="mk {c}" x="-4" y="-4" width="8" height="8"/>',
            2: f'<path class="mk {c}" d="M0,-5.5L5,3.5L-5,3.5Z"/>',
            3: f'<path class="mk {c}" d="M0,-5.5L5,0L0,5.5L-5,0Z"/>'}[cls_idx]


def point_class(group: str, role: str) -> tuple[int | None, bool, bool, str]:
    """(slot index, mixed, neutral, toggle key) for a row's role in this group's map.
    CB / ST colour only the mixed rows (mixed then takes slot 2, a square)."""
    coloured = ROLE_COLOURS[group]
    if not coloured:
        return (1, False, False, "mixed") if role == "mixed" else (None, False, True, role)
    if role == "mixed":
        return None, True, False, "mixed"
    if role in coloured:
        return coloured.index(role), False, False, role
    return None, False, True, role


def svg_map(ctx: dict, order: list[int]) -> tuple[str, list[list[float]], tuple[float, float]]:
    sm = ctx["sm"]
    s = sm["scores"]
    xs, ys = s[:, 0], s[:, 1]
    extent = max(xs.max() - xs.min(), (ys.max() - ys.min()) * (W - 2 * PAD) / (H - 2 * PAD))
    scale = (W - 2 * PAD) / extent
    cx, cy = (xs.max() + xs.min()) / 2, (ys.max() + ys.min()) / 2

    def px(x: float, y: float) -> tuple[float, float]:
        return W / 2 + (x - cx) * scale, H / 2 - (y - cy) * scale

    ox, oy = px(0.0, 0.0)
    names = ctx["names"]
    lab = lambda a, sg: end_name(names, sm["model"], ctx["labels"], a, sg)  # noqa: E731
    out = [f'<svg class="map" viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="Style map, {esc(sm["group"])}: each dot is a player">',
           f'<line id="axx" class="axis" x1="0" y1="{oy:.1f}" x2="{W}" y2="{oy:.1f}"/>',
           f'<line id="axy" class="axis" x1="{ox:.1f}" y1="0" x2="{ox:.1f}" y2="{H}"/>']
    xy = []
    for di, i in enumerate(order):
        r = sm["rows"][i]
        x, y = px(float(xs[i]), float(ys[i]))
        xy.append([round(x, 2), round(y, 2)])
        idx, mixed, neutral, _ = point_class(sm["group"], ctx["roles"][r["key"]]["label"])
        face = ctx["face"].get((r["name"], r["key"][1]))
        extra = ""
        if face:
            extra = (f'<circle class="face" r="8"/><text class="lbl" x="11" y="4" '
                     f'font-weight="600">{esc(face)}</text>')
        out.append(f'<g data-i="{di}" transform="translate({x:.1f} {y:.1f})">'
                   f'{_mark(idx, mixed, neutral)}{extra}<circle class="hitbox" r="11"/></g>')
    out.append('<g id="names"></g>')
    out += [f'<text class="end" x="{W - 10}" y="{H / 2 - 8:.0f}" text-anchor="end">{esc(lab(0, 1))} →</text>',
            f'<text class="end" x="10" y="{H / 2 - 8:.0f}">← {esc(lab(0, -1))}</text>',
            f'<text class="end" x="{W / 2:.0f}" y="18" text-anchor="middle">↑ {esc(lab(1, 1))}</text>',
            f'<text class="end" x="{W / 2:.0f}" y="{H - 10}" text-anchor="middle">↓ {esc(lab(1, -1))}</text>',
            "</svg>"]
    return "".join(out), xy, (round(ox, 2), round(oy, 2))


def _key_icon(group: str, role: str) -> str:
    idx, mixed, neutral, _ = point_class(group, role)
    return (f'<svg class="key" viewBox="-7 -7 14 14">{_mark(idx, mixed, neutral)}</svg>')


def explainer(ctx: dict, counts: dict[str, int]) -> str:
    sm, names, labels = ctx["sm"], ctx["names"], ctx["labels"]
    g = sm["group"]
    evr = sm["evr"]
    tot = round((evr[0] + evr[1]) * 100)
    axes = []
    for a, (dir_pos, dir_neg, what) in enumerate((("right", "left", "Left ↔ right"),
                                                   ("top", "bottom", "Bottom ↔ top"))):
        title = (axis_title(names[a]) if names[a] is not None
                 else f"not named yet (PC{a + 1})")
        ends = []
        for sign, where in ((1, dir_pos), (-1, dir_neg)):
            ms = end_metrics(sm["model"], a, sign)
            nm = end_name(names, sm["model"], labels, a, sign)
            desc = (", ".join(labels[m] for m in ms) if ms else
                    "no metric loads strongly on this end; it means low values of the "
                    "other end's metrics")
            ends.append(f"<li><b>{esc(where.capitalize())} — {esc(nm)}:</b> high {esc(desc)}</li>"
                        if ms else f"<li><b>{esc(where.capitalize())} — {esc(nm)}:</b> {esc(desc)}</li>")
        note = ctx.get("notes", {}).get(a)
        axes.append(f"<p><b>{what}: {esc(title)}</b> "
                    f"<span class=\"muted\">({round(evr[a] * 100)}% of the differences)</span></p>"
                    f"<ul>{''.join(ends)}</ul>"
                    + (f"<p class=\"muted\">{esc(note)}</p>" if note else ""))
    coloured = ROLE_COLOURS[g]
    keys = []
    for role, n in counts.items():
        word = "mixed role (two main roles)" if role == "mixed" else cm.role_words(
            {"label": role})
        keys.append(f"<li>{_key_icon(g, role)} {esc(word)}: {n} players</li>")
    role_note = [esc(ROLE_RULE)]
    f = ctx["facts"]
    if g == "AM-Winger":
        role_note.append(
            "LW / RW combine the winger slot of a 4-3-3 with the wide-midfield slot of a 4-4-2"
            + ("" if f["wide_mid_share"] is None else f" ({f['wide_mid_share']:.0%} of their minutes)")
            + f", so they partly reflect the team's "
            f"formation. Only {f['am_pure']} players in the pool are pure attacking midfielders "
            f"(AM, the 10); most central 10s are mixed with a wing role.")
    if g == "FB-WB":
        role_note.append(f"Wing-backs (LWB / RWB) are rare: {f['wb_n']} players, in "
                         + " and ".join(f["wb_leagues"]) + ".")
    if g == "DM-CM":
        role_note.append("DM is a defensive-midfield lineup slot and CM a central-midfield slot. "
                         "The slot partly reflects formation: a double pivot is recorded as two "
                         "DM slots, a midfield three as CM slots.")
    if not coloured:
        role_note.append("Only players who split their minutes between two roles are coloured; "
                         "everyone else is a plain dot.")
    return ('<details class="explain" open><summary>How to read this map</summary>'
            "<p>Each dot is a player. Players close together have a similar playing style "
            "within this position group.</p>"
            "<p><b>Position on the map shows playing style, not quality.</b> Left / right and up / "
            "down are style directions, not good / bad.</p>"
            f"<p>This map captures about {tot}% of the style differences between these players; "
            "players who look close may still differ on the rest.</p>"
            + "".join(axes)
            + f"<p><b>Colours and shapes</b></p><ul>{''.join(keys)}</ul>"
            + "".join(f"<p class=\"muted\">{n}</p>" for n in role_note)
            + "<p class=\"muted\">Ringed: well-known reference players. Scroll or pinch to zoom, "
              "drag to pan, hover a dot for its card, click a dot to pin the card and open its "
              "details.</p></details>")


def page(ctx: dict, figs_html: str) -> str:
    sm = ctx["sm"]
    g = sm["group"]
    order = sorted(range(len(sm["rows"])), key=lambda i: sm["rows"][i]["key"])
    svg, xy, origin = svg_map(ctx, order)
    roles_in = [ctx["roles"][sm["rows"][i]["key"]]["label"] for i in order]
    toggles = [point_class(g, rl)[3] for rl in roles_in]
    counts: dict[str, int] = {}
    for t in sorted(set(toggles), key=lambda t: (t == "mixed", cm.ROLES.index(t)
                                                 if t in cm.ROLES else 99)):
        counts[t] = toggles.count(t)
    data = {"W": W, "H": H, "o": origin, "xy": xy, "labelZoom": LABEL_ZOOM,
            "keys": [kref(sm["rows"][i]["key"]) for i in order],
            "scores": [[float(sm["scores"][i, 0]), float(sm["scores"][i, 1])] for i in order],
            "names": [sm["rows"][i]["name"] for i in order],
            "short": [ctx["face"].get((sm["rows"][i]["name"], sm["rows"][i]["key"][1]))
                      or sm["rows"][i]["name"] for i in order],
            "face": [bool(ctx["face"].get((sm["rows"][i]["name"], sm["rows"][i]["key"][1])))
                     for i in order],
            "role": toggles, "model": sm["model"],
            "tips": [tooltip(ctx, i) for i in order]}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    chips = "".join(
        f'<label class="chip"><input type="checkbox" data-role="{esc(t)}" checked>'
        f'{_key_icon(g, t)}{esc("mixed" if t == "mixed" else t)}</label>' for t in counts)
    names = ctx["names"]
    axis_t = [(axis_title(names[a]) if names[a] else f"PC{a + 1} (not named yet)")
              for a in (0, 1)]
    load_rows = []
    for a, ld in enumerate(sm["loadings"]):
        for m, fam, c in ld:
            load_rows.append(f'<tr><td>{esc(axis_t[a])}</td><td>{esc(ctx["labels"][m])} '
                             f'<span class="g">{esc(m)}</span></td><td>{esc(fam)}</td>'
                             f'<td class="n">{c:+.3f}</td></tr>')
    partial = ", ".join(f'{esc(r["name"])} ({esc(r["team"])})' for r in sm["partial"]) or "none"
    evr = sm["evr"]
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Style map {esc(g)}</title><style>{CSS}</style></head><body><main>'
            f'<h1>Style map — {esc(g)}, 2015/16</h1>'
            f'<p class="muted">{len(sm["rows"])} players with a complete DNA vector, '
            f'{sm["n_metrics"]} metrics, four leagues. Registry '
            f'{esc(ctx["meta"]["registry_version"][:12])}…. Report only (plan 005 M4g1).</p>'
            + explainer(ctx, counts)
            + '<p class="warn"><b>A 2D projection distorts distances. The neighbour list (M4f) is '
              'authoritative:</b> two dots close on this map need not be neighbours, and '
              'neighbours can sit apart on it.</p>'
            + f'<div class="hero"><div><b>{(evr[0] + evr[1]) * 100:.0f}%</b>of the style '
              f'differences shown</div><div><b>{evr[0] * 100:.0f}%</b>{esc(axis_t[0])}</div>'
              f'<div><b>{evr[1] * 100:.0f}%</b>{esc(axis_t[1])}</div></div>'
            + '<div class="bar"><label>Find a player <input id="q" type="search" '
              'placeholder="name, accents optional" autocomplete="off"></label>'
              '<span id="cnt" class="muted"></span><button id="reset" type="button">Reset view'
              f'</button><span class="muted">Show:</span>{chips}</div>'
            + svg + '<div id="tip" class="tip" hidden></div>'
            + '<h2>Top loadings per axis</h2><p class="muted">Component coefficients on the '
              'weighted input (sign: the largest absolute loading is positive). A positive '
              'coefficient means the metric rises toward the right / top end.</p>'
              '<div class="scroll"><table><thead><tr><th>Axis</th><th>Metric</th><th>Family</th>'
              '<th>Loading</th></tr></thead><tbody>' + "".join(load_rows)
            + "</tbody></table></div>" + figs_html
            + f'<h2>Not plotted</h2><p>Players with a metric below its minimum (PCA needs every '
              f'metric and ADR 004 §12.2 forbids imputation): {partial}.</p>'
            + f'<script type="application/json" id="pts">{blob}</script><script>{JS}</script>'
            + "</main></body></html>\n")
