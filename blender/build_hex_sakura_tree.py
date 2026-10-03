"""
HEX! Tokyo detailed sakura tree (Roblox-ready).

A gnarled, spreading cherry tree:
- dark bark with spiral ridges and the horizontal lenticel bands sakura bark is known for,
- flared roots,
- trunk -> limbs -> branches -> twigs,
- an umbrella crown of lumpy blossom clouds, shaded white on top to deep pink underneath,
- about a thousand individual five-petal flowers (notched sakura petals) on the clouds and along the bare twigs,
- loose petals on the ground and in the air.

Same units and single-palette-texture pipeline as the courts (import exactly like the courts).
Canopy is split into sectors so every MeshPart stays well under Roblox's triangle limit.

Run: blender --background --python blender/build_hex_sakura_tree.py -- [--render]
"""
import os
import sys
import math
import random
import bpy
import bmesh
from mathutils import Vector, noise

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_hex_courts as C  # noqa: E402  (materials, mesh builder, palette bake, export)

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
C.OUT_DIR = os.path.join(C.REPO, "export", "trees")
C.COL.update({
    "Bark_Dark":      ("#2a1e1f", 0.92, 0.0),
    "Bark":           ("#45322f", 0.90, 0.0),
    "Bark_Light":     ("#5c4740", 0.88, 0.0),
    "Blossom_White":  ("#fff2f6", 0.70, 0.0),
    "Blossom_Pale":   ("#f9d4e0", 0.70, 0.0),
    "Blossom_Pink":   ("#f2b0c6", 0.70, 0.0),
    "Blossom_Deep":   ("#e28dac", 0.72, 0.0),
    "Blossom_Shade":  ("#bf6f8f", 0.75, 0.0),
    "Blossom_Center": ("#c8416c", 0.60, 0.0),
})

SEED = 2026
RNG = random.Random(SEED)
SQUASH = 0.8           # blossom clumps are a little wider than tall
GRID = 2.5             # spatial hash cell for clump neighbour tests
MAX_TRIS = 9000        # per MeshPart (Roblox limit is 20k; leave headroom)


