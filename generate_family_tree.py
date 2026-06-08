#!/usr/bin/env python3
"""
generate_family_tree.py
Reads a GEDCOM file and outputs an Ancestry-style interactive HTML family tree.
Usage: python generate_family_tree.py *.ged
"""

import re, json, sys, os
from collections import defaultdict
import glob


# ── Parse GEDCOM ──────────────────────────────────────────────────────────────
def parse_gedcom(path):
    """
    Reads a GEDCOM file and extracts individuals and families into structured dicts.
    Returns:
        indis: dict of individual ID → {id: str, name: str, sex: str, birth: str, birthPlace: str, death: str, deathPlace: str}
        fams: dict of family ID → {id: str, husb: str, wife: str, children: list[str]}
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    def date_field(block, tag):
        idx = block.find(f"1 {tag}")
        if idx == -1:
            return ""
        m = re.search(r"2 DATE (.+?)[\n\r]", block[idx : idx + 200])
        return m.group(1).strip() if m else ""

    def place_field(block, tag):
        idx = block.find(f"1 {tag}")
        if idx == -1:
            return ""
        m = re.search(r"2 PLAC (.+?)[\n\r]", block[idx : idx + 300])
        return m.group(1).strip() if m else ""

    indis = {}
    for block in re.split(r"(?=\n0 @)", "\n" + content):
        m = re.match(r"\n0 (@[^@]+@) INDI", block)
        if not m:
            continue
        iid = m.group(1)
        name_m = re.search(r"1 NAME (.+)", block)
        sex_m = re.search(r"1 SEX ([MF])", block)
        name = name_m.group(1).replace("/", "").strip() if name_m else "Unknown"
        indis[iid] = {
            "id": iid,
            "name": name,
            "sex": sex_m.group(1) if sex_m else "?",
            "birth": date_field(block, "BIRT"),
            "birthPlace": place_field(block, "BIRT"),
            "death": date_field(block, "DEAT"),
            "deathPlace": place_field(block, "DEAT"),
        }

    fams = {}
    for block in re.split(r"(?=\n0 @)", "\n" + content):
        m = re.match(r"\n0 (@[^@]+@) FAM", block)
        if not m:
            continue
        fid = m.group(1)
        husb_m = re.search(r"1 HUSB (@[^@]+@)", block)
        wife_m = re.search(r"1 WIFE (@[^@]+@)", block)
        fams[fid] = {
            "id": fid,
            "husb": husb_m.group(1) if husb_m else None,
            "wife": wife_m.group(1) if wife_m else None,
            "children": re.findall(r"1 CHIL (@[^@]+@)", block),
        }
    return indis, fams


# ── Find direct ancestors only ────────────────────────────────────────────────
def find_ancestors(indis, fams, root_id):
    """
    Given individuals and families dicts, find all direct ancestors of root_id.
    Returns:
        ancestors: set of person IDs who are direct ancestors of root_id
        gen: dict of person ID → generation number (0=root, 1=parents, 2=grandparents, etc.)
        couples: dict of family ID → {fid, husb, wife, child} for families linking an ancestor child to their parents
        child_to_fam: dict mapping child ID → family ID they were born into
    """
    # Map each child to the family they were born into
    child_to_fam = {}
    for fid, fam in fams.items():
        for child in fam["children"]:
            child_to_fam[child] = fid

    def get_parents(pid):
        fid = child_to_fam.get(pid)
        if not fid:
            return None, None
        return fams[fid]["husb"], fams[fid]["wife"]

    # BFS upward collecting only direct ancestors
    ancestors = {root_id}
    gen = {root_id: 0}
    queue = [root_id]
    while queue:
        pid = queue.pop(0)
        father, mother = get_parents(pid)
        for parent in filter(None, [father, mother]):
            if parent not in ancestors:
                ancestors.add(parent)
                gen[parent] = gen[pid] + 1
                queue.append(parent)

    # Collect ancestor couples (families linking a child to their parents)
    couples = {}
    for pid in ancestors:
        fid = child_to_fam.get(pid)
        if fid and fid not in couples:
            fam = fams[fid]
            couples[fid] = {
                "fid": fid,
                "husb": fam["husb"],
                "wife": fam["wife"],
                "child": pid,  # the ancestor child they parent
            }

    return ancestors, gen, couples, child_to_fam


# ── Build tree JSON for D3 ────────────────────────────────────────────────────
def build_tree_data(indis, ancestors, gen, couples, root_id):
    """Constructs the data structure for the family tree visualization.
    Returns a dict with:
    - rootId: the ID of the root ancestor
    - nodes: list of {id, name, sex, birth, death, gen} for each ancestor
    - links: list of {source: parentId, target: childId} for parent-children relationships
    - spouseLinks: list of {source: personId, target: spouseId} for spouses within couples
    - couples: list of {fid, husb, wife, child} for each couple linking an ancestor child to their parents
    """
    nodes = [indis[pid] for pid in ancestors if pid in indis]
    for n in nodes:
        n["gen"] = gen.get(n["id"], 0)

    # Links: couple → child (one link per parent)
    links = []
    for c in couples.values():
        if c["husb"] and c["child"]:
            links.append({"source": c["husb"], "target": c["child"]})
        if c["wife"] and c["child"]:
            links.append({"source": c["wife"], "target": c["child"]})

    # Spouse links: within each couple
    spouse_links = []
    seen = set()
    for c in couples.values():
        if c["husb"] and c["wife"]:
            k = tuple(sorted([c["husb"], c["wife"]]))
            if k not in seen:
                seen.add(k)
                spouse_links.append({"source": c["husb"], "target": c["wife"]})

    return {
        "rootId": root_id,
        "nodes": nodes,
        "links": links,
        "spouseLinks": spouse_links,
        "couples": list(couples.values()),
    }


# ── Generate HTML ─────────────────────────────────────────────────────────────
def generate_html(data, out_path, n_total, root_ancestor):
    """
    Generates a self-contained HTML file with embedded data and D3.js visualization.
    The HTML includes inline CSS and JavaScript to render the family tree interactively.
    """
    data_json = json.dumps(data)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Family Tree</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family: -apple-system, "Segoe UI", Arial, sans-serif;
       background:#f0ece4; overflow:hidden; height:100vh; width:100vw; }}

#header {{
  position:fixed; top:0; left:0; right:0; height:48px;
  background:#3b2a1a; display:flex; align-items:center;
  justify-content:space-between; padding:0 18px; z-index:100;
  border-bottom:1px solid #5a3e28;
}}
#header h1 {{ font-size:16px; color:#f0e6d0; font-weight:600; letter-spacing:.02em; }}
#header .sub {{ font-size:11px; color:#b09070; margin-top:1px; }}
#controls {{ display:flex; gap:8px; }}
#controls button {{
  background:#5a3e28; border:1px solid #7a5e42; color:#f0e6d0;
  padding:5px 14px; border-radius:5px; cursor:pointer;
  font-size:12px; transition:background .15s;
}}
#controls button:hover {{ background:#555; }}

/* Person card */
.person-card {{
  position:absolute;
  width:110px; height:64px;
  background:#ffffff;
  border:1px solid #ccc;
  border-radius:5px;
  cursor:pointer;
  transition:box-shadow .15s, border-color .15s;
  overflow:hidden;
  display:flex; flex-direction:column; justify-content:center;
  padding:5px 7px;
}}
.person-card:hover {{ border-color:#8a6030; box-shadow:0 2px 8px rgba(0,0,0,.15); }}
.person-card.root {{ background:#fff8e8; border-color:#c89040; border-width:2px; }}
.person-card.male {{ border-left:4px solid #5b9bd5; }}
.person-card.female {{ border-left:4px solid #d4607a; }}
.person-card .pname {{
  font-size:10px; font-weight:600; color:#1a1208;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  line-height:1.3;
}}
.person-card .pdates {{
  font-size:8.5px; color:#666; margin-top:3px; line-height:1.3;
}}
.person-card .pgen {{
  font-size:8px; color:#999; margin-top:2px;
}}

svg.connectors {{
  position:absolute; top:0; left:0;
  pointer-events:none; overflow:visible;
}}
.connector {{ fill:none; stroke:#aaa; stroke-width:1.5px; }}
.spouse-bar {{ fill:none; stroke:#bbb; stroke-width:1px; stroke-dasharray:3,2; }}

#tooltip {{
  position:fixed; pointer-events:none;
  background:rgba(30,20,10,.95); color:#f0e8d8;
  padding:11px 15px; border-radius:7px; font-size:12px;
  line-height:1.7; max-width:270px; display:none; z-index:200;
  border:1px solid #6a5030; box-shadow:0 4px 20px rgba(0,0,0,.6);
}}
#tooltip strong {{ color:#f0c060; font-size:13px; }}

#canvas {{
  position:absolute; top:48px; left:0; right:0; bottom:0;
  overflow:hidden; cursor:grab;
}}
#canvas:active {{ cursor:grabbing; }}
#world {{ position:absolute; transform-origin:0 0; }}

#legend {{
  position:fixed; bottom:14px; left:14px;
  background:rgba(255,252,245,.95); border-radius:7px;
  padding:9px 13px; z-index:100; font-size:10px;
  color:#7a6050; line-height:1.9; border:1px solid #c8b898;
}}
.ldot {{ display:inline-block; width:9px; height:9px;
         border-radius:2px; margin-right:6px; vertical-align:middle; }}
#hint {{ position:fixed; bottom:14px; right:14px;
  background:rgba(255,252,245,.9); border-radius:5px;
  padding:6px 11px; font-size:10px; color:#999; z-index:100; border:1px solid #ddd; }}
</style>
</head>
<body>

<div id="header">
  <div>
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:18px">🌳</span>
      <div>
        <div style="font-size:16px;color:#f0e6d0;font-weight:600">{root_ancestor} Family Tree</div>
        <div class="sub">Direct ancestors of {root_ancestor} &nbsp;·&nbsp; {len(data["nodes"])} people shown of {n_total} total</div>
      </div>
    </div>
  </div>
  <div id="controls">
    <button onclick="fitView()">Fit View</button>
    <button onclick="findRootAncestor()">Find Root Ancestor</button>
    <button onclick="zoomIn()">＋</button>
    <button onclick="zoomOut()">－</button>
  </div>
</div>

<div id="tooltip"></div>
<div id="legend">
  <div><span class="ldot" style="background:#4a3a1a;border:1px solid #c89040"></span>Root ({root_ancestor})</div>
  <div><span class="ldot" style="border-left:3px solid #5b9bd5;background:#fff"></span>Male ancestor</div>
  <div><span class="ldot" style="border-left:3px solid #d4607a;background:#fff"></span>Female ancestor</div>
  <div style="margin-top:4px;color:#999;font-size:9px">─── parent connection</div>
</div>
<div id="hint">Scroll: zoom &nbsp;|&nbsp; Drag: pan &nbsp;|&nbsp; Click: details</div>

<div id="canvas">
  <div id="world"></div>
</div>

<script>
const DATA = {data_json};

// ── Layout constants ──────────────────────────────────────────────────────
const CW = 130, CH = 72;          // card width/height
const H_MARGIN = 16;              // gap between husband & wife cards
const COUPLE_GAP = 180;           // horizontal space per couple slot
const ROW_HEIGHT = 130;           // vertical distance between generations

// ── Build gen→nodes map ───────────────────────────────────────────────────
const byGen = {{}};
DATA.nodes.forEach(n => {{
  if (!byGen[n.gen]) byGen[n.gen] = [];
  byGen[n.gen].push(n);
}});
const nodeMap = {{}};
DATA.nodes.forEach(n => nodeMap[n.id] = n);

// ── Assign positions ─────────────────────────────────────────────────────────
// Simple approach: count the number of leaf couples at the highest generation,
// then divide horizontal space evenly. Each person gets one slot.
// This prevents exponential width blowup from recursive subtree calculation.

const childToCouple = {{}};
DATA.couples.forEach(c => {{ if (c.child) childToCouple[c.child] = c; }});
const maxGen = Math.max(...DATA.nodes.map(n => n.gen));
const pos = {{}};

// Step 1: find all leaf nodes at the top generation (no parents above them)
// and count total leaves to determine slot width
function countLeaves(personId) {{
  const pc = DATA.couples.find(c => c.child === personId);
  if (!pc) return 1;  // no parents known = leaf
  const hl = pc.husb ? countLeaves(pc.husb) : 0;
  const wl = pc.wife ? countLeaves(pc.wife) : 0;
  return Math.max(1, hl + wl);
}}

// Total leaves under the root ancestor determines overall tree width and slot size
const totalLeaves = countLeaves(DATA.rootId);
const SLOT = CW + 24;  // ← width per leaf slot (card + gap). Tune this for overall width.

// Step 2: assign x positions recursively
// Each person's x = center of their subtree's leaf slots
function assignX(personId, slotLeft) {{
  const pc = DATA.couples.find(c => c.child === personId);
  const leaves = countLeaves(personId);
  const centerX = slotLeft + (leaves * SLOT) / 2 - CW / 2;

  // Place this person centered in their slot range
  const g = DATA.nodes.find(n => n.id === personId)?.gen ?? 0;
  pos[personId] = {{ x: centerX, y: -g * ROW_HEIGHT }};

  if (!pc) return slotLeft + leaves * SLOT;

  // Place husband in left half of slot range, wife in right half
  let cursor = slotLeft;
  if (pc.husb) {{
    const hl = countLeaves(pc.husb);
    const hcenter = cursor + (hl * SLOT) / 2 - CW / 2;
    const hg = DATA.nodes.find(n => n.id === pc.husb)?.gen ?? 0;
    pos[pc.husb] = {{ x: hcenter, y: -hg * ROW_HEIGHT }};
    assignX(pc.husb, cursor);
    cursor += hl * SLOT;
  }}
  if (pc.wife) {{
    const wl = countLeaves(pc.wife);
    const wcenter = cursor + (wl * SLOT) / 2 - CW / 2;
    const wg = DATA.nodes.find(n => n.id === pc.wife)?.gen ?? 0;
    pos[pc.wife] = {{ x: wcenter, y: -wg * ROW_HEIGHT }};
    assignX(pc.wife, cursor);
    cursor += wl * SLOT;
  }}
  return cursor;
}}

// Center the whole tree at x=0
const treeWidth = totalLeaves * SLOT;
assignX(DATA.rootId, -treeWidth / 2);

// ── Render world ──────────────────────────────────────────────────────────
const world = document.getElementById('world');
const OFFSET_X = 600, OFFSET_Y = 500;  // canvas offset so gen0 is near bottom center

// SVG connector layer
const svgNS = 'http://www.w3.org/2000/svg';
const svg = document.createElementNS(svgNS, 'svg');
svg.setAttribute('class', 'connectors');
world.appendChild(svg);

function wx(x) {{ return x + OFFSET_X; }}
function wy(y) {{ return y + OFFSET_Y; }}

// Draw connectors
DATA.couples.forEach(couple => {{
  if (!couple.child || !pos[couple.child]) return;
  const childPos  = pos[couple.child];
  const childCX   = wx(childPos.x) + CW / 2;
  const childTopY = wy(childPos.y);

  const hpos = couple.husb ? pos[couple.husb] : null;
  const wpos = couple.wife ? pos[couple.wife] : null;

  if (hpos && wpos) {{
    const hcx     = wx(hpos.x) + CW / 2;
    const wcx     = wx(wpos.x) + CW / 2;
    const parBotY = wy(hpos.y) + CH;
    const midX    = (hcx + wcx) / 2;
    const barY    = parBotY + 14;       // bar sits 14px BELOW card bottoms — no tail
    const elbowY  = childTopY - 10;

    // Stub down from each card bottom to bar
    [hcx, wcx].forEach(px => {{
      const stub = document.createElementNS(svgNS, 'line');
      stub.setAttribute('class', 'connector');
      stub.setAttribute('x1', px); stub.setAttribute('y1', parBotY);
      stub.setAttribute('x2', px); stub.setAttribute('y2', barY);
      svg.appendChild(stub);
    }});

    // Horizontal bar
    const bar = document.createElementNS(svgNS, 'line');
    bar.setAttribute('class', 'connector');
    bar.setAttribute('x1', hcx); bar.setAttribute('y1', barY);
    bar.setAttribute('x2', wcx); bar.setAttribute('y2', barY);
    svg.appendChild(bar);

    // Drop from bar midpoint to child top
    const drop = document.createElementNS(svgNS, 'path');
    drop.setAttribute('class', 'connector');
    drop.setAttribute('d', `M ${{midX}} ${{barY}} L ${{midX}} ${{elbowY}} L ${{childCX}} ${{elbowY}} L ${{childCX}} ${{childTopY}}`);
    svg.appendChild(drop);

  }} else {{
    const parent   = hpos || wpos;
    if (parent) {{
      const pcx     = wx(parent.x) + CW / 2;
      const parBotY = wy(parent.y) + CH;
      const line    = document.createElementNS(svgNS, 'line');
      line.setAttribute('class', 'connector');
      line.setAttribute('x1', pcx);     line.setAttribute('y1', parBotY);
      line.setAttribute('x2', childCX); line.setAttribute('y2', childTopY);
      svg.appendChild(line);
    }}
  }}
}});


// Draw person cards
DATA.nodes.forEach(person => {{
  const p = pos[person.id];
  if (!p) return;

  const card = document.createElement('div');
  card.className = 'person-card' +
    (person.id === DATA.rootId ? ' root' : '') +
    (person.sex === 'M' ? ' male' : person.sex === 'F' ? ' female' : '');
  card.style.left = wx(p.x) + 'px';
  card.style.top  = wy(p.y) + 'px';

  // Name: first + last only
  const parts = person.name.trim().split(' ');
  const dispName = parts.length <= 2 ? person.name
    : parts[0] + ' ' + parts[parts.length - 1];

  // Date range
  const birthY = (person.birth || '').match(/\d{{4}}/)?.[0] || '';
  const deathY = (person.death || '').match(/\d{{4}}/)?.[0] || '';
  const dateStr = birthY && deathY ? `${{birthY}}–${{deathY}}`
                : birthY ? `b. ${{birthY}}`
                : deathY ? `d. ${{deathY}}` : '';

  const genLabel = person.gen === 0 ? '' :
    person.gen === 1 ? 'Parent' :
    person.gen === 2 ? 'Grandparent' :
    person.gen === 3 ? 'Great-grandparent' :
    `${{person.gen - 2}}× great-grandparent`;

  card.innerHTML = `
    <div class="pname" title="${{person.name}}">${{dispName}}</div>
    ${{dateStr ? `<div class="pdates">${{dateStr}}</div>` : ''}}
    ${{genLabel ? `<div class="pgen">${{genLabel}}</div>` : ''}}
  `;

  card.addEventListener('mouseenter', e => showTip(e, person));
  card.addEventListener('mousemove', moveTip);
  card.addEventListener('mouseleave', hideTip);
  world.appendChild(card);
}});

// ── Pan & zoom ────────────────────────────────────────────────────────────
const canvas = document.getElementById('canvas');
let scale = 1, tx = 0, ty = 0;
let dragging = false, startX, startY, startTx, startTy;

function applyTransform() {{
  world.style.transform = `translate(${{tx}}px,${{ty}}px) scale(${{scale}})`;
}}

canvas.addEventListener('wheel', e => {{
  e.preventDefault();
  const delta = e.deltaY > 0 ? 0.9 : 1.1;
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  tx = mx - (mx - tx) * delta;
  ty = my - (my - ty) * delta;
  scale = Math.max(0.08, Math.min(4, scale * delta));
  applyTransform();
}}, {{passive:false}});

canvas.addEventListener('mousedown', e => {{
  dragging=true; startX=e.clientX; startY=e.clientY; startTx=tx; startTy=ty;
}});
window.addEventListener('mousemove', e => {{
  if (!dragging) return;
  tx = startTx + (e.clientX - startX);
  ty = startTy + (e.clientY - startY);
  applyTransform();
}});
window.addEventListener('mouseup', () => dragging=false);

function fitView() {{
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  // Find bounding box of all positioned nodes
  let minX=Infinity, maxX=-Infinity, minY=Infinity, maxY=-Infinity;
  DATA.nodes.forEach(n => {{
    const p = pos[n.id]; if (!p) return;
    minX=Math.min(minX,p.x); maxX=Math.max(maxX,p.x+CW);
    minY=Math.min(minY,p.y); maxY=Math.max(maxY,p.y+CH);
  }});
  const tw = maxX-minX+60, th = maxY-minY+60;
  scale = Math.min(cw/tw, ch/th, 1.5);
  tx = (cw - tw*scale)/2 - minX*scale + 30*scale + OFFSET_X*scale;
  ty = (ch - th*scale)/2 - minY*scale + 30*scale + OFFSET_Y*scale;
  applyTransform();
}}

function findRootAncestor() {{
  const p = pos[DATA.rootId];
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  scale = 1.2;
  tx = cw/2 - (wx(p.x) + CW/2)*scale;
  ty = ch*0.82 - (wy(p.y) + CH/2)*scale;
  applyTransform();
}}
function zoomIn()  {{ scale=Math.min(4,scale*1.2);   applyTransform(); }}
function zoomOut() {{ scale=Math.max(0.08,scale/1.2); applyTransform(); }}

// ── Tooltip ───────────────────────────────────────────────────────────────
const tip = document.getElementById('tooltip');
function showTip(e, p) {{
  const genLabel = p.gen===0?'<span style="color:#b07010">Root — Donna Brown</span>':
    p.gen===1?'Parent':p.gen===2?'Grandparent':p.gen===3?'Great-grandparent':
    `${{p.gen-2}}× great-grandparent`;
  let html = `<strong>${{p.name}}</strong><br>${{genLabel}}`;
  if (p.birth) html += `<br>🎂 Born: ${{p.birth}}`;
  if (p.birthPlace) html += `<br><span style="color:#a89060;font-size:10px">${{p.birthPlace}}</span>`;
  if (p.death) html += `<br>✝ Died: ${{p.death}}`;
  if (p.deathPlace) html += `<br><span style="color:#a89060;font-size:10px">${{p.deathPlace}}</span>`;
  tip.innerHTML = html; tip.style.display='block'; moveTip(e);
}}
function moveTip(e) {{ tip.style.left=(e.clientX+16)+'px'; tip.style.top=(e.clientY-8)+'px'; }}
function hideTip()  {{ tip.style.display='none'; }}

// Start with Root in view
window.addEventListener('load', () => setTimeout(findRootAncestor, 50));
</script>
</body>
</html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ Saved → {out_path}  ({len(html):,} bytes)")


# ── Main ──────────────────────────────────────────────────────────────────────
"""
    Main function to read GEDCOM, find ancestors, build data, and generate HTML.
    Usage: python main.py [file.ged]
    If no file is provided, it looks for the first .ged file in the current directory
    and uses that. The output HTML is saved in the same directory with _tree.html suffix.
