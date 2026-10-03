"""
HEX! — Tokyo street courts (FULL + HALF) generator for Blender 4.2+/5.x

Imports the reference court (court/HEX_Basketball_Court_v4.fbx) FIRST and takes from it, unchanged:
  * court surface / apron extents and slab depth
  * the exact court-line geometry and the 3-point / key zone shapes
  * hoop placement: rim centre, rim height, rim radius, backboard plane + size
Then rebuilds ONLY the appearance as a clean Tokyo/Shibuya outdoor court + detailed outdoor hoops.

Deliverables (nothing else — no props, no surroundings):
  HEX_Tokyo_FullCourt : Floor, Lines, Graphics, Border, Hoop_A, Hoop_B
  HEX_Tokyo_HalfCourt : Floor, Lines, Graphics, Border, Hoop

Run:  blender --background --python blender/build_hex_courts.py -- [--render] [--no-export]
Units: identical to the reference FBX (metre FBX units, every mesh object scaled x3.571), so the courts
import exactly like HEX_Basketball_Court_v4 did.
"""
import bpy
import bmesh
import math
import os
import sys
import random
from contextlib import contextmanager
from mathutils import Vector, Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REF_FBX = os.path.join(REPO, "court", "HEX_Basketball_Court_v4.fbx")
OUT_DIR = os.path.join(REPO, "export", "courts")
TEX_DIR = os.path.join(HERE, "textures", "courts")
ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DO_RENDER = "--render" in ARGV
DO_EXPORT = "--no-export" not in ARGV
REF_SCALE = 1 / 0.28  # object scale used by the reference file (3.5714)


# ---------------------------------------------------------------------------------------------- colours
COL = {
    "Court_Graphite":   ("#2b2b2e", 0.85, 0.0),
    "Court_Graphite3":  ("#242427", 0.85, 0.0),
    "Court_Navy":       ("#222a3f", 0.85, 0.0),
    "Court_NavyDeep":   ("#171d2e", 0.80, 0.0),
    "Apron_Charcoal":   ("#1c1d21", 0.90, 0.0),
    "Line_WarmWhite":   ("#e6e9ed", 0.60, 0.0),   # soft white / very light cool grey
    "Sakura":           ("#e3a3b8", 0.60, 0.0),   # restrained sakura accent
    "Sakura_Bright":    ("#f07fa5", 0.55, 0.0),   # brighter pink, used extremely sparingly
    "Edge_Metal":       ("#3a3d43", 0.45, 0.7),
    "Steel_Graphite":   ("#34373d", 0.40, 0.75),
    "Steel_Dark":       ("#222428", 0.45, 0.70),
    "Aluminum":         ("#b9bec4", 0.30, 0.90),
    "Glass":            ("#cfe0ea", 0.04, 0.0),
    "Rim_Orange":       ("#ff5a14", 0.35, 0.40),
    "Net_White":        ("#f3f2ec", 0.80, 0.0),
    "Pad_Navy":         ("#1d2539", 0.75, 0.0),
    "Rubber":           ("#161618", 0.90, 0.0),
    "Concrete":         ("#8b8984", 0.90, 0.0),
    "Bolt":             ("#9ea4aa", 0.35, 0.90),
}
TILED = {"Court_Graphite": "graphite.png", "Court_Graphite3": "graphite3.png", "Court_Navy": "navy.png",
         "Apron_Charcoal": "apron.png"}
UV_TILE = 16.0  # studs per texture repeat on tiled surfaces


def srgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]


_M = {}


def mat(name):
    if name in _M:
        return _M[name]
    hx, rough, metal = COL[name]
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    p = m.node_tree.nodes["Principled BSDF"]
    c = srgb(hx) + [1.0]
    m.diffuse_color = c
    p.inputs["Base Color"].default_value = c
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    if name == "Glass":
        p.inputs["Alpha"].default_value = 0.35
        p.inputs["Transmission Weight"].default_value = 0.6
        try:
            m.surface_render_method = "BLENDED"
        except Exception:
            pass
    if name in TILED:
        path = os.path.join(TEX_DIR, TILED[name])
        if os.path.exists(path):
            tx = m.node_tree.nodes.new("ShaderNodeTexImage")
            tx.image = bpy.data.images.load(path, check_existing=True)
            m.node_tree.links.new(tx.outputs["Color"], p.inputs["Base Color"])
    _M[name] = m
    return m