# ---------------------------------------------------------------------------------------------- bark
def tube(faces, pts, radii, n, seed, bark=True, cap_end=True, twist=0.06):
    """Parallel-transport tube (no flipping on near-vertical paths) with a ridged bark profile."""
    pts = [Vector(p) for p in pts]
    rg = random.Random(seed)
    T = [(pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)]).normalized() for i in range(len(pts))]
    ref = Vector((0, 0, 1)) if abs(T[0].z) < 0.9 else Vector((1, 0, 0))
    N = (ref - T[0] * ref.dot(T[0])).normalized()
    prof = [1 + (rg.uniform(-0.09, 0.07) if n >= 8 else 0.0) for _ in range(n)]
    rings = []
    for i, p in enumerate(pts):
        if i:
            N = T[i - 1].rotation_difference(T[i]) @ N
            N = (N - T[i] * N.dot(T[i])).normalized()
        B = T[i].cross(N)
        ring = []
        for k in range(n):
            a = 2 * math.pi * k / n + twist * i
            ring.append(p + (N * math.cos(a) + B * math.sin(a)) * radii[i] * prof[k])
        rings.append(ring)
    for i in range(len(rings) - 1):
        band = rg.random() < 0.22 and bark and n >= 8     # lenticels: short light horizontal streaks
        b0, bl = rg.randrange(n), rg.randint(1, max(1, n // 5))
        for k in range(n):
            q = (k + 1) % n
            if band and (k - b0) % n < bl:
                m = "Bark_Light"
            elif prof[k] < 0.96 and n >= 8:
                m = "Bark_Dark"
            else:
                m = "Bark"
            faces.append(([rings[i][k], rings[i][q], rings[i + 1][q], rings[i + 1][k]], m))
    if cap_end:
        faces.append((rings[-1], "Bark"))


def grow(p0, d, L, r0, r1, nseg, droop, wander=0.16, minz=-0.15):
    pts, d = [Vector(p0)], Vector(d).normalized()
    for i in range(1, nseg + 1):
        w = Vector((RNG.gauss(0, 1), RNG.gauss(0, 1), RNG.gauss(0, 0.6))) * wander
        g = Vector((0, 0, -droop if i > nseg * 0.45 else droop * 0.4))
        d = (d + w + g).normalized()
        if d.z < minz:  # gentle arch, never hanging down
            d.z = minz
            d.normalize()
        pts.append(pts[-1] + d * (L / nseg))
    radii = [r0 + (r1 - r0) * (i / nseg) ** 0.85 for i in range(nseg + 1)]
    return pts, radii


def rot_dir(d, ang, up_bias=0.0, out_bias=0.0):
    """Turn direction d by ang (radians) about a random perpendicular axis, with up/outward bias."""
    d = Vector(d).normalized()
    a = Vector((RNG.gauss(0, 1), RNG.gauss(0, 1), RNG.gauss(0, 1)))
    a = (a - d * a.dot(d)).normalized()
    nd = (d * math.cos(ang) + a * math.sin(ang)).normalized()
    h = Vector((nd.x, nd.y, 0))
    if h.length > 1e-3:
        nd += h.normalized() * out_bias
    nd.z += up_bias
    return nd.normalized()


def build_wood():
    trunk_f, branch_f, tips, twigs, mids = [], [], [], [], []

    # trunk: leaning, gently S-curved, flared at the base
    lean = Vector((0.16, -0.08, 1)).normalized()
    pts, radii = grow((0, 0, -0.3), lean, 10.2, 1.45, 0.98, 38, droop=-0.006, wander=0.025)
    for i, f in enumerate((1.6, 1.42, 1.28, 1.17, 1.09, 1.04, 1.01)):
        radii[i] *= f
    tube(trunk_f, pts, radii, 18, 1, cap_end=True, twist=0.045)

    # flared roots
    for k in range(7):
        a = 2 * math.pi * k / 7 + RNG.uniform(-0.25, 0.25)
        dv = Vector((math.cos(a), math.sin(a), 0))
        L = RNG.uniform(2.4, 3.4)
        rp = [Vector((0, 0, 0)) + dv * (0.35 + L * t) + Vector((0, 0, 1.3 * (1 - t) ** 2 - 0.12))
              for t in [j / 6 for j in range(7)]]
        rr = [0.66 * (1 - 0.8 * j / 6) for j in range(7)]
        tube(trunk_f, rp, rr, 9, 100 + k)

    top, top_d = pts[-1], (pts[-1] - pts[-2]).normalized()

    def branch(p0, d, L, r0, depth, seed):
        nseg = (9, 7, 5)[depth - 1]
        r1 = (0.3, 0.1, 0.03)[depth - 1]
        droop = (0.015, 0.03, 0.05)[depth - 1]
        bp, br = grow(p0, d, L, r0, r1, nseg, droop)
        tube(branch_f, bp, br, (11, 8, 5)[depth - 1], seed, bark=depth < 3, cap_end=True)
        if depth == 3:
            tips.append((bp[-1], (bp[-1] - bp[-2]).normalized()))
            twigs.append(bp)
            return
        if depth == 2:
            mids.extend((bp[int(len(bp) * 0.65)], bp[-1]))
        if depth == 1:  # fill the inner crown along the limbs
            mids.extend((bp[int(len(bp) * 0.7)], bp[-1]))
        kids = [(0.45, 1), (0.7, 1), (1.0, 2)] if depth == 1 else [(0.5, 1), (0.8, 1), (1.0, 2)]
        for t, cnt in kids:
            i = min(len(bp) - 1, int(round(t * nseg)))
            pd = (bp[min(i + 1, nseg)] - bp[max(i - 1, 0)]).normalized()
            for _ in range(cnt):
                if depth == 1:
                    nd = rot_dir(pd, math.radians(RNG.uniform(26, 44)), up_bias=0.08, out_bias=0.45)
                    branch(bp[i], nd, RNG.uniform(5.5, 7.5), br[i] * 0.72, 2, seed * 10 + i)
                else:
                    nd = rot_dir(pd, math.radians(RNG.uniform(28, 46)), up_bias=0.3, out_bias=0.25)
                    branch(bp[i], nd, RNG.uniform(2.6, 3.8), br[i] * 0.75, 3, seed * 10 + i + 7)

    base_a = RNG.uniform(0, 2 * math.pi)
    for k in range(5):  # four wide spreading limbs + one rising leader that fills the top of the crown
        a = base_a + 2 * math.pi * k / 4 + RNG.uniform(-0.35, 0.35)
        e = math.radians(RNG.uniform(62, 70) if k == 4 else RNG.uniform(28, 40))
        d = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
        start = top - top_d * RNG.uniform(0.0, 1.4)
        branch(start, d, RNG.uniform(6.5, 7.5) if k == 4 else RNG.uniform(10.0, 12.5),
               0.5 if k == 4 else 0.74, 1, k + 1)
    return trunk_f, branch_f, tips, twigs, mids


# ---------------------------------------------------------------------------------------------- blossoms
_ICO = {}
PUFFS = []  # last built clumps (preview camera aims at one)
SHADES = ("Blossom_Shade", "Blossom_Deep", "Blossom_Pink", "Blossom_Pale", "Blossom_White")


def ico(sub=2):
    if sub not in _ICO:
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=sub, radius=1.0)
        bm.verts.ensure_lookup_table()
        _ICO[sub] = ([v.co.copy() for v in bm.verts], [[v.index for v in f.verts] for f in bm.faces])
        bm.free()
    return _ICO[sub]


class Puff:
    """One small fluffy blossom clump; the crown is hundreds of these clustered along the twigs."""
    def __init__(self, c, r):
        self.c, self.r = Vector(c), r
        self.off = Vector((RNG.uniform(0, 100), RNG.uniform(0, 100), RNG.uniform(0, 100)))
        self.base = RNG.choices((1, 2, 3), (0.15, 0.5, 0.35))[0]

    def rad(self, dvec):
        """Lumpy radius in direction dvec (unit)."""
        return self.r * (1 + 0.24 * noise.noise(dvec * 2.0 + self.off) + 0.08 * noise.noise(dvec * 5.0 + self.off))

    def point(self, dvec):
        p = dvec * self.rad(dvec)
        return self.c + Vector((p.x, p.y, p.z * SQUASH))

    def inside(self, q, k=0.97):
        lq = q - self.c
        lq = Vector((lq.x, lq.y, lq.z / SQUASH))
        if lq.length < 1e-6:
            return True
        return lq.length < self.rad(lq.normalized()) * k


def place_puffs(twigs, mids):
    """Clumps cluster along every twig (sitting on top of the wood) and at the branch ends."""
    puffs = []

    def jit(p, hz, z0, z1):
        return p + Vector((RNG.gauss(0, hz), RNG.gauss(0, hz), RNG.uniform(z0, z1)))

    for tw in twigs:
        n = len(tw) - 1
        for t in (0.2, 0.4, 0.55, 0.7, 0.85, 1.0):
            x = t * n
            i = min(int(x), n - 1)
            p = tw[i].lerp(tw[i + 1], x - i)
            for _ in range(RNG.choice((1, 1, 2))):
                puffs.append(Puff(jit(p, 0.45, 0.05, 0.55), RNG.uniform(0.6, 1.0)))
    for p in mids:
        for _ in range(2):
            puffs.append(Puff(jit(p, 0.6, 0.1, 0.7), RNG.uniform(0.75, 1.15)))
    return puffs


def shade(pf, nz, h):
    i = pf.base + (1 if nz > 0.45 else 0) - (1 if nz < -0.25 else 0) - (1 if nz < -0.7 else 0)
    i += 1 if h > 0.8 and nz > 0 else 0
    i -= 1 if h < 0.2 and nz < 0.3 else 0
    return SHADES[max(0, min(4, i))]


def puff_faces(pf, grid, zlo, zhi):
    """Visible faces of one clump (faces buried inside neighbouring clumps are dropped)."""
    verts, fs = ico()
    P = [pf.point(v.normalized()) for v in verts]
    gx, gy, gz = (int(math.floor(c / GRID)) for c in pf.c)
    near = [o for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)
            for o in grid.get((gx + i, gy + j, gz + k), ()) if o is not pf and (o.c - pf.c).length < o.r + pf.r + 0.6]
    out = []
    for f in fs:
        q = [P[i] for i in f]
        cen = sum(q, Vector()) / 3
        if any(all(o.inside(x, 0.9) for x in q) for o in near):
            continue
        n = (q[1] - q[0]).cross(q[2] - q[0])
        if n.length < 1e-9:
            continue
        n.normalize()
        if n.dot(cen - pf.c) < 0:
            q = q[::-1]
            n = -n
        h = (cen.z - zlo) / max(zhi - zlo, 1e-3)
        out.append((q, shade(pf, n.z, h), cen, n))
    return out


