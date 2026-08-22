# Generates OMEN-LIMIT-TEST-CHARTS-2026-08.html — the campaign chart gallery.
# Every chart: validated palette (blue #3987e5 / orange #d95926 / aqua #199e70 on
# #1a1f27), native <title> hover on every mark, data table beside/below each chart.
import math, io

INK = "#d8dee8"; DIM = "#8b96a5"; GRID = "#2a3240"; PANEL = "#1a1f27"
BLUE = "#3987e5"; ORANGE = "#d95926"; AQUA = "#199e70"; BAD = "#e5675c"; WARN = "#e5b567"

def svg_open(w, h, label):
    return ('<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" role="img" '
            'aria-label="%s" style="max-width:100%%;height:auto">' % (w, h, label))

def grid_y(out, L, Rr, ymap, vals, fmt="%d"):
    for v in vals:
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1"/>' % (L, ymap(v), Rr, ymap(v), GRID))
        out.append(('<text x="%d" y="%.1f" fill="%s" font-size="11" text-anchor="end">' + fmt + '</text>') % (L - 8, ymap(v) + 4, DIM, v))

# ============================================================ Chart 1: speedup ratios
# Old box vs new box, same cards: ratio bars around x1.0
ratios = [
    ("70B dual pp1024",        384.5/151.4, "151.4 -> 384.5 tok/s"),
    ("Q2 replica aggregate",   157.0/95.7,  "95.7 -> 157 tok/s"),
    ("30B-A3B single-stream",  100.0/81.7,  "81.7 -> ~95-105 tok/s (midpoint)"),
    ("14B single pp512",       1462.0/1298.0, "1261/1336 -> 1451/1474 (means)"),
    ("70B dual tg256",         11.7/11.28,  "11.28 -> 11.7 tok/s"),
    ("14B single tg128",       47.0/50.1,   "50.1 -> 46.7/47.3 tok/s"),
]
W,H = 740, 40+len(ratios)*44+40; L,Rr = 190, W-18
xmax = 2.8
def XR(v): return L + (Rr-L)*(v/xmax)
c1 = [svg_open(W,H,"Old box versus new box speedup ratios, same two cards")]
for gv in (0.5,1.0,1.5,2.0,2.5):
    c1.append('<line x1="%.1f" y1="30" x2="%.1f" y2="%d" stroke="%s" stroke-width="%s"/>' % (XR(gv), XR(gv), H-30, GRID if gv!=1.0 else DIM, 1 if gv!=1.0 else 1.5))
    c1.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">x%.1f</text>' % (XR(gv), H-14, DIM, gv))
for i,(name, r, note) in enumerate(ratios):
    y = 40 + i*44
    color = AQUA if r >= 1.02 else (BAD if r < 0.98 else DIM)
    x0, x1 = (XR(1.0), XR(r)) if r >= 1.0 else (XR(r), XR(1.0))
    c1.append('<text x="%d" y="%d" fill="%s" font-size="12" text-anchor="end">%s</text>' % (L-10, y+14, INK, name))
    c1.append('<rect x="%.1f" y="%d" width="%.1f" height="20" rx="4" fill="%s"><title>%s: x%.2f (%s)</title></rect>' % (x0, y, max(2.5,x1-x0), color, name, r, note))
    c1.append('<text x="%.1f" y="%d" fill="%s" font-size="12" font-weight="600" text-anchor="%s">x%.2f</text>' % (x1+ (6 if r>=1 else -6), y+14, INK, "start" if r>=1 else "end", r))
c1.append('<text x="%.1f" y="22" fill="%s" font-size="11" text-anchor="middle">old box = x1.0 (Win10 - 8801 - 32GB DDR4 - COOPMAT off)</text>' % (XR(1.0), DIM))
c1.append('</svg>')
chart1 = "".join(c1)

# ============================================================ Chart 2: size vs decode speed
# x = weights on disk (GB, log), y = single-stream decode tok/s (log), dense vs MoE
pts = [
    ("Qwen2.5-14B (dense)",   8.4,  47.0, "dense", "bench tg128"),
    ("Qwen2.5-32B (dense)",  18.5,  23.7, "dense", "old-box bench (not re-run)"),
    ("Llama-3.3-70B (dense)",39.6,  11.7, "dense", "bench tg256, dual-split"),
    ("Qwen3-30B-A3B (MoE)",  17.3,  95.0, "moe",   "server single-stream ~95-105"),
    ("gpt-oss-120b (MoE)",   59.0,  14.7, "moe",   "server single-stream e2e"),
]
W2,H2 = 740, 360; L2,T2,B2,R2 = 64, 30, 50, 20
pw,ph = W2-L2-R2, H2-T2-B2
def XS(g): return L2 + pw*(math.log10(g)-math.log10(7))/(math.log10(70)-math.log10(7))
def YS(t): return T2 + ph*(1 - (math.log10(t)-1)/(math.log10(120)-1))
c2 = [svg_open(W2,H2,"Model size on disk versus single-stream decode speed")]
for t in (10,20,50,100):
    c2.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1"/>' % (L2, YS(t), W2-R2, YS(t), GRID))
    c2.append('<text x="%d" y="%.1f" fill="%s" font-size="11" text-anchor="end">%d</text>' % (L2-8, YS(t)+4, DIM, t))
for g in (8,16,32,64):
    c2.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">%d GB</text>' % (XS(g), H2-B2+18, DIM, g))
