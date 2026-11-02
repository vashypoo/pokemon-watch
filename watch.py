#!/usr/bin/env python3
"""Toy Kingdom Pokémon watcher. Runs hourly on GitHub Actions.
 
Reads the store's Pokémon product feed, compares it with docs/data.json,
records new listings and restocks, pushes a phone alert via ntfy, and
rewrites the tracker page in docs/.
"""
import json, os, glob, time, datetime, urllib.request, urllib.parse
 
STORE = "https://www.toykingdom.com.ph"
FEED = STORE + "/collections/pokemon/products.json?limit=250&page={}"
DATA = "docs/data.json"
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "")
PAGE_URL = f"https://{REPO.split('/')[0].lower()}.github.io/{REPO.split('/')[-1]}/" if "/" in REPO else ""
UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
 
 
def get_json(url):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise last
 
 
def fetch_products():
    local = os.environ.get("TK_PAGES_DIR")  # for offline testing only
    if local:
        out = []
        for p in sorted(glob.glob(os.path.join(local, "*.json"))):
            out += json.load(open(p)).get("products", [])
        return out
    out = []
    for page in range(1, 11):
        batch = get_json(FEED.format(page)).get("products", [])
        out += batch
        if len(batch) < 250:
            break
    return out
 
 
def push(title, body, click):
    if not TOPIC:
        print("NTFY_TOPIC not set; skipping alert")
        return
    qs = urllib.parse.urlencode({"title": title, "click": click, "tags": "rotating_light", "priority": "high"})
    req = urllib.request.Request("https://ntfy.sh/" + urllib.parse.quote(TOPIC) + "?" + qs,
                                 data=body.encode("utf-8"), method="POST")
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print("alert failed:", e)
 
 
try:
    state = json.load(open(DATA))
except Exception:
    state = {}
old = state.get("products") or {}
events = state.get("events") or []
status = state.get("status") or {}
first_run = not state.get("baseline") and not old
 
try:
    raw = fetch_products()
    if not raw:
        raise RuntimeError("the store returned no Pokémon products")
except Exception as e:
    fails = int(status.get("fails", 0)) + 1
    status.update({"lastCheck": now, "ok": False, "fails": fails,
                   "message": f"Couldn't read the store ({type(e).__name__}). Retrying next hour."})
    state["status"] = status
    os.makedirs("docs", exist_ok=True)
    json.dump(state, open(DATA, "w"), ensure_ascii=False)
    if fails == 3:
        push("Pokémon Watch can't reach Toy Kingdom",
             "3 checks in a row failed. Alerts are paused until the store answers again.", PAGE_URL or STORE)
    print("check failed:", repr(e))
    raise SystemExit(0)
 
import re
# Keep only trading-card products. Matches the store's category/tags, or card words in the name.
TCG_WORDS = re.compile(
    r"\b(tcg|trading card|card game|cards?|booster|elite trainer|etb|blister|"
    r"tins?|deck|sleeves?|binder|portfolio|playmat|premium collection|"
    r"collection box|ex box|v box|build ?& ?battle|starter set|expansion pack)\b", re.I)
 
 
def is_tcg(prod):
    meta = " ".join([prod.get("product_type") or "", " ".join(prod.get("tags") or [])
                     if isinstance(prod.get("tags"), list) else str(prod.get("tags") or "")])
    if re.search(r"card|tcg", meta, re.I):
        return True
    return bool(TCG_WORDS.search(prod.get("title", "")))
 
 
raw = [p for p in raw if is_tcg(p)]
 
cur = {}
for prod in raw:
    vs = prod.get("variants") or []
    prices = [float(v["price"]) for v in vs if v.get("price")]
    cmps = [float(v["compare_at_price"]) for v in vs if v.get("compare_at_price")]
    imgs = prod.get("images") or []
    cur[str(prod["id"])] = {
        "title": prod["title"].strip(),
        "handle": prod["handle"],
        "price": min(prices) if prices else None,
        "compareAt": max(cmps) if cmps else None,
        "available": any(v.get("available") for v in vs),
        "image": (imgs[0].get("src") + "&width=200") if imgs and "?" in imgs[0].get("src", "") else
                 ((imgs[0].get("src") + "?width=200") if imgs else None),
    }
 
new, restock = [], []
for pid, p in cur.items():
    prev = old.get(pid)
    if prev is None:
        p["firstSeen"] = now
        if not first_run:
            new.append(pid)
    else:
        p["firstSeen"] = prev.get("firstSeen", now)
        if p["available"] and not prev.get("available"):
            restock.append(pid)
 
for kind, ids in (("new", new), ("restock", restock)):
    for pid in ids:
        p = cur[pid]
        events.insert(0, {"type": kind, "productId": pid, "title": p["title"], "handle": p["handle"],
                          "price": p["price"], "image": p["image"], "at": now})