"""


def main():
    ged_files = glob.glob("*.[Gg][Ee][Dd]")
    ged_file = (
        " ".join(sys.argv[1:])
        if len(sys.argv) > 1
        else (ged_files[0] if ged_files else None)
    )

    if not ged_file:
        print("No .ged file found. Please provide one as an argument.")
        sys.exit(1)
    base = os.path.splitext(os.path.basename(ged_file))[0]
    out_file = os.path.join(os.path.dirname(ged_file) or ".", base + "_tree.html")

    print(f"Reading {ged_file} …")
    indis, fams = parse_gedcom(ged_file)
    print(f"  {len(indis)} individuals, {len(fams)} families")

    # Build a set of all people who ARE someone's parent
    all_parents = set()
    for fam in fams.values():
        if fam["husb"]:
            all_parents.add(fam["husb"])
        if fam["wife"]:
            all_parents.add(fam["wife"])

    # Build set of all children
    all_children = set()
    for fam in fams.values():
        for child in fam["children"]:
            all_children.add(child)

    # Root = someone who is a child (has parents above them) but is NOT
    # listed as a parent in any family — i.e., the youngest generation leaf.
    # Among ties, pick the one whose subtree of ancestors is deepest.
    candidates = [iid for iid in indis if iid not in all_parents]

    if not candidates:
        # Fallback: pick whoever has the most ancestors
        candidates = list(indis.keys())

    def ancestor_depth(pid):
        visited, queue, depth = set(), [pid], 0
        child_to_fam = {}
        for fid, fam in fams.items():
            for child in fam["children"]:
                child_to_fam[child] = fid
        while queue:
            next_q = []
            for p in queue:
                fid = child_to_fam.get(p)
                if not fid:
                    continue
                for parent in [fams[fid]["husb"], fams[fid]["wife"]]:
                    if parent and parent not in visited:
                        visited.add(parent)
                        next_q.append(parent)
                        depth += 1
            queue = next_q
        return depth

    root_id = max(candidates, key=ancestor_depth)
    print(f"  Root: {indis[root_id]['name']} (auto-detected)")
    root_ancestor = indis[root_id]["name"]

    ancestors, gen, couples, child_to_fam = find_ancestors(indis, fams, root_id)
    print(f"  Direct ancestors: {len(ancestors)}  |  Couples: {len(couples)}")

    data = build_tree_data(indis, ancestors, gen, couples, root_id)
    generate_html(data, out_file, len(indis), root_ancestor)


if __name__ == "__main__":
    main()