c2.append('<text x="%d" y="%d" fill="%s" font-size="11">decode tok/s (log)</text>' % (L2-44, T2-12, DIM))
c2.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">weights on disk, Q4-class (log)</text>' % ((L2+W2-R2)/2, H2-8, DIM))
dense = [(g,t) for n,g,t,k,_ in pts if k=="dense"]
c2.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.5" stroke-dasharray="4 4" opacity="0.7"/>' % (" ".join("%.1f,%.1f"%(XS(g),YS(t)) for g,t in dense), BLUE))
for name,g,t,kind,note in pts:
    color = BLUE if kind=="dense" else ORANGE
    if kind=="dense":
        c2.append('<circle cx="%.1f" cy="%.1f" r="6" fill="%s" stroke="%s" stroke-width="2"><title>%s: %.1f GB, %.1f tok/s (%s)</title></circle>' % (XS(g), YS(t), color, PANEL, name, g, t, note))
    else:
        x,y = XS(g), YS(t)
        c2.append('<path d="M %.1f %.1f L %.1f %.1f L %.1f %.1f L %.1f %.1f Z" fill="%s" stroke="%s" stroke-width="2"><title>%s: %.1f GB, %.1f tok/s (%s)</title></path>' % (x, y-7, x+7, y, x, y+7, x-7, y, color, PANEL, name, g, t, note))
lbl = {"Qwen2.5-14B (dense)":(10,-12),"Qwen2.5-32B (dense)":(10,-10),"Llama-3.3-70B (dense)":(10,-12),
       "Qwen3-30B-A3B (MoE)":(10,-12),"gpt-oss-120b (MoE)":(10,16)}