def flower(faces, c, nrm, s, rot=None, cup=0.14, center=True, petal_mat=None):
    """Five notched sakura petals around c, facing nrm (CCW about nrm)."""
    nrm = Vector(nrm).normalized()
    ref = Vector((0, 0, 1)) if abs(nrm.z) < 0.95 else Vector((1, 0, 0))
    u = nrm.cross(ref).normalized()
    v = nrm.cross(u)
    rot = RNG.uniform(0, 2 * math.pi) if rot is None else rot
    pm = petal_mat or RNG.choices(("Blossom_White", "Blossom_Pale", "Blossom_Pink"), (0.45, 0.4, 0.15))[0]

    def P(a, r, lift=0.0):
        return c + (u * math.cos(a) + v * math.sin(a)) * r * s + nrm * lift * s

    for k in range(5):
        a = rot + 2 * math.pi * k / 5
        faces.append(([P(a, 0.1), P(a - 0.42, 0.62, cup * 0.6), P(a - 0.13, 1.0, cup), P(a, 0.84, cup),
                       P(a + 0.13, 1.0, cup), P(a + 0.42, 0.62, cup * 0.6)], pm))
    if center:
        faces.append(([P(rot + 2 * math.pi * k / 3 + 0.6, 0.2, 0.05) for k in range(3)], "Blossom_Center"))