events = events[:150]
 
status = {"lastCheck": now, "ok": True, "fails": 0, "count": len(cur), "message": "",
          "lastFound": now if (new or restock) else status.get("lastFound")}
os.makedirs("docs", exist_ok=True)
json.dump({"baseline": True, "products": cur, "events": events, "status": status}, open(DATA, "w"), ensure_ascii=False)
 
 
def peso(v):
    return f"₱{v:,.0f}" if v else ""
 
 
if new or restock:
    lines = [f"NEW: {cur[i]['title']} {peso(cur[i]['price'])}" for i in new]
    lines += [f"BACK: {cur[i]['title']} {peso(cur[i]['price'])}" for i in restock]
    one = (new + restock)[0]
    click = STORE + "/products/" + cur[one]["handle"] if len(new + restock) == 1 else (PAGE_URL or STORE + "/collections/pokemon")
    title = f"Toy Kingdom TCG: {len(new)} new, {len(restock)} restocked"
    push(title, "\n".join(lines[:8]) + (f"\n+{len(lines) - 8} more" if len(lines) > 8 else ""), click)
 
print(json.dumps({"firstRun": first_run, "count": len(cur), "new": len(new), "restock": len(restock)}))
 
# ---- tracker page -------------------------------------------------------
PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Pokémon Watch</title>
<meta name="theme-color" content="#eef2f7">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,800&family=Figtree:wght@400;500;600&display=swap">
<style>
:root{--bg:#eef2f7;--surface:#fff;--ink:#1b2433;--muted:#5d6b80;--line:#d7dfea;--new:#e2463a;--new-bg:#fde9e6;--restock:#0b8a7a;--restock-bg:#dff5f1;--sold:#8a94a5;--accent:#2a5bd7;
--fd:"Bricolage Grotesque","Avenir Next",system-ui,sans-serif;--fb:"Figtree",system-ui,-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#111722;--surface:#1a2231;--ink:#e8edf5;--muted:#9aa7bb;--line:#2b3546;--new:#ff6f61;--new-bg:#3a1f1e;--restock:#35c9b3;--restock-bg:#13332f;--sold:#6f7a8c;--accent:#7ea2ff;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--fb);font-size:15px;line-height:1.45}
.wrap{max-width:640px;margin:0 auto;padding:calc(20px + env(safe-area-inset-top)) max(16px,env(safe-area-inset-right)) 48px max(16px,env(safe-area-inset-left));display:grid;gap:28px}
header{display:grid;gap:10px}
.eyebrow{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}
h1{font-family:var(--fd);font-weight:800;font-size:clamp(28px,8vw,40px);line-height:1.02;margin:0}
.status{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:13px;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--sold);margin-right:6px}
.dot.ok{background:var(--restock)}.dot.err{background:var(--new)}
.tally{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.tally div{background:var(--surface);padding:10px 12px}
.tally b{display:block;font-family:var(--fd);font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.tally span{font-size:12px;color:var(--muted)}
h2{font-family:var(--fd);font-weight:600;font-size:19px;margin:0 0 10px}
ul{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.item{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:10px 12px;display:grid;grid-template-columns:56px 1fr auto;gap:4px 12px;align-items:center;text-decoration:none;color:var(--ink)}
.item img,.item .ph{width:56px;height:56px;border-radius:8px;object-fit:contain;background:#fff;grid-row:span 2}
.item .t{font-weight:600;min-width:0;overflow-wrap:anywhere}
.item:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.price{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
.meta{grid-column:2/-1;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:12.5px;color:var(--muted)}
.pill{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border-radius:999px}
.pill.new{color:var(--new);background:var(--new-bg)}.pill.restock{color:var(--restock);background:var(--restock-bg)}.pill.sold{color:var(--sold);border:1px solid var(--line)}
.is-sold .t{color:var(--muted)}.is-sold img{opacity:.55}
.empty{background:var(--surface);border:1px dashed var(--line);border-radius:12px;padding:18px;color:var(--muted);font-size:14px}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
.seg{display:flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--surface)}
.seg button{font:inherit;font-size:13px;border:0;background:none;color:var(--muted);padding:8px 12px}
.seg button[aria-pressed=true]{background:var(--ink);color:var(--bg)}
#q{flex:1 1 160px;min-width:0;font:inherit;font-size:14px;padding:8px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink)}
footer{font-size:12.5px;color:var(--muted)}
</style></head><body><div class="wrap">
<header><div class="eyebrow">toykingdom.com.ph · Pokémon TCG</div><h1>Pokémon Watch</h1>
<div class="status" id="status">Loading…</div>
<div class="tally"><div><b id="tT">–</b><span>Listed</span></div><div><b id="tI">–</b><span>In stock</span></div><div><b id="tO">–</b><span>Sold out</span></div></div></header>
<section><h2>Recent finds</h2><ul id="feed"><li class="empty">New listings and restocks show up here, newest first. The first check records what's already in the store, so finds start from the second check.</li></ul></section>
<section><h2>Everything being watched</h2>
<div class="controls"><div class="seg"><button data-f="all" aria-pressed="true">All</button><button data-f="in" aria-pressed="false">In stock</button><button data-f="out" aria-pressed="false">Sold out</button></div>
<input id="q" type="search" placeholder="Search, e.g. booster" aria-label="Search products"></div>
<ul id="list"><li class="empty">The product list appears after the first check.</li></ul></section>
<footer>Checks run every hour on GitHub. Tap a product to open it on Toy Kingdom. Branch stock may differ from the website.</footer></div>
<script>
const $=id=>document.getElementById(id),S="https://www.toykingdom.com.ph/products/";
let D=null,f="all",q="";try{f=localStorage.getItem("f")||"all"}catch(e){}
const peso=v=>v==null?"":"₱"+Number(v).toLocaleString("en-PH",{minimumFractionDigits:2,maximumFractionDigits:2});
function ago(i){if(!i)return"";const s=(Date.now()-new Date(i))/1e3;if(s<90)return"just now";if(s<3600)return Math.round(s/60)+" min ago";if(s<86400)return Math.round(s/3600)+" hr ago";const d=Math.round(s/86400);return d==1?"yesterday":d+" days ago"}
const stamp=i=>new Date(i).toLocaleString("en-PH",{month:"short",day:"numeric",hour:"numeric",minute:"2-digit"});
function E(t,c,x){const n=document.createElement(t);if(c)n.className=c;if(x!=null)n.textContent=x;return n}
function card(p,kind,when){const a=E("a","item"+(p.available?"":" is-sold"));a.href=S+encodeURIComponent(p.handle);a.target="_blank";a.rel="noopener";
const im=p.image?Object.assign(E("img"),{src:p.image,alt:"",loading:"lazy"}):E("div","ph");
const m=E("div","meta");if(kind=="new")m.append(E("span","pill new","New"));if(kind=="restock")m.append(E("span","pill restock","Back in stock"));if(!p.available)m.append(E("span","pill sold","Sold out"));if(when)m.append(E("span",null,when));
a.append(im,E("span","t",p.title),E("span","price",peso(p.price)),m);return a}
function render(){if(!D)return;const st=D.status||{},P=D.products||{},items=Object.entries(P).map(([id,p])=>({id,...p}));
const s=$("status");s.textContent="";const a=E("span");a.append(E("span","dot "+(st.ok?"ok":"err")),(st.ok?"Checked ":"Last check failed ")+ago(st.lastCheck));s.append(a);
if(st.ok&&st.lastFound)s.append(E("span",null,"Last find "+ago(st.lastFound)));if(!st.ok&&st.message)s.append(E("span",null,st.message));
const n=items.filter(p=>p.available).length;$("tT").textContent=items.length;$("tI").textContent=n;$("tO").textContent=items.length-n;
const ev=D.events||[];if(ev.length){const fe=$("feed");fe.textContent="";ev.slice(0,40).forEach(e=>{const l=P[e.productId]||{};const li=E("li");li.append(card({...e,available:l.available??true,price:l.price??e.price,image:l.image||e.image},e.type,stamp(e.at)+" · "+ago(e.at)));fe.append(li)})}
const qq=q.trim().toLowerCase();let sh=items.filter(p=>f=="all"||(f=="in"?p.available:!p.available)).filter(p=>!qq||p.title.toLowerCase().includes(qq));
sh.sort((x,y)=>(y.available-x.available)||String(y.firstSeen).localeCompare(String(x.firstSeen))||x.title.localeCompare(y.title));
const L=$("list");L.textContent="";if(!sh.length)L.append(E("li","empty","Nothing matches that filter."));sh.forEach(p=>{const li=E("li");li.append(card(p,null,"Tracked since "+stamp(p.firstSeen)));L.append(li)});
document.querySelectorAll(".seg button").forEach(b=>b.setAttribute("aria-pressed",b.dataset.f==f))}
async function load(){try{const r=await fetch("data.json?t="+Date.now(),{cache:"no-store"});if(!r.ok)throw 0;D=await r.json();render()}catch(e){if(!D)$("status").textContent="No data yet. The first check runs within the hour, or start it from the Actions tab."}}
document.querySelectorAll(".seg button").forEach(b=>b.onclick=()=>{f=b.dataset.f;try{localStorage.setItem("f",f)}catch(e){}render()});
$("q").oninput=e=>{q=e.target.value;render()};
document.addEventListener("visibilitychange",()=>{if(!document.hidden)load()});
load();setInterval(load,300000);
</script></body></html>
'''
open("docs/index.html", "w").write(PAGE)
open("docs/.nojekyll", "w").write("")