for name,g,t,kind,note in pts:
    dx,dy = lbl[name]
    anchor = "end" if "120b" in name else "start"
    if anchor == "end": dx = -12
    c2.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11" text-anchor="%s">%s</text>' % (XS(g)+dx, YS(t)+dy, INK, anchor, name.split(" (")[0]))
c2.append('<rect x="%d" y="%d" width="10" height="10" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">dense (bandwidth-bound curve)</text>' % (W2-R2-256, T2-2, BLUE, W2-R2-242, T2+7, DIM))
c2.append('<path d="M %d %d l 6 6 l -6 6 l -6 -6 Z" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">MoE (breaks the curve)</text>' % (W2-R2-66-6, T2-2, ORANGE, W2-R2-56, T2+7, DIM))
c2.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11" transform="rotate(-33 %.1f %.1f)" opacity="0.85">dense: speed falls with size</text>' % (XS(11), YS(31), DIM, XS(11), YS(31)))
c2.append('</svg>')
chart2 = "".join(c2)

# ============================================================ Chart 3: thermal story
phases = [
    ("idle\n(night 0)",        58, 53, "10-min baseline, both cards"),
    ("single-card\nbench",     84, 74, "Stage 1, one card loaded"),
    ("dual soak\n(pre-sail)",  94, 81, "overnight deep-context; card 04:00.0"),
    ("co-load\n(sail up)",     70, 70, "7b triple load AFTER the duct went up"),
    ("FINALE\n(everything)",   70, 70, "serve+xpu+CPU+disk, 60 min"),
]
W3,H3 = 740, 330; L3,T3,B3,R3 = 56, 30, 64, 18
pw3,ph3 = W3-L3-R3, H3-T3-B3
def X3(i): return L3 + pw3*(i+0.5)/len(phases)
def Y3(t): return T3 + ph3*(1-(t-40)/60.0)
c3 = [svg_open(W3,H3,"Peak temperatures by campaign phase, with the sail moment")]
grid_y(c3, L3, W3-R3, Y3, [40,50,60,70,80,90,100])
c3.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1.5" stroke-dasharray="6 4"/>' % (L3, Y3(95), W3-R3, Y3(95), BAD))
c3.append('<text x="%d" y="%.1f" fill="%s" font-size="11" text-anchor="end">95 C abort line</text>' % (W3-R3, Y3(95)-6, BAD))
sx = (X3(2)+X3(3))/2
c3.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="1.5" stroke-dasharray="5 4"/>' % (sx, T3, sx, H3-B3, AQUA))
c3.append('<text x="%.1f" y="%d" fill="%s" font-size="12" font-weight="600" text-anchor="middle">THE SAIL GOES UP</text>' % (sx, T3-8, AQUA))
for series, color, key in ((0, BAD, "VRAM"), (1, WARN, "GPU core")):
    vals = [(p[1] if series==0 else p[2]) for p in phases]
    c3.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>' % (" ".join("%.1f,%.1f"%(X3(i),Y3(v)) for i,v in enumerate(vals)), color))
    for i,v in enumerate(vals):
        c3.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s" stroke="%s" stroke-width="2"><title>%s peak %d C - %s</title></circle>' % (X3(i), Y3(v), color, PANEL, key, v, phases[i][3]))
        if series==0:
            c3.append('<text x="%.1f" y="%.1f" fill="%s" font-size="12" font-weight="600" text-anchor="middle">%d</text>' % (X3(i), Y3(v)-10, INK, v))
for i,p in enumerate(phases):
    for j,linetxt in enumerate(p[0].split("\n")):
        c3.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">%s</text>' % (X3(i), H3-B3+16+j*13, DIM, linetxt))
c3.append('<text x="%d" y="%d" fill="%s" font-size="11">peak C</text>' % (L3-40, T3-12, DIM))
c3.append('<rect x="%d" y="%d" width="10" height="10" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">VRAM</text>' % (L3+8, T3+2, BAD, L3+22, T3+11, DIM))
c3.append('<rect x="%d" y="%d" width="10" height="10" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">GPU core</text>' % (L3+78, T3+2, WARN, L3+92, T3+11, DIM))
c3.append('</svg>')
chart3 = "".join(c3)

# ============================================================ Chart 4: load times
loads = [
    ("14B (8.4 GB)",   9,  3),
    ("30B-A3B (17.3)", 60, 9),
    ("32B (18.5)",     75, 6),
    ("70B (39.6)",    126, 21),
    ("120B (59)",     159, None),
]
W4,H4 = 740, 320; L4,T4,B4,R4 = 56, 28, 58, 18
pw4,ph4 = W4-L4-R4, H4-T4-B4
def X4(i): return L4 + pw4*(i+0.5)/len(loads)
def Y4(s): return T4 + ph4*(1-s/170.0)
c4 = [svg_open(W4,H4,"Model load to first healthy, cold versus warm page cache")]
grid_y(c4, L4, W4-R4, Y4, [0,30,60,90,120,150])
bw = 26
for i,(name, cold, warm) in enumerate(loads):
    x = X4(i)
    c4.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" rx="4" fill="%s"><title>%s cold load: ~%ds to healthy</title></rect>' % (x-bw-1, Y4(cold), bw, Y4(0)-Y4(cold), BLUE, name, cold))
    c4.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11" text-anchor="middle">%d</text>' % (x-bw/2-1, Y4(cold)-6, INK, cold))
    if warm is not None:
        c4.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" rx="4" fill="%s"><title>%s warm load: ~%ds to healthy</title></rect>' % (x+1, Y4(warm), bw, Y4(0)-Y4(warm), AQUA, name, warm))
        c4.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11" text-anchor="middle">%d</text>' % (x+bw/2+1, Y4(warm)-6, INK, warm))
    else:
        c4.append('<text x="%.1f" y="%.1f" fill="%s" font-size="10" text-anchor="middle">no warm</text>' % (x+bw/2+3, Y4(4), DIM))
        c4.append('<text x="%.1f" y="%.1f" fill="%s" font-size="10" text-anchor="middle">run</text>' % (x+bw/2+3, Y4(4)+11, DIM))
    c4.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">%s</text>' % (x, H4-B4+18, DIM, name))
c4.append('<text x="%d" y="%d" fill="%s" font-size="11">seconds to /health 200 (3 s poll granularity)</text>' % (L4-40, T4-10, DIM))
c4.append('<rect x="%d" y="%d" width="10" height="10" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">cold (first read off E:)</text>' % (L4+14, T4+16, BLUE, L4+28, T4+25, DIM))
c4.append('<rect x="%d" y="%d" width="10" height="10" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">warm (page cache)</text>' % (L4+14, T4+34, AQUA, L4+28, T4+43, DIM))
c4.append('</svg>')
chart4 = "".join(c4)

# ============================================================ Chart 5: soak stability
s7b = [102.7, 102.7, 105.0, 102.0, 103.8, 103.4, 103.2, 101.9, 103.9, 105.2, 106.2, 102.7, 108.0, 104.8, 104.3, 100.3, 104.1, 104.2, 105.6, 101.9, 103.3, 105.6, 102.8, 103.9, 101.5, 104.7, 104.3, 104.9, 103.2, 103.1, 102.2, 104.5, 102.2, 103.7, 102.9, 102.3, 104.7, 103.3, 103.3, 102.6, 103.9, 101.7, 103.2, 102.8, 102.7, 102.0, 104.4, 103.9, 100.9, 105.0, 104.8, 104.4, 101.2, 102.7, 104.0, 104.9, 104.6, 101.7, 105.3, 105.4]
s8  = [104.6, 100.6, 105.9, 100.9, 100.4, 105.7, 100.9, 103.5, 103.2, 100.7, 106.7, 102.5, 100.3, 106.5, 99.9, 106.1, 102.6, 100.5, 104.7, 98.4, 99.6, 106.3, 101.9, 103.6, 102.2, 99.4, 103.9, 101.1, 98.7, 105.4, 100.8, 101.8, 98.2, 103.3, 97.9, 102.1, 104.4, 99.6, 103.6, 99.8, 100.6, 105.6, 101.3, 104.5, 102.4, 99.2, 102.7, 95.4, 104.8, 102.7, 98.9, 105.3, 96.5, 102.4, 96.6, 94.5, 103.6, 96.2, 105.9, 96.5]
W5,H5 = 740, 300; L5,T5,B5,R5 = 56, 30, 46, 18
pw5,ph5 = W5-L5-R5, H5-T5-B5
def X5(i,n): return L5 + pw5*i/(n-1)
def Y5(v): return T5 + ph5*(1-(v-80)/40.0)
c5 = [svg_open(W5,H5,"Serve throughput stability across the soak and the finale")]
grid_y(c5, L5, W5-R5, Y5, [80,90,100,110,120])
c5.append('<rect x="%d" y="%.1f" width="%d" height="%.1f" fill="%s" opacity="0.06"/>' % (L5, Y5(120), pw5, Y5(93)-Y5(120), AQUA))
c5.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1.5" stroke-dasharray="6 4"/>' % (L5, Y5(93), W5-R5, Y5(93), BAD))
c5.append('<text x="%d" y="%.1f" fill="%s" font-size="11" text-anchor="end">finale floor 93</text>' % (W5-R5, Y5(93)+14, BAD))
for series, color, name in ((s7b, BLUE, "7b co-load soak (847 waves, CV 5.1%)"), (s8, ORANGE, "FINALE all-out (425 waves, CV 5.2%)")):
    n = len(series)
    c5.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2"/>' % (" ".join("%.1f,%.1f"%(X5(i,n),Y5(v)) for i,v in enumerate(series)), color))
c5.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s" stroke="%s" stroke-width="2"><title>7b co-load soak: 847 waves, mean 103.5, CV 5.1%%</title></circle>' % (X5(12,60), Y5(108.0), BLUE, PANEL))
c5.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s" stroke="%s" stroke-width="2"><title>finale: 425 waves, mean 101.7, CV 5.2%%</title></circle>' % (X5(55,60), Y5(94.5), ORANGE, PANEL))
c5.append('<rect x="%d" y="%d" width="10" height="10" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">7b co-load (2 h, mean 103.5)</text>' % (L5+8, T5, BLUE, L5+22, T5+9, DIM))
c5.append('<rect x="%d" y="%d" width="10" height="10" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">finale all-out (1 h, mean 101.7)</text>' % (L5+218, T5, ORANGE, L5+232, T5+9, DIM))
c5.append('<text x="%d" y="%d" fill="%s" font-size="11">serve agg tok/s (bucket means)</text>' % (L5-40, T5-12, DIM))
c5.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">normalized run time -&gt;</text>' % ((L5+W5-R5)/2, H5-8, DIM))
c5.append('</svg>')
chart5 = "".join(c5)

# ============================================================ Chart 6: replica scaling
bars6 = [
    ("single server\n(1 card)",      46.7, BLUE,  "stage 5 baseline, 3 rounds"),
    ("dual replicas\n(1 per card)",  86.5, AQUA,  "stage 5, 1.85x - zero TDR"),
    ("2026-05 result\n(RETRACTED)",  None, DIM,   "1.96x on paper; display-adapter TDRs forced retraction"),
]
W6,H6 = 740, 300; L6,T6,B6,R6 = 56, 30, 62, 18
pw6,ph6 = W6-L6-R6, H6-T6-B6
def X6(i): return L6 + pw6*(i+0.5)/3
def Y6(v): return T6 + ph6*(1-v/100.0)
c6 = [svg_open(W6,H6,"Symmetric replica scaling: the un-retraction")]
grid_y(c6, L6, W6-R6, Y6, [0,25,50,75,100])
bw6 = 74
for i,(name, v, color, note) in enumerate(bars6):
    x = X6(i)
    if v is not None:
        c6.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" rx="4" fill="%s"><title>%s tok/s - %s</title></rect>' % (x-bw6/2, Y6(v), bw6, Y6(0)-Y6(v), color, v, note))
        c6.append('<text x="%.1f" y="%.1f" fill="%s" font-size="13" font-weight="600" text-anchor="middle">%.1f</text>' % (x, Y6(v)-8, INK, v))
    else:
        gy = Y6(46.7*1.96)
        c6.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" rx="4" fill="none" stroke="%s" stroke-width="1.5" stroke-dasharray="5 4"><title>%s</title></rect>' % (x-bw6/2, gy, bw6, Y6(0)-gy, DIM, note))
        c6.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11" text-anchor="middle">1.96x claim</text>' % (x, gy-8, DIM))
    for j,t in enumerate(name.split("\n")):
        c6.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">%s</text>' % (x, H6-B6+16+j*13, DIM, t))
c6.append('<text x="%.1f" y="%.1f" fill="%s" font-size="13" font-weight="600" text-anchor="middle">1.85x, zero TDR</text>' % (X6(1), Y6(96), AQUA))
c6.append('<text x="%d" y="%d" fill="%s" font-size="11">qwen2.5-32B agg tok/s (4-way waves)</text>' % (L6-40, T6-12, DIM))
c6.append('</svg>')
chart6 = "".join(c6)

# ============================================================ CAMPAIGN 2 (2026-08-22)
# Charts 9-12 are data-driven: they parse the raw result files on E: at
# generation time, so re-running this script after new legs land refreshes them.
import csv as _csv, json as _json, os as _os, statistics as _st

_RESDIR = r"E:\work\battlemage\burnin-2026-08\results"

def _bench_rows(path):
    out = []
    for line in io.open(path, encoding="utf-8", errors="replace"):
        c = [x.strip() for x in line.split("|")]
        if len(c) > 9 and c[1] == "512" and c[3].isdigit():
            out.append((int(c[3]), float(c[8])))
    return out

# ---- Chart 9: dose-response walk (expY, Mistral-24B, warm rep2)
expY = {v: dict(_bench_rows(_os.path.join(_RESDIR, "expY-mistral24b-mmv%d-rep2.txt" % v))) for v in (4, 8, 16)}
BS = [1, 2, 4, 6, 8, 9, 10, 12, 16, 24, 32]
W9, H9 = 760, 400; L9, R9, T9, B9 = 60, 24, 46, 66
pw9, ph9 = W9 - L9 - R9, H9 - T9 - B9
def X9(b): return L9 + pw9 * BS.index(b) / (len(BS) - 1)
def Y9(v): return T9 + ph9 * (1 - v / 170.0)
c9 = [svg_open(W9, H9, "Dose-response: the throughput cliff lands wherever the limit is set")]
grid_y(c9, L9, W9 - R9, Y9, (0, 50, 100, 150))
for b in BS:
    c9.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">%d</text>' % (X9(b), H9 - B9 + 18, DIM, b))
c9.append('<text x="%.1f" y="%d" fill="%s" font-size="12" font-weight="600" text-anchor="middle">parallel sequences (threads in flight)</text>' % ((L9 + W9 - R9) / 2, H9 - B9 + 40, INK))
c9.append('<text x="%d" y="%d" fill="%s" font-size="11">aggregate decode tok/s</text>' % (L9 - 40, T9 - 14, DIM))
_curves9 = ((4, WARN, "limit 4"), (8, ORANGE, "limit 8 (stock)"), (16, BLUE, "limit 16"))
for lim, color, lbl in _curves9:
    vals = expY[lim]
    pts = " ".join("%.1f,%.1f" % (X9(b), Y9(vals[b])) for b in BS if b in vals)
    c9.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5" stroke-linejoin="round"/>' % (pts, color))
    for b in BS:
        if b in vals:
            c9.append('<circle cx="%.1f" cy="%.1f" r="3.5" fill="%s" stroke="%s" stroke-width="1.5"><title>%s, B=%d: %.1f tok/s</title></circle>' % (X9(b), Y9(vals[b]), color, PANEL, lbl, b, vals[b]))
for lim, color, lbl in _curves9:
    x = X9(lim) + (X9(BS[BS.index(lim) + 1]) - X9(lim)) / 2
    c9.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="1.5" stroke-dasharray="5 4"/>' % (x, T9, x, H9 - B9, color))
    c9.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">cliff &gt;%d</text>' % (x, T9 - 4, color, lim))
c9.append('<text x="%.1f" y="%.1f" fill="%s" font-size="13" font-weight="700">move the number, the cliff moves</text>' % (X9(9) + 8, Y9(160), INK))
for i, (lim, color, lbl) in enumerate(_curves9):
    c9.append('<rect x="%d" y="%d" width="12" height="4" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">%s</text>' % (L9 + 8 + i * 130, H9 - 10, color, L9 + 24 + i * 130, H9 - 5, DIM, lbl))
c9.append('</svg>')
chart9 = "".join(c9)
t9 = "<tr><th>B</th>" + "".join("<th>%d</th>" % b for b in BS) + "</tr>"
for lim, _c, lbl in _curves9:
    t9 += "<tr><td>%s</td>" % lbl + "".join("<td>%.1f</td>" % expY[lim][b] if b in expY[lim] else "<td>-</td>" for b in BS) + "</tr>"

# ---- Chart 10: the name-brand roster (expZ, stock vs patched at B=16)
_z = {}
for r in _csv.DictReader(io.open(_os.path.join(_RESDIR, "expZ-summary.csv"), encoding="utf-8")):
    _z[(r["tag"], int(r["mmv"]), int(r["B"]))] = (float(r["S_TG"]), r["model"], int(r["ctx"]))
_roster10 = [("phi4", "dense"), ("gemma27", "dense"), ("qwen32", "dense"), ("mistral24", "dense"),
             ("llama70", "dense"), ("gptoss20", "MoE"), ("moe30", "MoE")]
W10 = 760; rowh = 58; H10 = 46 + len(_roster10) * rowh + 26
L10, R10 = 218, 60
xmax10 = 260.0
def X10(v): return L10 + (W10 - L10 - R10) * v / xmax10
c10 = [svg_open(W10, H10, "Seven name-brand models at 16 threads: stock versus patched")]
for gv in (0, 50, 100, 150, 200, 250):
    c10.append('<line x1="%.1f" y1="36" x2="%.1f" y2="%d" stroke="%s"/>' % (X10(gv), X10(gv), H10 - 24, GRID))
    c10.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">%d</text>' % (X10(gv), H10 - 8, DIM, gv))
c10.append('<text x="%d" y="24" fill="%s" font-size="11">aggregate decode tok/s at B=16</text>' % (L10, DIM))
for i, (tag, klass) in enumerate(_roster10):
    y = 46 + i * rowh
    s, label, ctx = _z[(tag, 8, 16)]
    p = _z[(tag, 16, 16)][0]
    ctxs = "" if ctx == 65536 else " - %dk ctx" % (ctx // 1024)
    c10.append('<text x="%d" y="%d" fill="%s" font-size="12" text-anchor="end">%s</text>' % (L10 - 10, y + 14, INK, label))
    c10.append('<text x="%d" y="%d" fill="%s" font-size="10" text-anchor="end">%s%s</text>' % (L10 - 10, y + 28, DIM, klass, ctxs))
    c10.append('<rect x="%.1f" y="%d" width="%.1f" height="14" rx="3" fill="%s"><title>%s stock (limit 8), B=16: %.1f tok/s</title></rect>' % (X10(0), y, max(2, X10(s) - X10(0)), ORANGE, label, s))
    c10.append('<rect x="%.1f" y="%d" width="%.1f" height="14" rx="3" fill="%s"><title>%s patched (limit 16), B=16: %.1f tok/s</title></rect>' % (X10(0), y + 17, max(2, X10(p) - X10(0)), BLUE, label, p))
    c10.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="start">%.0f</text>' % (X10(s) + 5, y + 11, DIM, s))
    c10.append('<text x="%.1f" y="%d" fill="%s" font-size="12" font-weight="700" text-anchor="start">%.0f&#8201;(x%.1f)</text>' % (X10(p) + 5, y + 29, INK, p, p / s))
c10.append('<rect x="%d" y="30" width="12" height="10" rx="2" fill="%s"/><text x="%d" y="39" fill="%s" font-size="11">stock (limit 8)</text>' % (W10 - R10 - 300, ORANGE, W10 - R10 - 282, DIM))
c10.append('<rect x="%d" y="30" width="12" height="10" rx="2" fill="%s"/><text x="%d" y="39" fill="%s" font-size="11">patched (16)</text>' % (W10 - R10 - 160, BLUE, W10 - R10 - 142, DIM))
c10.append('</svg>')
chart10 = "".join(c10)
t10 = "<tr><th>model</th><th>class</th><th>ctx</th><th>B16 stock</th><th>B16 patched</th><th>gain</th><th>B9 stock</th><th>B9 patched</th><th>gain</th></tr>"
for tag, klass in _roster10:
    s16, label, ctx = _z[(tag, 8, 16)]; p16 = _z[(tag, 16, 16)][0]
    s9 = _z[(tag, 8, 9)][0]; p9 = _z[(tag, 16, 9)][0]
    t10 += ("<tr><td>%s</td><td>%s</td><td>%d</td><td>%.1f</td><td>%.1f</td><td class=\"good\">x%.1f</td>"
            "<td>%.1f</td><td>%.1f</td><td class=\"good\">x%.1f</td></tr>" % (label, klass, ctx, s16, p16, p16 / s16, s9, p9, p9 / s9))

# ---- Chart 11: the dyno sheet (expY exact values, power + torque panels)
_dB = [1, 2, 4, 6, 8, 9, 10, 12, 16]
_agg8 = [expY[8][b] for b in _dB]
_agg16 = [expY[16][b] for b in _dB]
W11, H11 = 760, 448
L11, R11 = 60, 60
pw11 = W11 - L11 - R11
p1T, p1H = 40, 200
p2T, p2H = p1T + p1H + 44, 110
def X11(b): return L11 + pw11 * _dB.index(b) / (len(_dB) - 1)
def Y11a(v): return p1T + p1H * (1 - v / 170.0)
def Y11b(v): return p2T + p2H * (1 - v / 34.0)
c11 = [svg_open(W11, H11, "Dyno sheet: threads in flight versus total and per-thread throughput")]
c11.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s" opacity="0.10"/>' % (X11(1), p1T, X11(16) - X11(1), p1H, BLUE))
c11.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s" opacity="0.14"/>' % (X11(1), p1T, X11(8) - X11(1), p1H, ORANGE))
_rl = X11(8) + (X11(9) - X11(8)) / 2
c11.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="2" stroke-dasharray="6 4"/>' % (_rl, p1T, _rl, p2T + p2H, BAD))
c11.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">stock redline</text>' % (_rl, p1T - 6, BAD))
c11.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">patched redline: 16</text>' % (X11(16) - 30, p1T - 6, AQUA))
for v in (0, 50, 100, 150):
    c11.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>' % (L11, Y11a(v), W11 - R11, Y11a(v), GRID))
    c11.append('<text x="%d" y="%.1f" fill="%s" font-size="11" text-anchor="end">%d</text>' % (L11 - 8, Y11a(v) + 4, DIM, v))
for v in (0, 10, 20, 30):
    c11.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>' % (L11, Y11b(v), W11 - R11, Y11b(v), GRID))
    c11.append('<text x="%d" y="%.1f" fill="%s" font-size="11" text-anchor="end">%d</text>' % (L11 - 8, Y11b(v) + 4, DIM, v))
c11.append('<text x="%d" y="%d" fill="%s" font-size="12" font-weight="600">TOTAL POWER  <tspan font-weight="400" fill="%s">aggregate tok/s (all threads)</tspan></text>' % (L11, p1T - 22, INK, DIM))
c11.append('<text x="%d" y="%d" fill="%s" font-size="12" font-weight="600">TORQUE  <tspan font-weight="400" fill="%s">tok/s each thread still feels</tspan></text>' % (L11, p2T - 8, INK, DIM))
for vals, color, wdt, ymap in ((_agg8, ORANGE, 3, Y11a), (_agg16, BLUE, 3, Y11a),
                               ([v / b for v, b in zip(_agg8, _dB)], ORANGE, 2.5, Y11b),
                               ([v / b for v, b in zip(_agg16, _dB)], BLUE, 2.5, Y11b)):
    pts = " ".join("%.1f,%.1f" % (X11(b), ymap(v)) for b, v in zip(_dB, vals))
    c11.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%s" stroke-linejoin="round"/>' % (pts, color, wdt))
for vals, color, lbl in ((_agg8, ORANGE, "stock (8)"), (_agg16, BLUE, "patched (16)")):
    for b, v in zip(_dB, vals):
        c11.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s" stroke="%s" stroke-width="2"><title>%s: %d threads -&gt; %.1f tok/s total</title></circle>' % (X11(b), Y11a(v), color, PANEL, lbl, b, v))
c11.append('<text x="%.1f" y="%.1f" fill="%s" font-size="13" font-weight="700">the 9th thread: 125 &#8594; 20</text>' % (X11(9) + 6, Y11a(19.7) - 10, BAD))
c11.append('<text x="%.1f" y="%.1f" fill="%s" font-size="13" font-weight="700" text-anchor="end">159 @ 16 threads</text>' % (X11(16) - 8, Y11a(158.5) + 24, BLUE))
for b in _dB:
    c11.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">%d</text>' % (X11(b), p2T + p2H + 16, DIM, b))
c11.append('<text x="%.1f" y="%d" fill="%s" font-size="12" text-anchor="middle" font-weight="600">threads in flight</text>' % ((L11 + W11 - R11) / 2, H11 - 24, INK))
c11.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">same card - same watts - one line of code</text>' % ((L11 + W11 - R11) / 2, H11 - 6, DIM))
c11.append('<rect x="%d" y="%d" width="12" height="4" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">stock (limit 8)</text>' % (L11 + 14, p1T + 8, ORANGE, L11 + 30, p1T + 13, DIM))
c11.append('<rect x="%d" y="%d" width="12" height="4" rx="2" fill="%s"/><text x="%d" y="%d" fill="%s" font-size="11">patched (limit 16)</text>' % (L11 + 14, p1T + 26, BLUE, L11 + 30, p1T + 31, DIM))
c11.append('</svg>')
chart11 = "".join(c11)

# ---- Chart 12: frame-pacing strips (expF; placeholder until both JSONLs land)
_f8p = _os.path.join(_RESDIR, "expF-mistral24b-mmv8.jsonl")
_f16p = _os.path.join(_RESDIR, "expF-mistral24b-mmv16.jsonl")
t12 = ""
if _os.path.exists(_f8p) and _os.path.exists(_f16p):
    def _fload(p):
        return [_json.loads(x)["total_s"] for x in io.open(p, encoding="utf-8-sig") if x.strip()]
    _lat = {8: _fload(_f8p), 16: _fload(_f16p)}
    _ymax12 = max(max(_lat[8]), max(_lat[16])) * 1.08
    W12, H12 = 760, 430; L12, R12 = 56, 20
    _pH = 150; _tops = {8: 44, 16: 44 + _pH + 62}
    c12 = [svg_open(W12, H12, "Frame pacing: per-request latency, stock versus patched, 10 threads in flight")]
    for lim, color, lbl in ((8, ORANGE, "stock (limit 8)"), (16, BLUE, "patched (limit 16)")):
        vals = _lat[lim]; top = _tops[lim]
        med = _st.median(vals)
        stut = sum(1 for v in vals if v > 2 * med)
        n = len(vals)
        bw = (W12 - L12 - R12) / max(1, n)
        def Y12(v, _t=top): return _t + _pH * (1 - v / _ymax12)
        _step12 = 50 if _ymax12 > 150 else (20 if _ymax12 > 60 else 10)
        for gv in range(0, int(_ymax12) + 1, _step12):
            c12.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>' % (L12, Y12(gv), W12 - R12, Y12(gv), GRID))
            c12.append('<text x="%d" y="%.1f" fill="%s" font-size="10" text-anchor="end">%d</text>' % (L12 - 6, Y12(gv) + 3, DIM, gv))
        for i, v in enumerate(vals):
            fill = BAD if v > 2 * med else color
            x = L12 + i * bw
            c12.append('<rect x="%.2f" y="%.1f" width="%.2f" height="%.1f" fill="%s"><title>request %d: %.2f s</title></rect>' % (x, Y12(v), max(0.8, bw - 0.6), top + _pH - Y12(v), fill, i + 1, v))
        c12.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1.2" stroke-dasharray="4 4"/>' % (L12, Y12(med), W12 - R12, Y12(med), INK))
        c12.append('<text x="%d" y="%d" fill="%s" font-size="12" font-weight="600">%s  <tspan fill="%s" font-weight="400">median %.1f s - %d stutters (&gt;2x median) of %d requests</tspan></text>' % (L12, top - 8, color, lbl, DIM, med, stut, n))
        t12 += "<tr><td>%s</td><td>%d</td><td>%.2f</td><td>%.2f</td><td>%d</td></tr>" % (lbl, n, med, _st.mean(sorted(vals)[-max(1, n // 100):]), stut)
    c12.append('<text x="%.1f" y="%d" fill="%s" font-size="11" text-anchor="middle">request index (completion order) - seconds per request, lower and flatter is better</text>' % ((L12 + W12 - R12) / 2, H12 - 8, DIM))
    c12.append('</svg>')
    chart12 = "".join(c12)
    t12 = "<tr><th>config</th><th>n</th><th>median s</th><th>1%%-low s</th><th>stutters</th></tr>" + t12
else:
    chart12 = '<p class="dim">Experiment F frame-pacing data pending - regenerate after expF-flow.ps1 completes.</p>'
    t12 = "<tr><td>pending</td></tr>"

# ============================================================ assemble page
page = io.open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "charts.html"), encoding="utf-8").read()
chartA, chartB = page.split("<!--SPLIT-->")

html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OMEN Limit Test — Chart Annex</title>
<style>
:root { --bg:#12151a; --panel:#1a1f27; --ink:#d8dee8; --dim:#8b96a5; --line:#2a3240;
        --good:#4cc38a; --warn:#e5b567; --bad:#e5675c; --accent:#5ea0ef; }
body { background:var(--bg); color:var(--ink); font:15px/1.55 "Segoe UI",system-ui,sans-serif;
       max-width:1080px; margin:2rem auto; padding:0 1.25rem; }
h1 { font-size:1.5rem; border-bottom:2px solid var(--accent); padding-bottom:.4rem; }
h2 { font-size:1.15rem; color:var(--accent); margin-top:2.4rem; }
table { border-collapse:collapse; width:100%; margin:.8rem 0 0; font-size:.88rem; }
th,td { border:1px solid var(--line); padding:.35rem .6rem; text-align:left; }
th { background:var(--panel); }
.dim { color:var(--dim); } .good { color:var(--good); font-weight:600; } .bad { color:var(--bad); font-weight:600; }
figure { margin:1rem 0 0; background:var(--panel); border-radius:10px; padding:1rem; }
figcaption { color:var(--dim); font-size:.86rem; margin-top:.5rem; }
details { margin:.4rem 0 0; } summary { color:var(--dim); cursor:pointer; font-size:.86rem; }
</style>
</head>
<body>
<h1>OMEN Limit Test — Chart Annex</h1>
<p class="dim">Every slice of the 2026-08-20→21 campaign, drawn. Companion to <code>OMEN-LIMIT-TEST-2026-08.html</code>;
raw data at <code>E:\\work\\battlemage\\burnin-2026-08\\</code>. Hover any mark for exact values. Same two Arc Pro B70s throughout — old box = AM4/Win10/driver 8801/32 GB DDR4, new box = 285K/Win11/8974/128 GB DDR5.</p>

<h2>1 · Old box → new box, same silicon (speedup ratios)</h2>
<figure>%%C1%%<figcaption>What moved: prompt processing (compute-bound — COOPMAT + driver era). What didn't: decode (bandwidth-bound — same VRAM, same physics). The one regression (14B tg −6%%) is the price of nothing else changing.</figcaption>
<details><summary>data</summary><table><tr><th>metric</th><th>old</th><th>new</th><th>ratio</th></tr>
<tr><td>70B dual pp1024</td><td>151.4</td><td>384.5</td><td class="good">×2.54</td></tr>
<tr><td>Q2 replica aggregate</td><td>95.7 (floor)</td><td>157</td><td class="good">×1.64</td></tr>
<tr><td>30B-A3B single-stream</td><td>81.7</td><td>~95–105</td><td class="good">×1.22</td></tr>
<tr><td>14B single pp512</td><td>1261/1336</td><td>1451/1474</td><td class="good">×1.13</td></tr>
<tr><td>70B dual tg256</td><td>11.28</td><td>11.7</td><td class="good">×1.04</td></tr>
<tr><td>14B single tg128</td><td>50.1</td><td>46.7/47.3</td><td class="bad">×0.94</td></tr></table></details></figure>

<h2>2 · Model size vs decode speed — and the MoE cheat code</h2>
<figure>%%C2%%<figcaption>Dense models ride the bandwidth curve down (every token reads every weight). MoE models break it: the 30B-A3B activates ~3B params/token and decodes 2× faster than a dense 14B half its size; the 120B lands near the 70B despite 1.5× the weights. Size on disk ≠ speed — architecture is the lever.</figcaption>
<details><summary>data</summary><table><tr><th>model</th><th>GB</th><th>tok/s</th><th>class</th></tr>
<tr><td>Qwen2.5-14B</td><td>8.4</td><td>47.0</td><td>dense</td></tr><tr><td>Qwen2.5-32B</td><td>18.5</td><td>23.7 (old-box)</td><td>dense</td></tr>
<tr><td>Llama-3.3-70B</td><td>39.6</td><td>11.7</td><td>dense</td></tr><tr><td>Qwen3-30B-A3B</td><td>17.3</td><td>~95</td><td>MoE</td></tr>
<tr><td>gpt-oss-120b</td><td>59.0</td><td>14.7 (e2e)</td><td>MoE</td></tr></table></details></figure>

<h2>3 · Heat vs load — the whole thermal story in one line</h2>
<figure>%%C3%%<figcaption>Load rises left to right — and peaks FALL after the sail. The finale (maximum simultaneous load of the whole campaign) never crossed 70 °C. A 6'×12' cardboard duct, pillars of furniture, one fan: −24 °C at the worst point, measured at 2 s cadence.</figcaption></figure>

<h2>4 · Load time vs model size (cold vs warm)</h2>
<figure>%%C4%%<figcaption>Cold ≈ linear in bytes off the 4 TB NVMe (~0.4 GB/s effective incl. VRAM upload); warm page cache collapses it ~6–10×. The 120B never got a warm run — its 59 GB doesn't fit in cache beside a resident server. Includes 3 s health-poll granularity.</figcaption></figure>

<h2>5 · Stability under abuse — 3 hours of waves, one flat line</h2>
<figure>%%C5%%<figcaption>Every bucket of the 2 h triple-co-load soak and the 1 h everything-at-once finale. The story is that there is no story: no drift, no cliff, CV ~5%% in both, floor never touched. This is what "the rung is production-grade" looks like as a line.</figcaption></figure>

<h2>6 · The un-retraction — replica scaling, fifteen months later</h2>
<figure>%%C6%%<figcaption>May 2026: 1.96× measured, retracted — the display adapter TDR'd under symmetric load. August 2026: neither card drives a display; 1.85× stands with zero events. The architecture was right all along; the desktop was the bug.</figcaption></figure>

<h2>7 · Prompt processing vs context depth (FA crossover)</h2>
<figure>%%CA%%<figcaption>fa-off wins big below 16k and produces nothing at ≥24k; fa-on runs the full ladder; q8-KV rides fa-on at a ~5–6%% tax. The rung serves 65k, so it runs fa-on; a future short-context replica lane would want fa-off.</figcaption></figure>

<h2>8 · The concurrency knee that would not move</h2>
<figure>%%CB%%<figcaption>Throughput climbs to N*=8 then falls off a cliff with p95 exploding — the identical shape the 32 GB box produced, reproduced here with 78 GB of commit free. The knee lives in the cards/driver, not the host. Admission control pins at 8; the rung serves at 4 for latency headroom.</figcaption></figure>

<h1 style="margin-top:3rem">Campaign 2 — the knee, moved</h1>
<p class="dim">2026-08-22. The knee turned out to be one line of llama.cpp source: <code>mul_mat_vec_max_cols = 8</code> in the
Vulkan backend. Batches of ≤8 sequences ride a fast matrix-vector kernel; the 9th falls onto a general matmul path and
throughput collapses. We patched the constant into a runtime knob (<code>GGML_VK_MMV_MAX_COLS</code>, default 8 = stock,
one file, +24/−3) and proved causality by dose-response. Protocol: <code>llama-batched-bench -ngl 999 -fa 1 -npp 512
-ntg 128</code>, warm reps, cool-start gated, spill-guarded (silent VidMm demotion detected and refused). Same build,
same card, only the env var moves.</p>

<h2>9 · Dose-response — the cliff obeys the knob</h2>
<figure>%%C9%%<figcaption>Three runs of the same binary on Mistral-Small-24B, one B70: limit 4, 8 (stock), 16. The
curves are identical until each one's own limit, then each collapses at exactly the first batch past it — and past the
cliff all three converge on the same matmul-path floor. Move the number, the cliff moves. That is causality, not
correlation.</figcaption>
<details><summary>data (aggregate tok/s, warm rep2)</summary><table>%%T9%%</table></details></figure>

<h2>10 · Seven models everyone knows, one constant</h2>
<figure>%%C10%%<figcaption>Meta, OpenAI, Microsoft, Google, Alibaba, Mistral — stock vs patched at 16 threads on the
same silicon. Every dense model gains ×3.5–3.7 at B=16 (and ×6.6–6.9 at the 9th thread). The two sparse models gain
less — their expert FFNs dispatch through a separate gate (MUL_MAT_ID) with its own limit; only their dense attention
projections ride this knob. The split is the mechanism, visible.</figcaption>
<details><summary>data (aggregate tok/s)</summary><table>%%T10%%</table></details></figure>

<h2>11 · The dyno sheet</h2>
<figure>%%C11%%<figcaption>Threads as RPM, aggregate tok/s as horsepower, per-thread tok/s as torque. Stock rev-limits
at 8 and the 9th thread drops total power 125→20. The patch moves the redline to 16: 159 total at the new limit —
more than stock ever reaches at any thread count. Same card, same watts, one line of code.</figcaption></figure>

<h2>12 · Frame pacing — what the cliff feels like</h2>
<figure>%%C12%%<figcaption>The gamer's lens: 200 requests at 10 concurrent, per-request latency as a frame-time strip.
The result is worse than stutter: stock past its cliff is <b>uniformly</b> broken — median 204 s per request, every
stream crawling at ~1.3 tok/s, well below reading speed. Patched runs the identical load at median 38 s and ~5.4 tok/s
per stream (×5.4). Ten threads in flow on the same silicon — the stock build can hold eight.</figcaption>
<details><summary>data</summary><table>%%T12%%</table></details></figure>

<p class="dim">Generated 2026-08-21→22 from campaign artifacts (charts 9–12 re-read the raw results on E: at each
regeneration). Palette CVD-validated on this surface (six checks). Each chart's data table doubles as its accessible
view.</p>
</body>
</html>
"""
for k,v in (("%%C1%%",chart1),("%%C2%%",chart2),("%%C3%%",chart3),("%%C4%%",chart4),("%%C5%%",chart5),("%%C6%%",chart6),("%%CA%%",chartA.strip()),("%%CB%%",chartB.strip()),
            ("%%C9%%",chart9),("%%T9%%",t9),("%%C10%%",chart10),("%%T10%%",t10),("%%C11%%",chart11),("%%C12%%",chart12),("%%T12%%",t12)):
    html = html.replace(k, v)
html = html.replace("%%", "%")   # template captions escape literal percent as %%
io.open("C:/work/commandcenter/OMEN-LIMIT-TEST-CHARTS-2026-08.html", "w", encoding="utf-8", newline="\n").write(html)
print("gallery written:", len(html), "bytes, 12 charts")