def petal(faces, c, nrm, s, rot, m):
    nrm = Vector(nrm).normalized()
    ref = Vector((0, 0, 1)) if abs(nrm.z) < 0.95 else Vector((1, 0, 0))
    u = nrm.cross(ref).normalized()
    v = nrm.cross(u)

    def P(x, y):
        return c + (u * (x * math.cos(rot) - y * math.sin(rot)) + v * (x * math.sin(rot) + y * math.cos(rot))) * s

    faces.append(([P(-0.5, 0), P(0.1, -0.32), P(0.5, -0.16), P(0.38, 0), P(0.5, 0.16), P(0.1, 0.32)], m))


def tri_count(faces):
    return sum(len(f[0]) - 2 for f in faces)


def emit(name, faces, coll, root, smooth=False):
    mb = C.MB()
    for pts, m in faces:
        mb.face([tuple(p) for p in pts], m)
    ob = mb.build(name, coll, root, smooth=smooth)
    print("   %-16s %6d tris" % (name, tri_count(faces)))
    return ob


# ---------------------------------------------------------------------------------------------- build
def build():
    coll = bpy.data.collections.new("HEX_Sakura_Tree")
    bpy.context.scene.collection.children.link(coll)
    root = C.empty("HEX_Sakura_Tree", coll)

    trunk_f, branch_f, tips, twigs, mids = build_wood()
    puffs = place_puffs(twigs, mids)
    PUFFS[:] = puffs
    zlo = min(p.c.z - p.r for p in puffs)
    zhi = max(p.c.z + p.r for p in puffs)
    grid = {}
    for pf in puffs:
        grid.setdefault(tuple(int(math.floor(c / GRID)) for c in pf.c), []).append(pf)
    print("   %d blossom clumps" % len(puffs))

    # per-sector buckets: blossom clouds + their flowers
    NS = 8
    sector = lambda q: int(((math.atan2(q.y, q.x) + math.pi) / (2 * math.pi)) * NS) % NS  # noqa: E731
    buckets = [[] for _ in range(NS)]
    for pf in puffs:
        vis = puff_faces(pf, grid, zlo, zhi)
        b = buckets[sector(pf.c)]
        b += [(q, m) for q, m, _, _ in vis]
        # flowers sit proud of the cloud, more on the sunlit/outer side
        area = sum(((q[1] - q[0]).cross(q[2] - q[0])).length / 2 for q, _, _, _ in vis)
        nfl = int(area * 0.16 + RNG.random())
        cand = [x for x in vis if x[3].z > -0.3]
        for _ in range(nfl if cand else 0):
            q, _, cen, n = RNG.choice(cand)
            w1, w2 = RNG.random(), RNG.random()
            if w1 + w2 > 1:
                w1, w2 = 1 - w1, 1 - w2
            p = q[0] + (q[1] - q[0]) * w1 + (q[2] - q[0]) * w2
            nn = (n + Vector((RNG.gauss(0, 0.25), RNG.gauss(0, 0.25), RNG.gauss(0, 0.25)))).normalized()
            flower(b, p + n * (0.04 + 0.16 * RNG.random() ** 2), nn, RNG.uniform(0.24, 0.34))

    # bunches of flowers hanging along the bare twigs (sakura bloom in clusters on the wood)
    for (tp, td), tw in zip(tips, twigs):
        for _ in range(3):  # bunch at the tip, poking out of the clumps
            side = rot_dir(td, math.radians(RNG.uniform(35, 80)), up_bias=0.1)
            flower(buckets[sector(tp)], tp + side * 0.16, side, RNG.uniform(0.26, 0.34))
        i = int(0.15 * (len(tw) - 1))  # and a small one low on the twig, under the clumps
        p, d = tw[i], (tw[i + 1] - tw[i]).normalized()
        for _ in range(2):
            side = rot_dir(d, math.radians(90), up_bias=-0.25)
            flower(buckets[sector(p)], p + side * 0.16, side, RNG.uniform(0.24, 0.3))

    # split sectors into MeshParts under the triangle budget
    canopy = []
    idx = 1
    for b in buckets:
        chunk, n = [], 0
        for f in b:
            t = len(f[0]) - 2
            if n + t > MAX_TRIS and chunk:
                canopy.append(emit("Canopy_%02d" % idx, chunk, coll, root, smooth=True))
                idx, chunk, n = idx + 1, [], 0
            chunk.append(f)
            n += t
        if chunk:
            canopy.append(emit("Canopy_%02d" % idx, chunk, coll, root, smooth=True))
            idx += 1

    emit("Trunk", trunk_f, coll, root, smooth=True)
    emit("Branches", branch_f, coll, root, smooth=True)

    # fallen petals on the ground under the crown, plus a few drifting in the air
    gp = []
    for _ in range(420):
        r = 18 * math.sqrt(RNG.random())
        a = RNG.uniform(0, 2 * math.pi)
        c = Vector((r * math.cos(a), r * math.sin(a), 0.03 + RNG.uniform(0, 0.01)))
        petal(gp, c, (0, 0, 1), RNG.uniform(0.28, 0.4), RNG.uniform(0, 6.3),
              RNG.choices(("Blossom_White", "Blossom_Pale", "Blossom_Pink"), (0.35, 0.45, 0.2))[0])
    emit("Petals_Ground", gp, coll, root)
    ap = []
    for _ in range(90):
        r = 13 * math.sqrt(RNG.random())
        a = RNG.uniform(0, 2 * math.pi)
        c = Vector((r * math.cos(a), r * math.sin(a), RNG.uniform(2.0, zlo + 2)))
        n = Vector((RNG.gauss(0, 1), RNG.gauss(0, 1), RNG.uniform(0.3, 1.5))).normalized()
        petal(ap, c, n, RNG.uniform(0.3, 0.4), RNG.uniform(0, 6.3), RNG.choice(("Blossom_White", "Blossom_Pale")))
    emit("Petals_Air", ap, coll, root)
    return root