# ---------------------------------------------------------------------------------------------- textures
def make_textures():
    """Seamless outdoor-acrylic tiles (fine aggregate speckle + soft mottling)."""
    os.makedirs(TEX_DIR, exist_ok=True)
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except Exception:
        print("!! numpy/Pillow missing - tiled textures skipped")
        return
    rng = np.random.default_rng(5)
    N = 512
    for name, fname in TILED.items():
        base = np.array([int(COL[name][0][i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32)
        low = rng.normal(0, 1, (N // 8, N // 8))
        low = np.kron(low, np.ones((8, 8)))
        img = Image.fromarray(((low - low.min()) / (np.ptp(low) + 1e-6) * 255).astype(np.uint8))
        tiled = Image.new("L", (N * 3, N * 3))
        for i in range(3):
            for j in range(3):
                tiled.paste(img, (i * N, j * N))
        mott = np.asarray(tiled.filter(ImageFilter.GaussianBlur(28)).crop((N, N, 2 * N, 2 * N)), np.float32) / 255
        out = base[None, None, :] * (0.93 + 0.12 * mott[..., None])
        speck = rng.random((N, N))
        out[speck > 0.985] *= 1.35
        out[speck < 0.02] *= 0.7
        fine = rng.normal(0, 3.0, (N, N, 1))
        out = np.clip(out + fine, 0, 255).astype(np.uint8)
        Image.fromarray(out, "RGB").save(os.path.join(TEX_DIR, fname))
    print("== court textures ->", TEX_DIR)


# ---------------------------------------------------------------------------------------------- mesh builder
class MB:
    def __init__(self):
        self.v, self.f, self.fm, self.mats, self.mi = [], [], [], [], {}
        self.M = Matrix.Identity(4)
        self.st = []

    @contextmanager
    def xf(self, M):
        self.st.append(self.M)
        self.M = self.M @ M
        try:
            yield
        finally:
            self.M = self.st.pop()

    def _m(self, n):
        if n not in self.mi:
            self.mi[n] = len(self.mats)
            self.mats.append(n)
        return self.mi[n]

    def face(self, pts, m):
        flip = self.M.to_3x3().determinant() < 0
        b = len(self.v)
        for p in pts:
            self.v.append(tuple(self.M @ Vector(p)))
        idx = list(range(b, b + len(pts)))
        self.f.append(idx[::-1] if flip else idx)
        self.fm.append(self._m(m))

    def box(self, x0, y0, z0, x1, y1, z1, m, skip=()):
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        z0, z1 = sorted((z0, z1))
        F = {"-z": [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)],
             "+z": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
             "-y": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
             "+y": [(x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)],
             "-x": [(x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)],
             "+x": [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]}
        for k, pts in F.items():
            if k not in skip:
                self.face(pts, m)

    def prism(self, poly, z0, z1, m, bottom=False, side=True):
        a = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                for i in range(len(poly)))
        if a < 0:
            poly = poly[::-1]
        n = len(poly)
        if side:
            for i in range(n):
                p, q = poly[i], poly[(i + 1) % n]
                self.face([(p[0], p[1], z0), (q[0], q[1], z0), (q[0], q[1], z1), (p[0], p[1], z1)], m)
        self.face([(p[0], p[1], z1) for p in poly], m)
        if bottom:
            self.face([(p[0], p[1], z0) for p in poly[::-1]], m)

    def cyl(self, cx, cy, r, z0, z1, n, m, bottom=False):
        self.prism([(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
                    for i in range(n)], z0, z1, m, bottom)

    def ring(self, cx, cy, r0, r1, z, n, m, a0=0.0, a1=2 * math.pi, h=0.0):
        """Flat annulus (or arc band) at height z (optionally a thin slab of height h)."""
        full = abs(a1 - a0 - 2 * math.pi) < 1e-6
        segs = n
        for i in range(segs):
            A = a0 + (a1 - a0) * i / segs
            B = a0 + (a1 - a0) * (i + 1) / segs
            p0 = (cx + r0 * math.cos(A), cy + r0 * math.sin(A))
            p1 = (cx + r1 * math.cos(A), cy + r1 * math.sin(A))
            p2 = (cx + r1 * math.cos(B), cy + r1 * math.sin(B))
            p3 = (cx + r0 * math.cos(B), cy + r0 * math.sin(B))
            self.face([(p0[0], p0[1], z + h), (p1[0], p1[1], z + h), (p2[0], p2[1], z + h), (p3[0], p3[1], z + h)], m)
            if h > 0:
                self.face([(p1[0], p1[1], z), (p2[0], p2[1], z), (p2[0], p2[1], z + h), (p1[0], p1[1], z + h)], m)
                self.face([(p3[0], p3[1], z), (p0[0], p0[1], z), (p0[0], p0[1], z + h), (p3[0], p3[1], z + h)], m)
        _ = full

    def torus(self, cx, cy, cz, R, r, nu, nv, m, a0=0.0, a1=2 * math.pi):
        def P(u, v):
            return (cx + (R + r * math.cos(v)) * math.cos(u), cy + (R + r * math.cos(v)) * math.sin(u),
                    cz + r * math.sin(v))
        for i in range(nu):
            u0, u1 = a0 + (a1 - a0) * i / nu, a0 + (a1 - a0) * (i + 1) / nu
            for j in range(nv):
                v0, v1 = 2 * math.pi * j / nv, 2 * math.pi * (j + 1) / nv
                self.face([P(u0, v0), P(u1, v0), P(u1, v1), P(u0, v1)], m)

    def beam(self, p0, p1, w, h, m, round_n=0):
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        L = d.length
        if L < 1e-6:
            return
        x = d / L
        y = Vector((0, 0, 1)).cross(x)
        if y.length < 1e-4:
            y = Vector((0, 1, 0))
        y.normalize()
        z = x.cross(y)
        M = Matrix(((x[0], y[0], z[0], p0[0]), (x[1], y[1], z[1], p0[1]), (x[2], y[2], z[2], p0[2]), (0, 0, 0, 1)))
        with self.xf(M):
            if round_n:
                pts = [(0, w / 2 * math.cos(2 * math.pi * k / round_n), h / 2 * math.sin(2 * math.pi * k / round_n))
                       for k in range(round_n)]
                for k in range(round_n):
                    a, b = pts[k], pts[(k + 1) % round_n]
                    self.face([(0, a[1], a[2]), (L, a[1], a[2]), (L, b[1], b[2]), (0, b[1], b[2])][::-1], m)
            else:
                self.box(0, -w / 2, -h / 2, L, w / 2, h / 2, m)

    def sweep(self, pts, profile, m, side=None, scales=None, closed=False, caps=True):
        """Sweep a CCW 2D profile [(u,v)...] along a path. side: fixed side axis for planar paths."""
        pts = [Vector(p) for p in pts]
        n = len(pts)
        rings = []
        for i, p in enumerate(pts):
            if closed:
                t = (pts[(i + 1) % n] - pts[(i - 1) % n]).normalized()
            else:
                t = (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized()
            sd = Vector(side) if side else Vector((0, 0, 1)).cross(t)
            if sd.length < 1e-4:
                sd = Vector((0, 1, 0))
            sd = (sd - t * sd.dot(t)).normalized()
            up = t.cross(sd)
            k = scales[i] if scales else 1.0
            rings.append([p + sd * u * k + up * v * k for u, v in profile])
        pairs = list(zip(rings, rings[1:] + ([rings[0]] if closed else [])))
        L = len(profile)
        for a_, b_ in pairs:
            for k in range(L):
                q = (k + 1) % L
                self.face([tuple(a_[k]), tuple(a_[q]), tuple(b_[q]), tuple(b_[k])], m)
        if caps and not closed:
            self.face([tuple(v) for v in rings[0][::-1]], m)
            self.face([tuple(v) for v in rings[-1]], m)

    def build(self, name, coll, parent=None, uv_tile=None, smooth=False):
        if not self.f:
            return None
        cx = sum(v[0] for v in self.v) / len(self.v)
        cy = sum(v[1] for v in self.v) / len(self.v)
        cz = min(v[2] for v in self.v)
        me = bpy.data.meshes.new(name)
        me.from_pydata([(v[0] - cx, v[1] - cy, v[2] - cz) for v in self.v], [], self.f)
        for n in self.mats:
            me.materials.append(mat(n))
        me.polygons.foreach_set("material_index", self.fm)
        uv = me.uv_layers.new(name="UVMap")
        data = []
        for poly in me.polygons:
            nx, ny, nz = (abs(c) for c in poly.normal)
            for li in poly.loop_indices:
                co = me.vertices[me.loops[li].vertex_index].co
                X, Y, Z = co.x + cx, co.y + cy, co.z + cz
                s = uv_tile or 8.0
                data += (X / s, Y / s) if nz >= max(nx, ny) else (Y / s, Z / s) if nx >= ny else (X / s, Z / s)
        uv.data.foreach_set("uv", data)
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(name, me)
        coll.objects.link(ob)
        ob.location = (cx, cy, cz)
        if smooth:
            smooth_weighted(ob)
        if parent:
            ob.parent = parent
            ob.location = Vector((cx, cy, cz)) - wpos(parent)
        return ob


def rrect(w, h, r, seg=3):
    """CCW rounded-rectangle profile (w along u, h along v)."""
    r = min(r, w / 2 - 1e-4, h / 2 - 1e-4)
    pts = []
    for cx, cy, a0 in ((w / 2 - r, -h / 2 + r, -90), (w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90),
                       (-w / 2 + r, -h / 2 + r, 180)):
        for k in range(seg + 1):
            a = math.radians(a0 + 90 * k / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def circle(r, n=10):
    return [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def smooth_weighted(ob):
    """Smooth shading with face-area weighted normals (flat faces stay flat, bevels read round)."""
    me = ob.data
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    bm = bmesh.new()
    bm.from_mesh(me)
    for e in bm.edges:
        if len(e.link_faces) == 2 and e.calc_face_angle(0) > math.radians(50):
            e.smooth = False
    bm.to_mesh(me)
    bm.free()
    mod = ob.modifiers.new("WN", "WEIGHTED_NORMAL")
    mod.keep_sharp = True
    mod.mode = "FACE_AREA"
    dg = bpy.context.evaluated_depsgraph_get()
    new = bpy.data.meshes.new_from_object(ob.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    ob.modifiers.clear()
    old = ob.data
    ob.data = new
    new.name = old.name
    bpy.data.meshes.remove(old)


def wpos(o):
    p = Vector()
    while o is not None:
        p += o.location
        o = o.parent
    return p


def empty(name, coll, parent=None, loc=(0, 0, 0)):
    e = bpy.data.objects.new(name, None)
    e.empty_display_size = 2
    coll.objects.link(e)
    e.location = loc
    if parent:
        e.parent = parent
        e.location = Vector(loc) - wpos(parent)
    return e


# ---------------------------------------------------------------------------------------------- reference
def load_reference():
    bpy.ops.import_scene.fbx(filepath=REF_FBX)
    objs = {o.name: o for o in bpy.data.objects}
    ref = {}

    def bounds(o):
        ws = [o.matrix_world @ Vector(c) for c in o.bound_box]
        return (Vector(map(min, *[(w.x, w.y, w.z) for w in ws])) if False else
                Vector((min(w.x for w in ws), min(w.y for w in ws), min(w.z for w in ws))),
                Vector((max(w.x for w in ws), max(w.y for w in ws), max(w.z for w in ws))))
    s0, s1 = bounds(objs["Court_Surface"])
    a0, a1 = bounds(objs["Court_Apron"])
    sl0, _ = bounds(objs["Court_Slab"])
    r0, r1 = bounds(objs["Rim_A"])
    b0, b1 = bounds(objs["Backboard_A"])
    ref["surf"] = (s1.x, s1.y)              # half extents of the playing surface
    ref["apron"] = (a1.x + 0.22, a1.y + 0.21)  # half extents incl. the apron edge marks
    ref["slab"] = -sl0.z
    ref["rim_c"] = ((r0.x + r1.x) / 2, (r0.y + r1.y) / 2)
    ref["rim_top"] = r1.z
    ref["rim_R"] = (r1.x - r0.x) / 2       # outer radius
    ref["board_x"] = b0.x                  # face of the backboard (toward the court)
    ref["board_t"] = b1.x - b0.x
    ref["board_y"] = b1.y
    ref["board_z"] = (b0.z, b1.z)

    def world_mesh(name):
        o = objs[name]
        me = o.data.copy()
        me.transform(o.matrix_world)
        return me
    ref["lines"] = world_mesh("Court_Lines")
    # the reference line mesh also carries the old centre-hexagon logo: keep only the real court markings
    HR = 11.3
    bm = bmesh.new()
    bm.from_mesh(ref["lines"])
    kill = []
    for f in bm.faces:
        c = f.calc_center_median()
        if abs(c.y) <= HR * 0.866 and 1.732 * abs(c.x) + abs(c.y) <= 1.732 * HR:
            kill.append(f)
    bmesh.ops.delete(bm, geom=kill, context="FACES")
    bm.to_mesh(ref["lines"])
    bm.free()
    ref["hex_gap"] = HR * 0.866
    ref["zone_paint"] = world_mesh("Court_Surface_Blue")
    ref["zone_three"] = world_mesh("Court_Surface_Tone")
    # these zone meshes also carry the old centre-court art (honeycomb, circle): keep only the end zones
    for key in ("zone_paint", "zone_three"):
        bm = bmesh.new()
        bm.from_mesh(ref[key])
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if abs(f.calc_center_median().x) < 20.0], context="FACES")
        bm.to_mesh(ref[key])
        bm.free()
    xs = [v.co.x for v in ref["lines"].vertices if abs(v.co.x) < 1.0 and 11.0 < abs(v.co.y) < 30.0]
    ref["centerline_half"] = max(abs(min(xs)), abs(max(xs)))
    ref["three_apex"] = min(v.co.x for v in ref["zone_three"].vertices if v.co.x > 0)
    ref["anchors"] = [(o.name, o.matrix_world.translation.copy()) for o in objs.values()
                      if o.type == "EMPTY" and (o.name.startswith("RimAnchor") or o.name.startswith("PhysicsNode"))]
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        if c.users == 0:
            bpy.data.collections.remove(c)
    print("== reference:", {k: v for k, v in ref.items() if not hasattr(v, "vertices") and k != "anchors"})
    return ref


def mesh_object(name, me, m, coll, parent, z, xmin=None, shift=0.0, extra=()):
    """Use a reference mesh (exact geometry), optionally clipped at x >= xmin, lifted to z, recoloured."""
    me = me.copy()
    me.name = name
    bm = bmesh.new()
    bm.from_mesh(me)
    if xmin is not None:
        bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(xmin, 0, 0),
                               plane_no=(1, 0, 0), clear_inner=True)
    for (x0, y0, x1, y1) in extra:
        vs = [bm.verts.new((x0, y0, 0)), bm.verts.new((x1, y0, 0)), bm.verts.new((x1, y1, 0)), bm.verts.new((x0, y1, 0))]
        bm.faces.new(vs)
    for v in bm.verts:
        v.co.z = z
        v.co.x += shift
    bm.to_mesh(me)
    bm.free()
    me.materials.clear()
    me.materials.append(mat(m))
    for p in me.polygons:
        p.material_index = 0
    uv = me.uv_layers.active or me.uv_layers.new(name="UVMap")
    data = []
    for poly in me.polygons:
        for li in poly.loop_indices:
            co = me.vertices[me.loops[li].vertex_index].co
            data += (co.x / UV_TILE, co.y / UV_TILE)
    uv.data.foreach_set("uv", data)
    c = sum((v.co for v in me.vertices), Vector()) / max(1, len(me.vertices))
    me.transform(Matrix.Translation(-c))
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    ob.parent = parent
    ob.location = c - wpos(parent)
    return ob


# ---------------------------------------------------------------------------------------------- graphics helpers
def hexagon(r, rot=0.0):
    return [(r * math.cos(rot + i * math.pi / 3), r * math.sin(rot + i * math.pi / 3)) for i in range(6)]


def hex_band(mb, cx, cy, r0, r1, z, m):
    a, b = hexagon(r0), hexagon(r1)
    for i in range(6):
        j = (i + 1) % 6
        mb.face([(cx + a[i][0], cy + a[i][1], z), (cx + b[i][0], cy + b[i][1], z),
                 (cx + b[j][0], cy + b[j][1], z), (cx + a[j][0], cy + a[j][1], z)], m)


def _cubic(p0, p1, p2, p3, n):
    out = []
    for i in range(n):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def petal_outline(n=10):
    """One stylised sakura petal (unit blossom radius, pointing +y): smooth flanks, notched tip. CCW."""
    right = _cubic((0, 0.07), (0.40, 0.22), (0.60, 0.74), (0.24, 0.99), n) + \
        _cubic((0.24, 0.99), (0.14, 1.04), (0.05, 0.96), (0, 0.85), n) + [(0, 0.85)]
    left = [(-x, y) for x, y in reversed(right[1:-1])]
    return right + left  # up the +x flank, notch, down the -x flank = CCW


PETAL_PTS = petal_outline()


def petal_poly(R, k, scale=1.0, pivot=0.57):
    """Petal k (0..4) of a blossom of radius R, optionally shrunk about its own centre."""
    a = math.pi / 2 - k * 2 * math.pi / 5 - math.pi / 2
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for x, y in PETAL_PTS:
        x, y = x * scale, pivot + (y - pivot) * scale
        out.append((R * (x * ca - y * sa), R * (x * sa + y * ca)))
    return out


def blossom(mb, cx, cy, R, z, keyline=True, accent=True, hub=True, dz=0.004):
    """Clean geometric sakura emblem: white keyline, sakura petals, bright inner accent, round centre."""
    if keyline:
        mb.face([(cx + R * 0.5 * math.cos(2 * math.pi * i / 40), cy + R * 0.5 * math.sin(2 * math.pi * i / 40), z)
                 for i in range(40)], "Line_WarmWhite")
    for k in range(5):
        if keyline:
            mb.face([(cx + x, cy + y, z + dz * 0.15 * (k + 1)) for x, y in petal_poly(R, k, 1.0)], "Line_WarmWhite")
        mb.face([(cx + x, cy + y, z + dz * (1.2 + 0.1 * k)) for x, y in petal_poly(R, k, 0.86 if keyline else 1.0)],
                "Sakura")
        if accent:
            mb.face([(cx + x, cy + y, z + 2 * dz) for x, y in petal_poly(R, k, 0.36, pivot=0.42)], "Sakura_Bright")
    if hub:  # round flower centre (no hexagon)
        mb.face([(cx + R * 0.2 * math.cos(2 * math.pi * i / 32), cy + R * 0.2 * math.sin(2 * math.pi * i / 32),
                  z + 3 * dz) for i in range(32)], "Line_WarmWhite")
        mb.face([(cx + R * 0.1 * math.cos(2 * math.pi * i / 24), cy + R * 0.1 * math.sin(2 * math.pi * i / 24),
                  z + 4 * dz) for i in range(24)], "Sakura_Bright")


def blossom_vertical(mb, M, R):
    """Small blossom on a vertical face: M maps local (x,y,0) into the face plane (local +z = outward)."""
    with mb.xf(M):
        blossom(mb, 0, 0, R, 0.0, keyline=True, accent=False, hub=True, dz=0.004)


def rect_ring(mb, x0, y0, x1, y1, w, z, m):
    mb.box(x0, y0, z, x1, y0 + w, z + 0.001, m, skip=("-z",))
    mb.box(x0, y1 - w, z, x1, y1, z + 0.001, m, skip=("-z",))
    mb.box(x0, y0 + w, z, x0 + w, y1 - w, z + 0.001, m, skip=("-z",))
    mb.box(x1 - w, y0 + w, z, x1, y1 - w, z + 0.001, m, skip=("-z",))


# ---------------------------------------------------------------------------------------------- hoop
NET_NODES = {}


def build_hoop(ref, coll, parent, tag, side=1, shift=0.0):
    """Premium outdoor hoop (same layout/proportions as before), built for the +x end and rotated for B.
    Smooth manufactured look: one continuous rounded pole+gooseneck, round tubing, rounded frame/pads,
    weighted normals. Rim stays orange; no text, small sakura emblem on the pads."""
    R = Matrix.Rotation(math.pi if side < 0 else 0.0, 4, "Z") @ Matrix.Translation((shift, 0, 0))
    rx, ry = ref["rim_c"]
    rz_top = ref["rim_top"]
    bx, bt, by = ref["board_x"], ref["board_t"], ref["board_y"]
    bz0, bz1 = ref["board_z"]
    tube = 0.055
    ringR = ref["rim_R"] - tube
    rz = rz_top - tube
    zmid = (bz0 + bz1) / 2
    root = empty(tag, coll, parent, loc=tuple(R @ Vector((bx + 4, 0, 0))))
    parts = {}

    def mbp(key):
        if key not in parts:
            parts[key] = MB()
            parts[key].M = R.copy()
        return parts[key]

    # ---- backboard: clear glass inside a rounded aluminium frame + thin target square
    g = mbp("Backboard_Glass")
    g.box(bx, -by, bz0, bx + bt, by, bz1, "Glass")
    f = mbp("Backboard_Frame")
    xc = bx + bt / 2
    fw_, fd_ = 0.17, 0.15
    f.box(xc - fd_, -by - fw_ / 2, bz1 - fw_ / 2, xc + fd_, by + fw_ / 2, bz1 + fw_ / 2, "Aluminum")
    f.box(xc - fd_, -by - fw_ / 2, bz0 - fw_ / 2, xc + fd_, by + fw_ / 2, bz0 + fw_ / 2, "Aluminum")
    for yy in (-by, by):
        f.box(xc - fd_, yy - fw_ / 2, bz0 + fw_ / 2, xc + fd_, yy + fw_ / 2, bz1 - fw_ / 2, "Aluminum")
    tw, th, lw = 1.09, 1.64, 0.075
    tz0 = rz_top + 0.12
    for (y0, z0, y1, z1) in ((-tw, tz0, tw, tz0 + lw), (-tw, tz0 + th - lw, tw, tz0 + th),
                             (-tw, tz0, -tw + lw, tz0 + th), (tw - lw, tz0, tw, tz0 + th)):
        f.box(bx - 0.008, y0, z0, bx - 0.001, y1, z1, "Line_WarmWhite")
    # bottom edge pad (rounded rubber) with a sakura accent line and a small blossom
    pad = mbp("Backboard_Pad")
    pad.sweep([(xc, -by + 0.05, bz0 - 0.08), (xc, by - 0.05, bz0 - 0.08)], rrect(0.22, 0.40, 0.08, 3), "Rubber",
              side=(0, 0, 1))
    fx = xc - 0.20 - 0.004

    # ---- clean symmetric support frame behind the glass (round tubing)
    s = mbp("Backboard_Support")
    xs = xc + 0.15 + 0.16
    tr = circle(0.07, 10)
    sy_, z0_, z1_ = 2.25, bz0 + 0.38, bz1 - 0.38
    for zz in (z0_, z1_):
        s.sweep([(xs, -sy_ - 0.07, zz), (xs, sy_ + 0.07, zz)], tr, "Steel_Graphite", side=(0, 0, 1))
    for yy in (-sy_, sy_):
        s.sweep([(xs, yy, z0_ - 0.07), (xs, yy, z1_ + 0.07)], tr, "Steel_Graphite", side=(1, 0, 0))
    for yv in (-0.8, 0.8):
        s.sweep([(xs, yv, z0_), (xs, yv, z1_)], tr, "Steel_Graphite", side=(1, 0, 0))
    s.sweep([(xs, -sy_, zmid), (xs, sy_, zmid)], tr, "Steel_Graphite", side=(0, 0, 1))
    for (yv, zv) in ((-sy_, z0_ + 0.5), (sy_, z0_ + 0.5), (-sy_, z1_ - 0.5), (sy_, z1_ - 0.5), (-0.8, zmid),
                     (0.8, zmid)):
        s.beam((xc + 0.14, yv, zv), (xs, yv, zv), 0.09, 0.09, "Aluminum", round_n=8)
    # mounting plate where the arm meets the frame
    zg = zmid + 0.45
    plate_x = xs + 0.09
    s.sweep([(plate_x, 0, zg), (plate_x + 0.12, 0, zg)], rrect(1.3, 1.1, 0.18, 3), "Steel_Dark", side=(0, 1, 0))
    for (yy, zz) in ((-0.45, zg - 0.38), (0.45, zg - 0.38), (-0.45, zg + 0.38), (0.45, zg + 0.38)):
        s.beam((plate_x + 0.12, yy, zz), (plate_x + 0.17, yy, zz), 0.08, 0.08, "Bolt", round_n=6)
    for sy in (-1, 1):
        s.sweep([(plate_x + 0.06, sy * 0.45, zg - 0.45), (xs, sy * (sy_ - 0.2), z0_ + 0.05)], circle(0.06, 8),
                "Steel_Graphite")

    # ---- rim (orange), breakaway mount, hooks, rear brace
    r = mbp("Rim")
    r.torus(rx, ry, rz, ringR, tube, 48, 10, "Rim_Orange")
    for k in range(10):
        a = 2 * math.pi * k / 10
        r.torus(rx + ringR * math.cos(a), ry + ringR * math.sin(a), rz - 0.075, 0.045, 0.012, 8, 4, "Rim_Orange")
    r.torus(rx, ry, rz - 0.13, ringR - 0.02, 0.03, 18, 6, "Rim_Orange", a0=-math.radians(55), a1=math.radians(55))
    for sy in (-1, 1):
        a = math.radians(55) * sy
        r.beam((rx + (ringR - 0.02) * math.cos(a), (ringR - 0.02) * math.sin(a), rz - 0.13),
               (rx + ringR * math.cos(a), ringR * math.sin(a), rz - 0.02), 0.05, 0.05, "Rim_Orange", round_n=8)
    mnt = mbp("Rim_Mount")
    mnt.sweep([(bx - 0.065, 0, rz_top - 0.15), (bx - 0.005, 0, rz_top - 0.15)], rrect(0.78, 0.56, 0.1, 3),
              "Rim_Orange", side=(0, 1, 0))
    mnt.sweep([(bx - 0.36, 0, rz_top - 0.22), (bx - 0.06, 0, rz_top - 0.22)], rrect(0.4, 0.32, 0.08, 3),
              "Rim_Orange", side=(0, 1, 0))
    for sy in (-1, 1):
        ang = math.asin(0.82 / ringR)
        px_, py_ = rx + ringR * math.cos(ang), sy * ringR * math.sin(ang)
        mnt.beam((bx - 0.05, sy * 0.3, rz_top - 0.36), (px_, py_, rz - 0.01), 0.07, 0.07, "Rim_Orange", round_n=8)
    for sy in (-0.27, 0.27):
        for zz in (rz_top - 0.34, rz_top + 0.04):
            mnt.beam((bx - 0.075, sy, zz), (bx - 0.065, sy, zz), 0.06, 0.06, "Bolt", round_n=6)

    # ---- net: separate cords like the reference file (NetCord_<X>_L<level>_<i>_<j>), tighter waist
    letter = tag.split("_")[1] if "_" in tag else ""
    pre = "NetCord_%s_" % letter if letter else "NetCord_"
    levels = [(ringR - 0.02, rz - 0.06), (1.02, rz - 0.55), (0.82, rz - 1.06), (0.58, rz - 1.58)]  # tight bottom
    nodes = []
    for L, (rr, zz) in enumerate(levels):
        off = 0.5 if L % 2 else 0.0
        nodes.append([(rx + rr * math.cos(2 * math.pi * (k + off) / 10), ry + rr * math.sin(2 * math.pi * (k + off) / 10),
                       zz) for k in range(10)])
    net_root = empty("%s_Net" % tag, coll, root, loc=tuple(R @ Vector((rx, ry, rz))))
    cords = []
    for L in range(len(levels) - 1):
        for i in range(10):
            for j in ((i, (i - 1) % 10) if L % 2 == 0 else (i, (i + 1) % 10)):
                c = MB()
                c.M = R.copy()
                c.sweep([nodes[L][i], nodes[L + 1][j]], circle(0.028, 6), "Net_White")
                cords.append((c, "%sL%d_%02d_%02d" % (pre, L, i, j)))
    for c, nm in cords:
        c.build(nm, coll, net_root, smooth=True)
    NET_NODES[tag] = [[tuple(R @ Vector(q)) for q in lvl] for lvl in nodes]

    # ---- one continuous fabricated pole + gooseneck (rounded square tube, smooth bend into the board)
    p = mbp("Support")
    px = ref["surf"][0] + 3.9
    prof = rrect(0.84, 0.84, 0.2, 3)
    P = [Vector((px, 0, bz1 - 1.9)), Vector((px, 0, bz1 + 0.45)), Vector((plate_x + 3.3, 0, zg)),
         Vector((plate_x + 0.12, 0, zg))]

    def bez(t):
        u = 1 - t
        return u ** 3 * P[0] + 3 * u * u * t * P[1] + 3 * u * t * t * P[2] + t ** 3 * P[3]
    path = [Vector((px, 0, 0.12)), Vector((px, 0, 2.0)), Vector((px, 0, 5.0))] + [bez(i / 18) for i in range(19)]
    p.sweep(path, prof, "Steel_Graphite", side=(0, 1, 0))
    # collars (slightly larger rounded rings)
    for zc in (bz0 - 1.4, 3.2):
        p.sweep([(px, 0, zc - 0.16), (px, 0, zc + 0.16)], rrect(0.98, 0.98, 0.26, 3), "Steel_Dark", side=(0, 1, 0))
    # lower brace (round tube) + gas strut
    b0 = Vector((px - 0.42, 0, bz0 - 1.4))
    b1 = Vector((xs, 0, z0_))
    p.sweep([b0, b0.lerp(b1, 0.5), b1], circle(0.16, 14), "Steel_Graphite")
    for e_, d_ in ((b0, 1), (b1, -1)):
        dirv = (b1 - b0).normalized() * d_
        p.sweep([e_, e_ + dirv * 0.35], circle(0.21, 14), "Steel_Dark")
    g0 = Vector((px - 0.42, 0, 5.0))
    g1 = b0.lerp(b1, 0.55)
    p.sweep([g0, g0.lerp(g1, 0.55)], circle(0.11, 12), "Steel_Dark")
    p.sweep([g0.lerp(g1, 0.5), g1], circle(0.065, 10), "Aluminum")
    # height-adjust crank housing (rounded) + handle
    p.sweep([(px + 0.42, 0, 4.4), (px + 0.66, 0, 4.4)], rrect(0.42, 0.6, 0.12, 3), "Steel_Dark", side=(0, 1, 0))
    p.sweep([(px + 0.66, 0, 4.4), (px + 0.9, 0, 4.4)], circle(0.05, 8), "Bolt")
    # base: rounded plate, hex nuts on anchor bolts, gussets, hexagonal cover
    p.sweep([(px, 0, 0.0), (px, 0, 0.12)], rrect(1.9, 1.9, 0.22, 3), "Steel_Dark", side=(0, 1, 0))
    for sx in (-0.72, 0.72):
        for sy in (-0.72, 0.72):
            p.cyl(px + sx, sy, 0.06, 0.12, 0.34, 8, "Bolt")
            p.cyl(px + sx, sy, 0.12, 0.12, 0.22, 6, "Steel_Graphite")
    for ang in range(4):
        a = ang * math.pi / 2
        dx, dy = math.cos(a), math.sin(a)
        g0_ = (px + dx * 0.42, dy * 0.42)
        ox, oy = -dy * 0.04, dx * 0.04
        p.face([(g0_[0] + ox, g0_[1] + oy, 0.12), (g0_[0] + dx * 0.55 + ox, g0_[1] + dy * 0.55 + oy, 0.12),
                (g0_[0] + ox, g0_[1] + oy, 0.95)], "Steel_Dark")
        p.face([(g0_[0] - ox, g0_[1] - oy, 0.95), (g0_[0] + dx * 0.55 - ox, g0_[1] + dy * 0.55 - oy, 0.12),
                (g0_[0] - ox, g0_[1] - oy, 0.12)], "Steel_Dark")
        p.face([(g0_[0] + dx * 0.55 + ox, g0_[1] + dy * 0.55 + oy, 0.12),
                (g0_[0] + dx * 0.55 - ox, g0_[1] + dy * 0.55 - oy, 0.12),
                (g0_[0] - ox, g0_[1] - oy, 0.95), (g0_[0] + ox, g0_[1] + oy, 0.95)], "Steel_Dark")
    p.prism([(px + 1.3 * math.cos(i * math.pi / 3), 1.3 * math.sin(i * math.pi / 3)) for i in range(6)],
            0.02, 0.05, "Steel_Graphite")

    # ---- pole padding: soft rounded block, rounded top/bottom, sakura band + small blossom
    pd = mbp("Padding")
    pp = rrect(1.3, 1.3, 0.34, 4)
    zs = [0.3, 0.36, 0.44, 6.06, 6.14, 6.2]
    sc = [0.9, 0.97, 1.0, 1.0, 0.97, 0.9]
    pd.sweep([(px, 0, z) for z in zs], pp, "Pad_Navy", side=(0, 1, 0), scales=sc)

    smooth_keys = {"Backboard_Frame", "Backboard_Pad", "Backboard_Support", "Rim", "Rim_Mount", "Support", "Padding"}
    for key, mb in parts.items():
        mb.build("%s_%s" % (tag, key), coll, root, smooth=key in smooth_keys)
    return root


# ---------------------------------------------------------------------------------------------- courts
def build_court(ref, name, half=False):
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    SX, SY = ref["surf"]
    AX, AY = ref["apron"]
    slab = ref["slab"]
    cl = ref["centerline_half"]
    if half:
        x0s = -cl - (SX - max(v.co.x for v in ref["lines"].vertices))  # same margin as behind the baseline
        x1s = SX
        x0a, x1a = x0s - (AX - SX), AX
        shift = -(x0a + x1a) / 2
    else:
        x0s, x1s, x0a, x1a, shift = -SX, SX, -AX, AX, 0.0
    root = empty(name, coll)
    # ---------------- FLOOR
    floor = empty("Floor", coll, root)
    mb = MB()
    mb.box(x0s + shift, -SY, -slab, x1s + shift, SY, 0.0, "Court_Graphite")
    mb.build("Floor_Surface", coll, floor, uv_tile=UV_TILE)
    xmin = (-cl - 0.001) if half else None
    mesh_object("Floor_ThreePoint", ref["zone_three"], "Court_Graphite3", coll, floor, 0.010, xmin, shift)
    mesh_object("Floor_Paint", ref["zone_paint"], "Court_Navy", coll, floor, 0.020, xmin, shift)
    # ---------------- LINES (exact reference geometry)
    gap = ref["hex_gap"] + 0.4
    mesh_object("Lines", ref["lines"], "Line_WarmWhite", coll, root, 0.045, xmin, shift,
                extra=[(-cl, -gap, cl, gap)] if half else ())
    # ---------------- GRAPHICS: one clean sakura emblem + one sakura perimeter accent. No text, no extras.
    gfx = empty("Graphics", coll, root)
    g = MB()
    if not half:
        cx, cy, k = 0.0, 0.0, 1.0
    else:
        lo, hi = 6.0 + 1.0, ref["three_apex"] - 1.0
        k = min(0.75, (hi - lo) / 2 / 10.4)
        cx, cy = (lo + hi) / 2, 0.0
    ex = cx + shift
    # navy centre disc (circle r6 like the keys' paint) with a crisp white ring
    g.prism([(ex + x, cy + y) for x, y in circle(6.0 * k, 72)], 0.022, 0.024, "Court_Navy", side=False)
    g.ring(ex, cy, 6.0 * k - 0.09, 6.0 * k + 0.09, 0.026, 72, "Line_WarmWhite")
    # the sakura blossom: the court's identity
    blossom(g, ex, cy, 8.6 * k, 0.03)
    if half:
        # 1v1 check arc at the top line (same radius as the full court's centre circle)
        g.ring(shift + 0.0, 0.0, 6.0 - 0.09, 6.0 + 0.09, 0.04, 40, "Line_WarmWhite", -math.pi / 2, math.pi / 2)
    g.build("Graphics_Emblem", coll, gfx)
    pk = MB()
    rect_ring(pk, x0s + shift - 1.2, -SY - 1.2, x1s + shift + 1.2, SY + 1.2, 0.24, 0.03, "Sakura")
    pk.build("Graphics_PerimeterAccent", coll, gfx)
    # ---------------- BORDER (apron as 4 convex slabs + metal edge lip)
    border = empty("Border", coll, root)
    for nm, r in (("Border_Apron_N", (x0a, SY, x1a, AY)), ("Border_Apron_S", (x0a, -AY, x1a, -SY)),
                  ("Border_Apron_E", (x1s, -SY, x1a, SY)), ("Border_Apron_W", (x0a, -SY, x0s, SY))):
        a = MB()
        a.box(r[0] + shift, r[1], -slab, r[2] + shift, r[3], 0.0, "Apron_Charcoal")
        a.build(nm, coll, border, uv_tile=UV_TILE)
    e = MB()
    lip = 0.35
    for r in ((x0a, AY - lip, x1a, AY), (x0a, -AY, x1a, -AY + lip), (x0a, -AY + lip, x0a + lip, AY - lip),
              (x1a - lip, -AY + lip, x1a, AY - lip)):
        e.box(r[0] + shift, r[1], -slab - 0.02, r[2] + shift, r[3], 0.06, "Edge_Metal")
    e.build("Border_Edge", coll, border)
    # ---------------- HOOPS
    if half:
        build_hoop(ref, coll, root, "Hoop", side=1, shift=shift)
    else:
        build_hoop(ref, coll, root, "Hoop_A", side=1)
        build_hoop(ref, coll, root, "Hoop_B", side=-1)
    return coll, root, shift


# ---------------------------------------------------------------------------------------------- export helpers
def all_children(o):
    return [o] + list(o.children_recursive)


def palette_bake(objs, size_cells=16, cell=32, fname="court_palette.png"):
    """EVERY material -> one palette texture (Roblox's importer drops plain material colours, and a single
    shared texture per file is the most reliable thing to import). 32px swatches so far mips don't bleed."""
    names = sorted({m.name for o in objs if o.type == "MESH" for m in o.data.materials if m})
    size = size_cells * cell
    img = bpy.data.images.new("HEX_Court_Palette", size, size, alpha=False)
    px = [0.0] * (size * size * 4)
    idx = {}
    for i, n in enumerate(names):
        h = COL[n][0].lstrip("#")
        rgb = [int(h[k:k + 2], 16) / 255 for k in (0, 2, 4)]
        c, r = i % size_cells, i // size_cells
        idx[n] = ((c + 0.5) / size_cells, (r + 0.5) / size_cells)
        for y in range(r * cell, (r + 1) * cell):
            for x in range(c * cell, (c + 1) * cell):
                o_ = (y * size + x) * 4
                px[o_:o_ + 4] = rgb + [1.0]
    img.pixels = px
    img.filepath_raw = os.path.join(TEX_DIR, fname)
    img.file_format = "PNG"
    img.save()
    pm = bpy.data.materials.new("HEX_Court_Palette")
    try:
        pm.use_nodes = True
    except Exception:
        pass
    tx = pm.node_tree.nodes.new("ShaderNodeTexImage")
    tx.image = img
    tx.interpolation = "Closest"
    pm.node_tree.links.new(tx.outputs["Color"], pm.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    for o in objs:
        if o.type != "MESH":
            continue
        me = o.data
        ms = [m.name for m in me.materials if m]
        if not ms or not all(m in idx for m in ms):
            continue
        uv = me.uv_layers.active or me.uv_layers.new(name="UVMap")
        data = [0.0] * (len(me.loops) * 2)
        for poly in me.polygons:
            u, v = idx[ms[poly.material_index]]
            for li in poly.loop_indices:
                data[li * 2], data[li * 2 + 1] = u, v
        uv.data.foreach_set("uv", data)
        me.materials.clear()
        me.materials.append(pm)
        me.polygons.foreach_set("material_index", [0] * len(me.polygons))


def to_reference_units(objs):
    """Match the reference FBX convention: every mesh object scaled x3.571 with geometry / 3.571."""
    for o in objs:
        if o.type == "MESH" and abs(o.scale.x - REF_SCALE) > 1e-4:
            o.data.transform(Matrix.Scale(1 / REF_SCALE, 4))
            o.scale = (REF_SCALE, REF_SCALE, REF_SCALE)


def export(root, fname):
    os.makedirs(OUT_DIR, exist_ok=True)
    objs = all_children(root)
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    path = os.path.join(OUT_DIR, fname)
    bpy.ops.export_scene.fbx(filepath=path, use_selection=True, object_types={"MESH", "EMPTY"},
                             apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS", axis_forward="-Z",
                             axis_up="Y", mesh_smooth_type="OFF", use_triangles=True, add_leaf_bones=False,
                             bake_anim=False, path_mode="COPY", embed_textures=True)
    print("   exported %s (%d objects, %.1f MB)" % (path, len(objs), os.path.getsize(path) / 1e6))
    return path


def export_anchors(name, tags):
    """Optional: RimAnchor / PhysicsNode empties at the new net nodes (same naming as the reference file)."""
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    root = empty(name, coll)
    for tag in tags:
        letter = tag.split("_")[1] if "_" in tag else ""
        mid = "_%s_" % letter if letter else "_"
        for L, lvl in enumerate(NET_NODES[tag]):
            for k, q in enumerate(lvl):
                nm = ("RimAnchor%sL0_%02d" % (mid, k)) if L == 0 else ("PhysicsNode%sL%d_%02d" % (mid, L, k))
                e = empty(nm, coll, root, loc=q)
                e.empty_display_size = 0.2
    return root


# ---------------------------------------------------------------------------------------------- preview
def preview(roots_info):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = int(os.environ.get('COURT_SAMPLES', '48'))
    sc.cycles.use_denoising = True
    sc.view_settings.view_transform = "AgX"
    w = bpy.data.worlds.new("w")
    sc.world = w
    try:
        w.use_nodes = True
    except Exception:
        pass
    sky = w.node_tree.nodes.new("ShaderNodeTexSky")
    for t in ("MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "NISHITA"):
        try:
            sky.sky_type = t
            break
        except Exception:
            pass
    sky.sun_disc = False
    sky.sun_elevation = math.radians(40)
    w.node_tree.links.new(sky.outputs["Color"], w.node_tree.nodes["Background"].inputs["Color"])
    w.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.16
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 4.2
    sun.data.color = (1.0, 0.95, 0.88)
    sun.data.angle = math.radians(1.5)
    sun.rotation_euler = (math.radians(48), math.radians(12), math.radians(-35))
    sc.collection.objects.link(sun)
    gp = bpy.data.meshes.new("previewGround")  # NOT exported - render backdrop only
    gp.from_pydata([(-600, -600, -0.9), (600, -600, -0.9), (600, 600, -0.9), (-600, 600, -0.9)], [], [(0, 1, 2, 3)])
    gm = bpy.data.materials.new("pg")
    gm.diffuse_color = (0.06, 0.06, 0.07, 1)
    gm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.05, 0.05, 0.055, 1)
    gp.materials.append(gm)
    go = bpy.data.objects.new("previewGround", gp)
    sc.collection.objects.link(go)
    out = os.path.join(REPO, "renders", "courts")
    os.makedirs(out, exist_ok=True)

    def shot(name, loc, tgt, lens=None, ortho=None, res=(1600, 900)):
        cd = bpy.data.cameras.new(name)
        cam = bpy.data.objects.new(name, cd)
        sc.collection.objects.link(cam)
        cam.location = loc
        cam.rotation_euler = (Vector(tgt) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        cd.clip_end = 3000
        if ortho:
            cd.type = "ORTHO"
            cd.ortho_scale = ortho
        else:
            cd.lens = lens or 28
        sc.camera = cam
        sc.render.resolution_x, sc.render.resolution_y = res
        sc.render.filepath = os.path.join(out, name + ".png")
        bpy.ops.render.render(write_still=True)
        print("   rendered", name)
    fy = roots_info["half_y"]
    shot("full_top", (0, 0, 400), (0, 0.001, 0), ortho=150, res=(1600, 980))
    shot("emblem", (0, 0, 120), (0, 0.001, 0), ortho=26, res=(1100, 1100))
    shot("full_aerial", (-62, -78, 46), (6, 0, 0), lens=24)
    shot("full_hoop", (40, -15, 9.5), (55, 0, 8.6), lens=30)
    shot("hoop_detail", (50.0, -5.2, 9.4), (54.6, 0, 9.0), lens=35)
    shot("full_player", (-20, 18, 5.5), (40, 0, 7), lens=22)
    shot("half_top", (0, fy, 400), (0, fy + 0.001, 0), ortho=95, res=(1400, 1100))
    shot("half_aerial", (-48, fy - 58, 40), (6, fy, 0), lens=24)


# ---------------------------------------------------------------------------------------------- main
def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    make_textures()
    ref = load_reference()
    full_coll, full_root, _ = build_court(ref, "HEX_Tokyo_FullCourt", half=False)
    half_coll, half_root, hshift = build_court(ref, "HEX_Tokyo_HalfCourt", half=True)
    HALF_Y = 130.0
    half_root.location.y = HALF_Y          # side by side in the .blend; exported at its own origin
    bpy.context.view_layer.update()
    path = os.path.join(HERE, "HEX_Tokyo_Courts.blend")
    for o in all_children(full_root) + all_children(half_root):
        if o.type == "MESH":
            o.data.update()
    tris = {r.name: sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in all_children(r) if o.type == "MESH")
            for r in (full_root, half_root)}
    print("== triangles:", tris)
    if DO_RENDER:
        preview({"half_y": HALF_Y})
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    print("== saved", path)
    if DO_EXPORT:
        half_root.location.y = 0.0
        bpy.context.view_layer.update()
        a_full = export_anchors("HEX_Tokyo_FullCourt_NetAnchors", ("Hoop_A", "Hoop_B"))
        a_half = export_anchors("HEX_Tokyo_HalfCourt_NetAnchors", ("Hoop",))
        objs = all_children(full_root) + all_children(half_root)
        palette_bake(objs)
        to_reference_units(objs)
        export(full_root, "HEX_Tokyo_FullCourt.fbx")
        # both courts live in one .blend, so the half court's names carry ".001": free them up for export
        for o in all_children(full_root):
            o.name = o.name + "__fullcourt"
        for o in all_children(half_root):
            if "." in o.name:
                o.name = o.name.rsplit(".", 1)[0]
        export(half_root, "HEX_Tokyo_HalfCourt.fbx")
        export(a_full, "optional_HEX_Tokyo_FullCourt_NetAnchors.fbx")
        export(a_half, "optional_HEX_Tokyo_HalfCourt_NetAnchors.fbx")


if __name__ == "__main__":
    main()