# ---------------------------------------------------------------------------------------------- preview
def preview(root):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 48
    sc.cycles.use_denoising = True
    w = bpy.data.worlds.new("w")
    sc.world = w
    w.color = (0.42, 0.5, 0.62)
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.2
    sun.data.angle = math.radians(3)
    sun.rotation_euler = (math.radians(50), 0.15, math.radians(-35))
    sc.collection.objects.link(sun)
    me = bpy.data.meshes.new("g")
    me.from_pydata([(-200, -200, 0), (200, -200, 0), (200, 200, 0), (-200, 200, 0)], [], [(0, 1, 2, 3)])
    me.materials.append(C.mat("Court_Graphite"))
    sc.collection.objects.link(bpy.data.objects.new("g", me))
    d = C.MB()  # 5.2-stud reference avatar (render only)
    ox, oy = 4.0, -3.0
    for b in ((-1.0, -0.5, 0, -0.05, 0.5, 2.0), (0.05, -0.5, 0, 1.0, 0.5, 2.0), (-1.0, -0.5, 2.0, 1.0, 0.5, 4.0),
              (-2.0, -0.5, 2.0, -1.05, 0.5, 4.0), (1.05, -0.5, 2.0, 2.0, 0.5, 4.0), (-0.6, -0.6, 4.0, 0.6, 0.6, 5.2)):
        d.box(b[0] + ox, b[1] + oy, b[2], b[3] + ox, b[4] + oy, b[5], "Concrete")
    d.build("avatar", bpy.context.scene.collection)

    ld = sun.rotation_euler.to_matrix() @ Vector((0, 0, 1))  # towards the sun
    lh = Vector((ld.x, ld.y, 0)).normalized()
    far = max(PUFFS, key=lambda p: p.c.dot(lh) + 0.4 * p.c.z)  # an outer, sunlit clump
    fc = far.c
    out = os.path.join(C.REPO, "renders", "trees")
    os.makedirs(out, exist_ok=True)
    shots = (("sakura_tree_hero", (40, -48, 14), (0, 0, 13), 35, (1400, 1000)),
             ("sakura_tree_below", (7.5, -9, 2.6), (-1, 2, 17), 20, (1200, 1000)),
             ("sakura_tree_closeup", tuple(fc + lh * 12 + Vector((0, 0, 1.5))), tuple(fc), 55, (1200, 900)),
             ("sakura_tree_top", (0, -0.01, 80), (0, 0, 0), 30, (1000, 1000)))
    for name, loc, tgt, lens, res in shots:
        cd = bpy.data.cameras.new(name)
        cam = bpy.data.objects.new(name, cd)
        sc.collection.objects.link(cam)
        cam.location = loc
        cam.rotation_euler = (Vector(tgt) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        cd.lens = lens
        cd.clip_end = 2000
        sc.camera = cam
        sc.render.resolution_x, sc.render.resolution_y = res
        sc.render.filepath = os.path.join(out, name + ".png")
        bpy.ops.render.render(write_still=True)
        print("   rendered", name)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    root = build()
    objs = [o for o in C.all_children(root) if o.type == "MESH"]
    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in objs)
    print("   total %d tris in %d meshes" % (tris, len(objs)))
    if "--render" in ARGV:
        preview(root)
        for n in ("avatar", "g"):
            if n in bpy.data.objects:
                bpy.data.objects.remove(bpy.data.objects[n])
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "HEX_Sakura_Tree.blend"), compress=True)
    objs = C.all_children(root)
    C.palette_bake(objs, fname="sakura_tree_palette.png")
    C.to_reference_units(objs)
    C.export(root, "HEX_Sakura_Tree.fbx")


if __name__ == "__main__":
    main()
