"""
HEX!  -  City Basketball Park  -  ENVIRONMENT SHELL generator for Blender (5.x / 4.2+)

Builds a huge, mostly-empty, flat basketball plaza sunk into a dense Tokyo/Shibuya-inspired
commercial district, plus one sunken practice area reached by wide stairs.

UNITS:  1 Blender unit == 1 Roblox stud.   (Avatar ~5 studs tall, doors 8.5, floors 12, risers 1.)

Run headless (from the repo root):
    blender --background --python blender/build_hex_city.py -- --export --render
or open Blender > Scripting > open this file > Run Script.

Optional flags after "--":
    --export        write per-collection FBX files to  export/fbx/
    --render        write preview renders to          renders/
    --views a,b     only render these views
    --samples N     Cycles samples for previews (default 40)
    --no-save       don't write blender/HEX_City_Map.blend

YOUR COURT:  drop the HEX court FBX into  court/  (or set env HEX_COURT_FBX=path).  The script
imports it FIRST, measures it, and grows the layout if the court is bigger than the default
budget. The court itself is never resized and never exported with the map.
"""

import bpy
import bmesh
import math
import os
import sys
import glob
import json
import random
import time
from types import SimpleNamespace
from contextlib import contextmanager
from mathutils import Vector, Matrix

# ----------------------------------------------------------------------------------------------
# paths / args
# ----------------------------------------------------------------------------------------------


def _script_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


SCRIPT_DIR = _script_dir()
REPO_DIR = os.path.dirname(SCRIPT_DIR) if os.path.basename(SCRIPT_DIR) == "blender" else SCRIPT_DIR
ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _arg_value(name, default=None):
    for i, a in enumerate(ARGV):
        if a == name and i + 1 < len(ARGV):
            return ARGV[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


DO_EXPORT = "--export" in ARGV
DO_RENDER = "--render" in ARGV
DO_SAVE = "--no-save" not in ARGV
RENDER_VIEWS = _arg_value("--views")
RENDER_SAMPLES = int(_arg_value("--samples", "40"))
RENDER_RES = tuple(int(v) for v in _arg_value("--res", "1600x900").split("x"))

SEED = 2077

# ----------------------------------------------------------------------------------------------
# scale constants (studs)
# ----------------------------------------------------------------------------------------------
Z_STREET = -0.6          # road surface (plaza / sidewalks are at 0)
Z0 = Z_STREET            # building base
GF_H = 15.0              # ground-floor (storefront) height
SF_TOP = GF_H - 4.6      # top of shopfront glass (fascia / sign band above)
FL_H = 12.0              # typical upper floor height
DOOR_H = 7.5
DOOR_W = 4.5
RAIL_H = 2.7             # railing height above its base (coping 0.6 below -> ~3.3 above the deck)
PIT_D = 16.0             # practice-area depth (one storey)
WALL_T = 3.0             # retaining-wall thickness
ST = 44.0                # perimeter street width
SW = 18.0                # sidewalk width
MG_OFF = 110.0           # midground ring offset from the foreground frontage line
FG_MAX_D = 88.0          # max foreground building depth

DEFAULT_COURT_L = 150.0  # used when no court FBX is found (deliberately generous)
DEFAULT_COURT_W = 85.0

# ----------------------------------------------------------------------------------------------
# small utils
# ----------------------------------------------------------------------------------------------


def ceil_to(v, step):
    return math.ceil(v / step) * step


def srgb_to_lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hexcol(h, a=1.0):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (srgb_to_lin(r), srgb_to_lin(g), srgb_to_lin(b), a)


def rect_sub(a, b):
    """a minus b, both (x0,y0,x1,y1). Returns list of non-overlapping rects."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if bx0 >= ax1 or bx1 <= ax0 or by0 >= ay1 or by1 <= ay0:
        return [a]
    out = []
    if by0 > ay0:
        out.append((ax0, ay0, ax1, by0))
    if by1 < ay1:
        out.append((ax0, by1, ax1, ay1))
    cy0, cy1 = max(ay0, by0), min(ay1, by1)
    if bx0 > ax0:
        out.append((ax0, cy0, bx0, cy1))
    if bx1 < ax1:
        out.append((bx1, cy0, ax1, cy1))
    return [r for r in out if r[2] - r[0] > 1e-4 and r[3] - r[1] > 1e-4]


class Painter:
    """Paints tagged rectangles; later paint wins. tag None == hole."""

    def __init__(self):
        self.pieces = []

    def paint(self, rect, tag):
        nxt = []
        for r, t in self.pieces:
            for q in rect_sub(r, rect):
                nxt.append((q, t))
        if tag is not None:
            nxt.append((rect, tag))
        self.pieces = nxt


def frame_matrix(origin, ax, ay):
    """Local->world matrix. ax/ay are 2D unit vectors for local x/y; z stays up."""
    oz = origin[2] if len(origin) > 2 else 0.0
    return Matrix(((ax[0], ay[0], 0.0, origin[0]),
                   (ax[1], ay[1], 0.0, origin[1]),
                   (0.0, 0.0, 1.0, oz),
                   (0.0, 0.0, 0.0, 1.0)))


def rot_z(a):
    return Matrix.Rotation(a, 4, "Z")


def trans(x, y, z=0.0):
    return Matrix.Translation((x, y, z))


# ----------------------------------------------------------------------------------------------
# materials
# ----------------------------------------------------------------------------------------------
# name: (hex, roughness, metallic, emission_hex or None, emission_strength, noise_amount)
PALETTE = {
    # ground
    "Ground_Concrete":      ("#7f7c77", 0.92, 0.0, None, 0, 0.10),
    "Ground_ConcreteLight": ("#928f88", 0.90, 0.0, None, 0, 0.10),
    "Ground_Asphalt":       ("#3b3c3f", 0.95, 0.0, None, 0, 0.12),
    "Ground_Street":        ("#2b2c2f", 0.95, 0.0, None, 0, 0.12),
    "Ground_PaverDark":     ("#4f4c49", 0.90, 0.0, None, 0, 0.12),
    "Ground_PaverBrick":    ("#77544a", 0.90, 0.0, None, 0, 0.12),
    "Ground_Sidewalk":      ("#6c6964", 0.90, 0.0, None, 0, 0.10),
    "Practice_Floor":       ("#6b7077", 0.85, 0.0, None, 0, 0.08),
    "Practice_FloorBorder": ("#55585d", 0.88, 0.0, None, 0, 0.08),
    "Curb_Granite":         ("#9c9994", 0.80, 0.0, None, 0, 0.08),
    "Paint_White":          ("#d9d9d3", 0.70, 0.0, None, 0, 0.0),
    "Grate_Metal":          ("#25272a", 0.55, 0.7, None, 0, 0.0),
    "Joint_Dark":           ("#55534f", 0.95, 0.0, None, 0, 0.0),
    # walls / architecture
    "Concrete_Wall":        ("#8a8782", 0.90, 0.0, None, 0, 0.08),
    "Concrete_Dark":        ("#5c5a57", 0.90, 0.0, None, 0, 0.08),
    "Concrete_Light":       ("#b8b4ab", 0.88, 0.0, None, 0, 0.06),
    "OffWhite":             ("#d5d1c6", 0.85, 0.0, None, 0, 0.05),
    "Beige_Tile":           ("#b9a98e", 0.80, 0.0, None, 0, 0.06),
    "Tile_Gray":            ("#9a9893", 0.75, 0.0, None, 0, 0.06),
    "Stucco_Old":           ("#a69d8c", 0.92, 0.0, None, 0, 0.10),
    "Brick":                ("#7a4b3c", 0.90, 0.0, None, 0, 0.10),
    "Brick_Dark":           ("#583a31", 0.90, 0.0, None, 0, 0.10),
    "Charcoal":             ("#38393b", 0.80, 0.0, None, 0, 0.05),
    "FadedBlack":           ("#212224", 0.75, 0.0, None, 0, 0.04),
    "DarkMetal":            ("#2e3135", 0.45, 0.75, None, 0, 0.0),
    "Metal_Panel":          ("#6d7176", 0.50, 0.55, None, 0, 0.03),
    "Aluminum":             ("#a3a8ad", 0.35, 0.85, None, 0, 0.0),
    "Metal_Rail":           ("#3a3d41", 0.45, 0.70, None, 0, 0.0),
    "AC_Unit":              ("#c6c6c0", 0.60, 0.20, None, 0, 0.0),
    "Roof_Gray":            ("#6c6b68", 0.95, 0.0, None, 0, 0.10),
    "Roof_Dark":            ("#4a4b4c", 0.95, 0.0, None, 0, 0.10),
    "Glass_Dark":           ("#1c2530", 0.06, 0.45, None, 0, 0.0),
    "Glass_Blue":           ("#2a3e53", 0.05, 0.50, None, 0, 0.0),
    "Glass_Silver":         ("#66737e", 0.08, 0.70, None, 0, 0.0),
    "Glass_Store":          ("#121417", 0.04, 0.0, "#ffe2b8", 0.12, 0.0),
    "Door_Dark":            ("#17181a", 0.40, 0.30, None, 0, 0.0),
    "Awning_Red":           ("#7a2b27", 0.85, 0.0, None, 0, 0.0),
    "Awning_Green":         ("#2f4a3b", 0.85, 0.0, None, 0, 0.0),
    "Awning_Navy":          ("#25314a", 0.85, 0.0, None, 0, 0.0),
    "Awning_Beige":         ("#a89a7c", 0.85, 0.0, None, 0, 0.0),
    "Sign_Cream":           ("#e6dcc4", 0.60, 0.0, None, 0, 0.0),
    "Sign_Red":             ("#9b2a25", 0.60, 0.0, None, 0, 0.0),
    "Sign_Navy":            ("#1f2c4d", 0.60, 0.0, None, 0, 0.0),
    "Sign_Black":           ("#18191b", 0.60, 0.0, None, 0, 0.0),
    # HEX accents + light
    "HEX_Navy":             ("#1b2a4a", 0.70, 0.10, None, 0, 0.0),
    "HEX_Orange_LED":       ("#ff7a1a", 0.40, 0.0, "#ff7a1a", 6.0, 0.0),
    "LED_Cyan":             ("#2de2ff", 0.40, 0.0, "#2de2ff", 6.0, 0.0),
    "LED_Magenta":          ("#ff2fa8", 0.40, 0.0, "#ff2fa8", 6.0, 0.0),
    "LED_Purple":           ("#8a4dff", 0.40, 0.0, "#8a4dff", 6.0, 0.0),
    "LED_White":            ("#ffffff", 0.40, 0.0, "#f4f8ff", 5.0, 0.0),
    "LED_Red":              ("#ff2a2a", 0.40, 0.0, "#ff2a2a", 10.0, 0.0),
    "Lamp_Warm":            ("#fff1d6", 0.40, 0.0, "#fff1d6", 12.0, 0.0),
    # background skyline (lighter / bluer = atmospheric depth)
    "Skyline_A":            ("#7f8995", 0.70, 0.10, None, 0, 0.0),
    "Skyline_B":            ("#6c7683", 0.60, 0.15, None, 0, 0.0),
    "Skyline_C":            ("#8f959d", 0.75, 0.05, None, 0, 0.0),
    "Skyline_Glass":        ("#5d7085", 0.20, 0.45, None, 0, 0.0),
    "Skyline_Band":         ("#5d6875", 0.60, 0.20, None, 0, 0.0),
    # Tokyo kit
    "Tile_White":           ("#dcd8ce", 0.70, 0.0, None, 0, 0.05),
    "Tile_Brown":           ("#8a6a55", 0.75, 0.0, None, 0, 0.06),
    "Concrete_Exposed":     ("#8f8c86", 0.90, 0.0, None, 0, 0.10),
    "Soffit":               ("#bdb8ad", 0.80, 0.0, None, 0, 0.0),
    "Downlight":            ("#fff3dd", 0.40, 0.0, "#fff3dd", 8.0, 0.0),
    "Door_Glass":           ("#262b31", 0.08, 0.40, None, 0, 0.0),
    "Glass_Konbini":        ("#e8eeee", 0.10, 0.0, "#f2fbff", 1.6, 0.0),
    "Interior_Lit":         ("#2a2230", 0.50, 0.0, "#ff9ad0", 0.45, 0.0),
    "Wood_Dark":            ("#4b3426", 0.80, 0.0, None, 0, 0.06),
    "Noren_Navy":           ("#1f2b48", 0.90, 0.0, None, 0, 0.0),
    "Noren_Red":            ("#8e1f1f", 0.90, 0.0, None, 0, 0.0),
    "Noren_White":          ("#e4ddcc", 0.90, 0.0, None, 0, 0.0),
    "Lantern_Red":          ("#d8341f", 0.50, 0.0, "#ff5a2a", 2.5, 0.0),
    "Shutter":              ("#8e9297", 0.50, 0.50, None, 0, 0.0),
    "Pipe_Gray":            ("#7a7d80", 0.55, 0.40, None, 0, 0.0),
    "Balcony_Panel":        ("#b9c0c6", 0.35, 0.10, None, 0, 0.0),
    "Stripe_A":             ("#2b9b8f", 0.60, 0.0, None, 0, 0.0),
    "Stripe_B":             ("#f2c230", 0.60, 0.0, None, 0, 0.0),
    "Stripe_C":             ("#7d3cff", 0.60, 0.0, None, 0, 0.0),
    "Stripe_D":             ("#e94f37", 0.60, 0.0, None, 0, 0.0),
    "Vending_Body":         ("#ebebe8", 0.50, 0.10, None, 0, 0.0),
    "Vending_RedBody":      ("#b3202a", 0.50, 0.10, None, 0, 0.0),
    "Vending_Panel":        ("#dff3ff", 0.30, 0.0, "#dff3ff", 2.2, 0.0),
    "Pole_Concrete":        ("#9a978f", 0.90, 0.0, None, 0, 0.0),
    "Cable":                ("#151515", 0.60, 0.0, None, 0, 0.0),
    "Bulb_Strip":           ("#ffd27a", 0.40, 0.0, "#ffd27a", 6.0, 0.0),
    # reference only
    "Ref_Avatar":           ("#e8e2d0", 0.60, 0.0, None, 0, 0.0),
    "Ref_CourtGhost":       ("#ff7a1a", 0.60, 0.0, "#ff7a1a", 1.5, 0.0),
    "Ref_HalfCourtGhost":   ("#2de2ff", 0.60, 0.0, "#2de2ff", 1.5, 0.0),
}

SCREEN_PALETTES = [
    ("#ff2d95", "#7b2cff", "#1fd1ff"),
    ("#ff7a00", "#ffd23f", "#ff3b3b"),
    ("#00e5ff", "#0047ff", "#e8f6ff"),
    ("#ff3b3b", "#151515", "#f2f2f2"),
    ("#8a2be2", "#ff4fd8", "#ffe14d"),
    ("#00ffa3", "#00a3ff", "#2a1aff"),
    ("#ff6b00", "#1b2a4a", "#ffffff"),
    ("#ffd1dc", "#ff4f9a", "#5a2bff"),
    ("#2bff88", "#f4fff8", "#ff2bd6"),
    ("#3a7bff", "#00e1ff", "#ff8a00"),
]

_MATS = {}


def _principled(m):
    nt = m.node_tree
    return nt.nodes.get("Principled BSDF")


def get_mat(name):
    if name in _MATS:
        return _MATS[name]
    if name in ATLAS_MATS:
        _MATS[name] = atlas_mat(name)
        return _MATS[name]
    if name.startswith("Screen_"):
        return screen_mat(int(name.split("_")[1]))
    hx, rough, metal, ehex, estr, noise = PALETTE[name]
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    p = _principled(m)
    col = hexcol(hx)
    m.diffuse_color = col
    p.inputs["Base Color"].default_value = col
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    if ehex:
        p.inputs["Emission Color"].default_value = hexcol(ehex)
        p.inputs["Emission Strength"].default_value = estr
    if noise > 0:
        nt = m.node_tree
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        nz = nt.nodes.new("ShaderNodeTexNoise")
        nz.inputs["Scale"].default_value = 0.035
        nz.inputs["Detail"].default_value = 6.0
        mr = nt.nodes.new("ShaderNodeMapRange")
        mr.inputs["To Min"].default_value = 1.0 - noise
        mr.inputs["To Max"].default_value = 1.0 + noise * 0.6
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        mix.inputs[6].default_value = col
        nt.links.new(geo.outputs["Position"], nz.inputs["Vector"])
        nt.links.new(nz.outputs["Fac"], mr.inputs["Value"])
        nt.links.new(mr.outputs["Result"], mix.inputs[7])
        nt.links.new(mix.outputs[2], p.inputs["Base Color"])
    _MATS[name] = m
    return m


def screen_mat(i):
    name = "Screen_%02d" % i
    if name in _MATS:
        return _MATS[name]
    pal = SCREEN_PALETTES[(i - 1) % len(SCREEN_PALETTES)]
    r = random.Random(1000 + i)
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    nt = m.node_tree
    p = _principled(m)
    p.inputs["Base Color"].default_value = (0.01, 0.01, 0.012, 1)
    p.inputs["Roughness"].default_value = 0.25
    m.diffuse_color = hexcol(pal[0])
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Rotation"].default_value = (r.uniform(-0.6, 0.6), r.uniform(-0.6, 0.6), r.uniform(0, 3.14))
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.inputs["Scale"].default_value = r.uniform(1.2, 3.5)
    wave.inputs["Distortion"].default_value = r.uniform(3, 9)
    wave.inputs["Detail"].default_value = 2.0
    grad = nt.nodes.new("ShaderNodeTexGradient")
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "FLOAT"
    mix.inputs["Factor"].default_value = 0.55
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position = 0.15
    els[0].color = hexcol(pal[0])
    els[1].position = 0.85
    els[1].color = hexcol(pal[2])
    e = els.new(0.5)
    e.color = hexcol(pal[1])
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], wave.inputs["Vector"])
    nt.links.new(tc.outputs["Generated"], grad.inputs["Vector"])
    nt.links.new(grad.outputs["Fac"], mix.inputs[2])
    nt.links.new(wave.outputs["Fac"], mix.inputs[3])
    nt.links.new(mix.outputs[0], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], p.inputs["Emission Color"])
    p.inputs["Emission Strength"].default_value = 2.6
    _MATS[name] = m
    return m


# ----------------------------------------------------------------------------------------------
# mesh builder
# ----------------------------------------------------------------------------------------------
UV_SCALE = 8.0  # 1 UV unit = 8 studs (tiling textures later in Roblox)

FACE_KEYS = ("-x", "+x", "-y", "+y", "-z", "+z")


class MB:
    """Accumulates flat-shaded polygons (with per-face material) under a transform stack."""

    def __init__(self):
        self.verts = []
        self.faces = []
        self.fmat = []
        self.mats = []
        self.mi = {}
        self.M = Matrix.Identity(4)
        self.stack = []

    # transform stack ------------------------------------------------------------------------
    @contextmanager
    def xf(self, M):
        self.stack.append(self.M)
        self.M = self.M @ M
        try:
            yield
        finally:
            self.M = self.stack.pop()

    def _mat(self, name):
        if name not in self.mi:
            self.mi[name] = len(self.mats)
            self.mats.append(name)
        return self.mi[name]

    # primitives -----------------------------------------------------------------------------
    def face(self, pts, mat):
        M = self.M
        flip = M.to_3x3().determinant() < 0
        base = len(self.verts)
        for p in pts:
            self.verts.append(tuple(M @ Vector(p)))
        idx = list(range(base, base + len(pts)))
        if flip:
            idx.reverse()
        self.faces.append(idx)
        self.fmat.append(self._mat(mat))

    def box(self, x0, y0, z0, x1, y1, z1, mat, skip=(), fm=None):
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        z0, z1 = min(z0, z1), max(z0, z1)
        if x1 - x0 < 1e-5 or y1 - y0 < 1e-5 or z1 - z0 < 1e-5:
            return
        fm = fm or {}
        F = {
            "-z": [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)],
            "+z": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
            "-y": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
            "+y": [(x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)],
            "-x": [(x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)],
            "+x": [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
        }
        for k in FACE_KEYS:
            if k in skip:
                continue
            self.face(F[k], fm.get(k, mat))

    def cbox(self, cx, cy, cz, sx, sy, sz, mat, skip=(), fm=None):
        self.box(cx - sx / 2, cy - sy / 2, cz - sz / 2, cx + sx / 2, cy + sy / 2, cz + sz / 2, mat, skip, fm)

    def obox(self, M, sx, sy, sz, mat, skip=()):
        """Box centred on the origin of local matrix M."""
        with self.xf(M):
            self.box(-sx / 2, -sy / 2, -sz / 2, sx / 2, sy / 2, sz / 2, mat, skip)

    def prism(self, poly, z0, z1, mat, side_mats=None, top=True, bottom=False, top_mat=None):
        """Vertical extrusion of a CCW 2D polygon."""
        area = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))
        if area < 0:
            poly = list(reversed(poly))
            if side_mats:
                n_ = len(side_mats)
                side_mats = [side_mats[(n_ - 2 - i) % n_] for i in range(n_)]
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            sm = side_mats[i] if side_mats else mat
            self.face([(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)], sm)
        if top:
            self.face([(p[0], p[1], z1) for p in poly], top_mat or mat)
        if bottom:
            self.face([(p[0], p[1], z0) for p in reversed(poly)], mat)

    def cyl(self, cx, cy, r, z0, z1, n, mat, top=True, bottom=False, top_mat=None, phase=0.0):
        poly = [(cx + r * math.cos(phase + 2 * math.pi * i / n), cy + r * math.sin(phase + 2 * math.pi * i / n))
                for i in range(n)]
        self.prism(poly, z0, z1, mat, top=top, bottom=bottom, top_mat=top_mat)

    def extrude_x(self, pts_yz, x0, x1, mat):
        """Extrude a (y,z) profile polygon along x from x0 to x1."""
        area = sum(pts_yz[i][0] * pts_yz[(i + 1) % len(pts_yz)][1] - pts_yz[(i + 1) % len(pts_yz)][0] * pts_yz[i][1]
                   for i in range(len(pts_yz)))
        P = pts_yz if area > 0 else list(reversed(pts_yz))
        n = len(P)
        for i in range(n):
            a, b = P[i], P[(i + 1) % n]
            self.face([(x0, a[0], a[1]), (x0, b[0], b[1]), (x1, b[0], b[1]), (x1, a[0], a[1])], mat)
        self.face([(x1, p[0], p[1]) for p in P], mat)
        self.face([(x0, p[0], p[1]) for p in reversed(P)], mat)

    def beam(self, p0, p1, w, h, mat, skip=()):
        """Rectangular beam between two local points (w across, h up)."""
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        L = d.length
        if L < 1e-5:
            return
        x = d / L
        up = Vector((0, 0, 1))
        y = up.cross(x)
        if y.length < 1e-4:
            y = Vector((0, 1, 0))
        y.normalize()
        z = x.cross(y)
        M = Matrix(((x[0], y[0], z[0], p0[0]), (x[1], y[1], z[1], p0[1]), (x[2], y[2], z[2], p0[2]), (0, 0, 0, 1)))
        with self.xf(M):
            self.box(0, -w / 2, -h / 2, L, w / 2, h / 2, mat, skip)

    def empty(self):
        return not self.faces

    # build ----------------------------------------------------------------------------------
    def build(self, name, coll, origin=(0.0, 0.0, 0.0), parent=None, merge=True):
        if not self.faces:
            return None
        ox, oy, oz = origin
        verts = [(v[0] - ox, v[1] - oy, v[2] - oz) for v in self.verts]
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], self.faces)
        for mn in self.mats:
            me.materials.append(get_mat(mn))
        me.polygons.foreach_set("material_index", self.fmat)
        # box-projected world-scale UVs
        uv = me.uv_layers.new(name="UVMap")
        uvs = []
        for poly in me.polygons:
            nx, ny, nz = (abs(c) for c in poly.normal)
            for li in poly.loop_indices:
                co = me.vertices[me.loops[li].vertex_index].co
                wx, wy, wz = co.x + ox, co.y + oy, co.z + oz
                if nz >= nx and nz >= ny:
                    uvs += (wx / UV_SCALE, wy / UV_SCALE)
                elif nx >= ny:
                    uvs += (wy / UV_SCALE, wz / UV_SCALE)
                else:
                    uvs += (wx / UV_SCALE, wz / UV_SCALE)
        uv.data.foreach_set("uv", uvs)
        if merge:
            bm = bmesh.new()
            bm.from_mesh(me)
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
            bm.to_mesh(me)
            bm.free()
        me.update()
        ob = bpy.data.objects.new(name, me)
        ob.location = origin
        coll.objects.link(ob)
        if parent is not None:
            ob.parent = parent
            ob.location = Vector(origin) - parent.location
        return ob


def mesh_from(name, fn):
    """Create a standalone (shared / instanced) mesh datablock from a builder function."""
    mb = MB()
    fn(mb)
    tmp = bpy.data.collections.new("_tmp")
    ob = mb.build(name, tmp)
    me = ob.data
    bpy.data.objects.remove(ob)
    bpy.data.collections.remove(tmp)
    me.use_fake_user = True
    return me


# ----------------------------------------------------------------------------------------------
# scene / collections
# ----------------------------------------------------------------------------------------------
COLL = {}


def setup_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.name = "HEX_City"
    sc.unit_settings.system = "METRIC"
    sc.unit_settings.scale_length = 1.0
    root = bpy.data.collections.new("HEX_CITY_MAP")
    sc.collection.children.link(root)
    COLL["ROOT"] = root
    for name in ("MAIN_GROUND", "PRACTICE_AREA", "BUILDINGS_FOREGROUND", "BUILDINGS_MIDGROUND",
                 "SKYLINE_BACKGROUND", "BILLBOARD_SCREENS", "STRUCTURES", "GROUND_DETAILS"):
        c = bpy.data.collections.new(name)
        root.children.link(c)
        COLL[name] = c
    for name in ("FLOOR", "STAIRS", "RETAINING_WALLS", "RAILINGS"):
        c = bpy.data.collections.new(name)
        COLL["PRACTICE_AREA"].children.link(c)
        COLL[name] = c
    sg = bpy.data.collections.new("SIGNAGE")
    root.children.link(sg)
    COLL["SIGNAGE"] = sg
    for name in ("SIGNS_STOREFRONT", "SIGNS_FLOOR", "SIGNS_BLADE_VERTICAL", "SIGNS_ROOFTOP", "SIGNS_BUILDING_NAME",
                 "LED_STRIPS"):
        c = bpy.data.collections.new(name)
        sg.children.link(c)
        COLL[name] = c
    for name in ("SCREENS_LANDMARK", "SCREENS_LARGE", "SCREENS_MEDIUM", "SCREENS_SMALL", "SCREENS_SKYLINE"):
        c = bpy.data.collections.new(name)
        COLL["BILLBOARD_SCREENS"].children.link(c)
        COLL[name] = c
    ref = bpy.data.collections.new("SCALE_REFERENCE (not exported)")
    sc.collection.children.link(ref)
    COLL["REF"] = ref
    ghosts = bpy.data.collections.new("COURT_CAPACITY_GHOSTS (not exported)")
    ref.children.link(ghosts)
    COLL["GHOSTS"] = ghosts


# ----------------------------------------------------------------------------------------------
# court detection + layout
# ----------------------------------------------------------------------------------------------


def detect_court():
    cands = []
    env = os.environ.get("HEX_COURT_FBX")
    if env:
        cands.append(env)
    for pat in ("*.fbx", "*.FBX"):
        cands += sorted(glob.glob(os.path.join(REPO_DIR, "court", pat)))
    for path in cands:
        if not os.path.isfile(path):
            continue
        before = set(bpy.data.objects)
        try:
            bpy.ops.import_scene.fbx(filepath=path)
        except Exception as e:  # pragma: no cover
            print("!! court import failed:", e)
            continue
        new = [o for o in bpy.data.objects if o not in before]
        mn = Vector((1e9, 1e9, 1e9))
        mx = Vector((-1e9, -1e9, -1e9))
        for o in new:
            if o.type != "MESH":
                continue
            for c in o.bound_box:
                w = o.matrix_world @ Vector(c)
                mn = Vector(map(min, mn, w))
                mx = Vector(map(max, mx, w))
        for o in new:
            for c in list(o.users_collection):
                c.objects.unlink(o)
            COLL["REF"].objects.link(o)
        if mx.x < mn.x:
            continue
        dims = mx - mn
        L, W = max(dims.x, dims.y), min(dims.x, dims.y)
        print("== HEX court imported from %s : %.1f x %.1f x %.1f studs" % (path, dims.x, dims.y, dims.z))
        return dict(L=L, W=W, H=dims.z, path=os.path.relpath(path, REPO_DIR), objects=new, mn=mn, mx=mx)
    return None


def compute_layout(CL, CW):
    lay = SimpleNamespace()
    lay.CL, lay.CW = CL, CW
    lay.slot_l = ceil_to(CL + 40, 10)       # full court + run-off + circulation
    lay.slot_w = ceil_to(CW + 36, 10)
    lay.half_l = ceil_to(CL / 2 + 30, 10)  # half court slot length
    gap = 15
    lay.spine_ns, lay.spine_ew, lay.band = 60.0, 50.0, 30.0
    # practice pit
    lay.PX = max(210.0, (lay.slot_l + lay.half_l + 110) / 2)
    lay.PD = max(190.0, lay.slot_w + 60)
    lay.STAIRZ = 40.0
    lay.PY0 = -(lay.spine_ew / 2 + 35)
    lay.PY1 = lay.PY0 - lay.STAIRZ - lay.PD
    lay.TRUN = 40.0
    lay.TW = 36.0
    tyc = lay.PY0 - lay.STAIRZ - lay.PD / 2
    lay.TY0, lay.TY1 = tyc - lay.TW / 2, tyc + lay.TW / 2
    # plaza half extents
    hx_courts = lay.spine_ns / 2 + 2 * lay.slot_l + 3 * gap + lay.band
    hx_wings = lay.PX + WALL_T + lay.TRUN + WALL_T + 14 + lay.slot_w + 2 * gap + lay.band
    lay.HX = ceil_to(max(485.0, hx_courts, hx_wings), 5)
    hy_courts = lay.spine_ew / 2 + 2 * lay.slot_w + 3 * gap + lay.band
    hy_pit = -lay.PY1 + WALL_T + 14 + 60 + lay.band
    lay.HY = ceil_to(max(380.0, hy_courts, hy_pit), 5)
    lay.FX = lay.HX + ST + SW
    lay.FY = lay.HY + ST + SW
    # side streets: (side, world coordinate along that side)
    lay.side_streets = {"N": 150.0, "S": -lay.HX * 0.55, "E": 0.33 * lay.HY, "W": -0.37 * lay.HY}
    return lay


# ----------------------------------------------------------------------------------------------
# instanced rooftop / street equipment
# ----------------------------------------------------------------------------------------------
LIB = {}
EQUIP_FOOT = {"HVAC_S": (5.5, 4.0), "HVAC_L": (11, 4.5), "WaterTank": (7.5, 7.5), "Antenna": (2.5, 2.5),
              "Vent": (2.5, 2.5), "CoolingTower": (10, 10), "SatDish": (4, 4)}


def build_library():
    def hvac_s(mb):
        mb.box(-2.5, -1.75, 0, 2.5, 1.75, 0.4, "DarkMetal")
        mb.box(-2.4, -1.65, 0.4, 2.4, 1.65, 3.0, "AC_Unit")
        mb.cyl(0.9, 0, 1.2, 3.0, 3.25, 10, "DarkMetal")
        mb.box(-2.2, -1.75, 0.8, -0.4, -1.6, 2.6, "Grate_Metal")

    def hvac_l(mb):
        mb.box(-5.3, -2.1, 0, 5.3, 2.1, 0.4, "DarkMetal")
        mb.box(-5.2, -2.0, 0.4, 5.2, 2.0, 3.4, "AC_Unit")
        for cx in (-2.6, 2.6):
            mb.cyl(cx, 0, 1.6, 3.4, 3.8, 12, "DarkMetal")
        mb.box(-5.2, -2.05, 0.9, 5.2, -1.95, 2.8, "Grate_Metal")

    def water(mb):
        for sx in (-2.2, 2.2):
            for sy in (-2.2, 2.2):
                mb.box(sx - 0.25, sy - 0.25, 0, sx + 0.25, sy + 0.25, 3.2, "DarkMetal")
        mb.box(-2.8, -2.8, 3.0, 2.8, 2.8, 3.4, "DarkMetal")
        mb.cyl(0, 0, 3.1, 3.4, 8.6, 10, "Concrete_Light", top=False, bottom=True)
        cone = [(3.2 * math.cos(2 * math.pi * i / 10), 3.2 * math.sin(2 * math.pi * i / 10)) for i in range(10)]
        for i in range(10):
            a, b = cone[i], cone[(i + 1) % 10]
            mb.face([(a[0], a[1], 8.6), (b[0], b[1], 8.6), (0, 0, 10.0)], "Roof_Dark")

    def antenna(mb):
        mb.box(-1.0, -1.0, 0, 1.0, 1.0, 0.6, "Concrete_Dark")
        mb.box(-0.18, -0.18, 0.6, 0.18, 0.18, 18, "DarkMetal")
        for z, w in ((10, 3.0), (13.5, 2.2), (16.5, 1.4)):
            mb.box(-w, -0.08, z, w, 0.08, z + 0.2, "DarkMetal")

    def vent(mb):
        mb.box(-1.1, -1.1, 0, 1.1, 1.1, 1.8, "Metal_Panel")
        mb.cyl(0, 0, 0.45, 1.8, 3.6, 8, "DarkMetal")
        mb.box(-0.7, -0.7, 3.6, 0.7, 0.7, 3.9, "DarkMetal")

    def cooling(mb):
        mb.box(-4.8, -4.8, 0, 4.8, 4.8, 0.5, "DarkMetal")
        mb.box(-4.6, -4.6, 0.5, 4.6, 4.6, 6.0, "Metal_Panel")
        mb.cyl(0, 0, 3.6, 6.0, 7.6, 12, "AC_Unit", top=False)
        mb.cyl(0, 0, 3.3, 6.2, 7.4, 12, "Grate_Metal")
        mb.box(-4.65, -4.65, 1.0, 4.65, -4.55, 5.0, "Grate_Metal")

    def dish(mb):
        mb.box(-0.4, -0.4, 0, 0.4, 0.4, 2.4, "DarkMetal")
        M = trans(0, 0, 3.2) @ Matrix.Rotation(math.radians(55), 4, "X")
        with mb.xf(M):
            mb.cyl(0, 0, 1.8, -0.15, 0.15, 10, "OffWhite", bottom=True)

    def mast(mb):
        # tall plaza light mast (58 studs) with 4-head crossbar
        mb.box(-1.6, -1.6, 0, 1.6, 1.6, 1.4, "Concrete_Dark")
        mb.cyl(0, 0, 0.75, 1.4, 56, 8, "Metal_Rail", top=False)
        mb.box(-0.5, -0.5, 52.5, 0.5, 0.5, 56.6, "Metal_Rail")
        mb.box(-7.0, -0.6, 55.5, 7.0, 0.6, 56.6, "Metal_Rail")
        for x in (-5.4, -1.8, 1.8, 5.4):
            mb.box(x - 1.5, -1.3, 54.2, x + 1.5, 1.3, 55.5, "DarkMetal", skip=("-z",))
            mb.box(x - 1.3, -1.1, 54.1, x + 1.3, 1.1, 54.2, "Lamp_Warm")

    def mast_s(mb):
        # practice-area mast (38 studs), single long head pointing over the pit
        mb.box(-1.3, -1.3, 0, 1.3, 1.3, 1.2, "Concrete_Dark")
        mb.cyl(0, 0, 0.6, 1.2, 38, 8, "Metal_Rail", top=False)
        mb.box(-0.45, -0.45, 36, 0.45, 6.5, 38.6, "Metal_Rail")
        for y in (2.0, 5.0):
            mb.box(-2.4, y - 1.2, 36.6, 2.4, y + 1.2, 37.8, "DarkMetal", skip=("-z",))
            mb.box(-2.2, y - 1.0, 36.5, 2.2, y + 1.0, 36.6, "Lamp_Warm")

    def vending(body):
        def fn(mb):
            mb.box(-1.7, -1.2, 0, 1.7, 1.2, 6.2, body)
            mb.box(-1.5, -1.25, 2.6, 0.6, -1.2, 5.8, "Vending_Panel")
            mb.box(0.8, -1.3, 3.0, 1.5, -1.2, 4.6, "DarkMetal")
            mb.box(-1.5, -1.3, 0.6, 1.5, -1.2, 1.6, "FadedBlack")
            mb.box(-1.75, -1.25, 6.2, 1.75, 1.25, 6.5, "DarkMetal")
        return fn

    def pole(mb):
        mb.cyl(0, 0, 0.65, 0, 36, 8, "Pole_Concrete")
        for z, w in ((33.0, 2.2), (30.5, 1.6)):
            mb.box(-w, -0.25, z - 0.3, w, 0.25, z + 0.3, "DarkMetal")
        mb.cyl(0.9, 0.9, 0.8, 24.5, 27.5, 8, "Metal_Panel", bottom=True)
        mb.box(-0.3, 0.6, 9.0, 0.3, 1.0, 12.0, "OffWhite")

    for name, fn in (("Vending_Drink", vending("Vending_Body")), ("Vending_Red", vending("Vending_RedBody")),
                     ("UtilityPole", pole)):
        LIB[name] = mesh_from("LIB_" + name, fn)
    for name, fn in (("HVAC_S", hvac_s), ("HVAC_L", hvac_l), ("WaterTank", water), ("Antenna", antenna),
                     ("Vent", vent), ("CoolingTower", cooling), ("SatDish", dish),
                     ("LightMast_Plaza", mast), ("LightMast_Practice", mast_s)):
        LIB[name] = mesh_from("LIB_" + name, fn)


_inst_count = {}


def instance(kind, world_loc, rz, coll, parent=None, name=None):
    _inst_count[kind] = _inst_count.get(kind, 0) + 1
    nm = name or ("%s_%s_%02d" % (parent.name if parent else "RT", kind, _inst_count[kind]))
    ob = bpy.data.objects.new(nm, LIB[kind])
    coll.objects.link(ob)
    ob.rotation_euler = (0, 0, rz)
    if parent is not None:
        ob.parent = parent
        ob.location = Vector(world_loc) - parent.location
    else:
        ob.location = world_loc
    return ob


# ----------------------------------------------------------------------------------------------
# facade generators (all in a "front frame":  a along facade, t<0 = outward, z up)
# ----------------------------------------------------------------------------------------------
SIGN_FLAT = ["Sign_Cream", "Sign_Red", "Sign_Navy", "Sign_Black", "Sign_Cream", "Sign_Black"]
SIGN_LIT = ["LED_White", "LED_Cyan", "LED_Magenta", "HEX_Orange_LED", "LED_Purple"]
AWNINGS = ["Awning_Red", "Awning_Green", "Awning_Navy", "Awning_Beige", "Charcoal"]


def storefront(mb, rng, L, sp, base=Z0, simple=False):
    wall = sp["wall"]
    fascia = sp.get("fascia", wall)
    mb.box(0, -0.45, base, L, 0, 0.45, "Concrete_Dark", skip=("+y",))
    mb.box(0, -0.12, 0.45, L, 0, SF_TOP, "Glass_Store", skip=("+y", "-z"))
    mb.box(0, -1.1, SF_TOP, L, 0, GF_H, fascia, skip=("+y",))
    if simple:
        n = max(1, round(L / 20.0))
        for i in range(n + 1):
            a = L * i / n
            mb.box(max(0, a - 0.9), -1.0, base, min(L, a + 0.9), 0, SF_TOP, wall, skip=("+y",))
        return
    n = max(1, round(L / rng.uniform(13.0, 22.0)))
    sw_ = L / n
    for i in range(n + 1):
        a = sw_ * i
        mb.box(max(0, a - 0.9), -1.1, base, min(L, a + 0.9), 0, SF_TOP, wall, skip=("+y",))
    for i in range(n):
        a0, a1 = i * sw_ + 0.9, (i + 1) * sw_ - 0.9
        if a1 - a0 < 6:
            continue
        dc = a0 + (a1 - a0) * rng.choice((0.25, 0.5, 0.75))
        dc = min(max(dc, a0 + DOOR_W / 2 + 0.6), a1 - DOOR_W / 2 - 0.6)
        mb.box(dc - DOOR_W / 2 - 0.3, -0.35, 0, dc + DOOR_W / 2 + 0.3, -0.12, DOOR_H + 0.3, "DarkMetal")
        mb.box(dc - DOOR_W / 2, -0.4, 0, dc + DOOR_W / 2, -0.25, DOOR_H, "Door_Dark")
        mb.box(a0, -0.3, DOOR_H + 0.3, a1, -0.1, DOOR_H + 0.7, "DarkMetal")
        k = int((a1 - a0) / 4.5)
        for j in range(1, k):
            m = a0 + (a1 - a0) * j / k
            if abs(m - dc) < DOOR_W / 2 + 0.8:
                continue
            mb.box(m - 0.15, -0.3, 0.45, m + 0.15, -0.1, SF_TOP, "DarkMetal")
        r = rng.random()
        if r < 0.55:
            mat = rng.choice(SIGN_LIT) if rng.random() < 0.35 else rng.choice(SIGN_FLAT)
            mb.box(a0 + 1.2, -1.5, SF_TOP + 0.7, a1 - 1.2, -1.1, GF_H - 0.7, mat)
        r = rng.random()
        if r < 0.35:  # sloped fabric awning
            depth = rng.uniform(3.5, 5.0)
            M = trans((a0 + a1) / 2, -depth / 2, SF_TOP - 0.7) @ Matrix.Rotation(math.radians(-18), 4, "X")
            mb.obox(M, a1 - a0 - 0.4, depth + 0.6, 0.25, rng.choice(AWNINGS))
        elif r < 0.6:  # flat metal canopy
            mb.box(a0, -rng.uniform(3, 4.5), SF_TOP - 0.2, a1, 0, SF_TOP + 0.3, "DarkMetal")


def facade_grid(mb, rng, L, zs, ze, sp, roof_led=None):
    """Piers + spandrel bands over a glass core face -> deep recessed window grid."""
    if ze - zs < 2:
        return
    fh = sp["fh"]
    nfl = max(1, int((ze - zs) / fh + 0.01))
    nb = max(1, round(L / sp["bay"]))
    bw = L / nb
    pw, pd = sp["pier_w"], sp["pier_d"]
    bd = sp["band_d"]
    wall = sp["wall"]
    pier_mat = sp.get("pier_mat", wall)
    for i in range(nb + 1):
        a = i * bw
        a0, a1 = max(0.0, a - pw / 2), min(L, a + pw / 2)
        if i in (0, nb):
            a0, a1 = (0.0, pw) if i == 0 else (L - pw, L)
        mb.box(a0, -pd, zs, a1, 0, ze, pier_mat, skip=("+y",))
    sill = sp["sill"]
    wh = sp["win_h"]
    prev_top = zs
    tops = []
    for k in range(nfl):
        zf = zs + k * fh
        wb = zf + sill
        wt = min(wb + wh, zf + fh - 0.25)
        if wb - prev_top > 0.05:
            mb.box(0, -bd, prev_top, L, 0, wb, wall, skip=("+y",))
        tops.append((wb, wt))
        prev_top = wt
    if ze - prev_top > 0.05:
        mb.box(0, -bd, prev_top, L, 0, ze, wall, skip=("+y",))
    if sp.get("mull"):
        for i in range(nb):
            c = (i + 0.5) * bw
            mb.box(c - 0.16, -0.3, zs, c + 0.16, 0, ze, "DarkMetal", skip=("+y",))
    if sp.get("ledge") or sp.get("ac", 0) > 0:
        for i in range(nb):
            a0, a1 = i * bw + pw / 2, (i + 1) * bw - pw / 2
            for wb, wt in tops:
                if sp.get("ledge"):
                    mb.box(a0 + 0.2, -bd - 0.45, wb - 0.35, a1 - 0.2, 0, wb, sp.get("ledge_mat", "Concrete_Light"),
                           skip=("+y",))
                if rng.random() < sp.get("ac", 0):
                    c = rng.uniform(a0 + 1.4, max(a0 + 1.4, a1 - 1.4))
                    mb.box(c - 1.2, -bd - 1.3, wb - 2.1, c + 1.2, -bd, wb - 0.35, "AC_Unit")
    if sp.get("cornice"):
        mb.box(0, -bd - 1.4, ze - 1.6, L, 0, ze, sp.get("cornice_mat", "Concrete_Light"), skip=("+y",))
    if roof_led:
        mb.box(0, -bd - 0.12, ze - 0.7, L, -bd, ze - 0.35, roof_led, skip=("+y",))


FACE_FRAMES = {
    # name: (origin_fn(x0,y0,x1,y1), a_axis, t_axis, length_fn, core box face key)
    "front": (lambda x0, y0, x1, y1: (x0, y0), (1, 0), (0, 1), lambda x0, y0, x1, y1: x1 - x0, "-y"),
    "right": (lambda x0, y0, x1, y1: (x1, y0), (0, 1), (-1, 0), lambda x0, y0, x1, y1: y1 - y0, "+x"),
    "back": (lambda x0, y0, x1, y1: (x1, y1), (-1, 0), (0, -1), lambda x0, y0, x1, y1: x1 - x0, "+y"),
    "left": (lambda x0, y0, x1, y1: (x0, y1), (0, -1), (1, 0), lambda x0, y0, x1, y1: y1 - y0, "-x"),
}


@contextmanager
def face_frame(mb, rect, face, z=0.0):
    ofn, ax, ay, lfn, _ = FACE_FRAMES[face]
    o = ofn(*rect)
    with mb.xf(frame_matrix((o[0], o[1], z), ax, ay)):
        yield lfn(*rect)


def parapet(mb, rect, z, h, t, mat, coping="Concrete_Light"):
    x0, y0, x1, y1 = rect
    mb.box(x0, y0, z, x1, y0 + t, z + h, mat)
    mb.box(x0, y1 - t, z, x1, y1, z + h, mat)
    mb.box(x0, y0 + t, z, x0 + t, y1 - t, z + h, mat)
    mb.box(x1 - t, y0 + t, z, x1, y1 - t, z + h, mat)
    if coping:
        mb.box(x0 - 0.15, y0 - 0.15, z + h, x1 + 0.15, y0 + t + 0.1, z + h + 0.3, coping)


def mass(mb, rng, rect, z0, z1, sp, faces, store_faces=(), simple_store=False, roof_led=None, parapet_h=None):
    """A box volume with detailed facades on `faces`; other faces get plain wall material."""
    fm = {}
    for f, (_, _, _, _, key) in FACE_FRAMES.items():
        fm[key] = sp["core"] if f in faces else sp.get("plain", sp["wall"])
    fm["+z"] = sp.get("roof", "Roof_Gray")
    x0, y0, x1, y1 = rect
    mb.box(x0, y0, z0, x1, y1, z1, sp["wall"], skip=("-z",), fm=fm)
    for f in faces:
        with face_frame(mb, rect, f) as L:
            if f in store_faces and z0 <= 0.5:
                storefront(mb, rng, L, sp, base=z0, simple=simple_store)
                facade_grid(mb, rng, L, GF_H, z1, sp, roof_led=roof_led)
            else:
                facade_grid(mb, rng, L, z0 if z0 > 0.5 else GF_H, z1, sp, roof_led=roof_led)
                if z0 <= 0.5 and f not in store_faces:
                    # plain ground floor base under the grid
                    mb.box(0, -sp["band_d"], z0, L, 0, GF_H, sp["wall"], skip=("+y",))
    ph = parapet_h if parapet_h is not None else rng.uniform(2.0, 3.4)
    parapet(mb, rect, z1, ph, 0.8, sp["wall"])
    return dict(rect=rect, z=z1)


# ----------------------------------------------------------------------------------------------
# facade specs per architectural type
# ----------------------------------------------------------------------------------------------


def spec_zakkyo(rng):
    return dict(core="Glass_Dark", wall=rng.choice(["OffWhite", "Beige_Tile", "Concrete_Light", "Charcoal",
                                                     "Tile_Gray", "Concrete_Wall", "Stucco_Old"]),
                fh=rng.choice([11.0, 11.5, 12.0]), bay=rng.uniform(6.0, 8.5), pier_w=rng.uniform(1.6, 2.6),
                pier_d=rng.uniform(0.6, 1.0), sill=rng.uniform(3.0, 3.6), win_h=rng.uniform(5.2, 6.4),
                band_d=rng.uniform(0.5, 0.9), ledge=rng.random() < 0.45, ac=0.10,
                roof=rng.choice(["Roof_Gray", "Roof_Dark"]))


def spec_office(rng):
    core = rng.choice(["Glass_Blue", "Glass_Dark", "Glass_Dark"])
    return dict(core=core, wall=rng.choice(["Concrete_Wall", "Concrete_Light", "OffWhite", "Tile_Gray",
                                            "Concrete_Dark"]),
                fh=12.0, bay=rng.uniform(8.5, 11.0), pier_w=rng.uniform(1.2, 2.2), pier_d=rng.uniform(1.0, 1.6),
                sill=rng.uniform(2.8, 3.4), win_h=rng.uniform(6.0, 7.4), band_d=rng.uniform(0.6, 1.1),
                mull=True, roof="Roof_Gray")


def spec_ribbon(rng):
    # horizontal ribbon windows (bands dominate)
    return dict(core=rng.choice(["Glass_Dark", "Glass_Blue"]), wall=rng.choice(["OffWhite", "Concrete_Light",
                                                                                 "Metal_Panel"]),
                fh=12.0, bay=rng.uniform(14, 20), pier_w=0.5, pier_d=0.4, sill=3.6, win_h=5.2,
                band_d=rng.uniform(1.0, 1.6), mull=True, pier_mat="DarkMetal", roof="Roof_Gray")


def spec_glass(rng):
    return dict(core=rng.choice(["Glass_Blue", "Glass_Silver", "Glass_Dark"]),
                wall=rng.choice(["Aluminum", "DarkMetal", "Metal_Panel"]), plain="Glass_Dark",
                fh=12.0, bay=rng.uniform(4.2, 5.5), pier_w=0.45, pier_d=rng.uniform(0.9, 1.4), sill=0.0,
                win_h=11.3, band_d=0.35, roof="Roof_Dark")


def spec_brick(rng):
    return dict(core="Glass_Dark", wall=rng.choice(["Brick", "Brick_Dark", "Stucco_Old", "Brick"]),
                fh=12.0, bay=rng.uniform(6.5, 7.5), pier_w=rng.uniform(2.6, 3.2), pier_d=0.7, sill=3.6,
                win_h=6.2, band_d=0.5, ledge=True, ac=0.06, cornice=True, roof="Roof_Dark")


def spec_mid(rng):
    t = rng.random()
    if t < 0.45:
        s = spec_office(rng)
        s["bay"] = rng.uniform(11, 14)
        s["mull"] = False
    elif t < 0.75:
        s = spec_glass(rng)
        s["bay"] = rng.uniform(7, 9)
    else:
        s = spec_ribbon(rng)
    return s


# ----------------------------------------------------------------------------------------------
# GROUND
# ----------------------------------------------------------------------------------------------


def slab(name, coll, rect, z1, mat, side_mat=None, thick=4.0):
    x0, y0, x1, y1 = rect
    mb = MB()
    sm = side_mat or mat
    mb.box(x0, y0, z1 - thick, x1, y1, z1, mat, skip=("-z",),
           fm={"-x": sm, "+x": sm, "-y": sm, "+y": sm})
    return mb.build(name, coll, origin=((x0 + x1) / 2, (y0 + y1) / 2, z1))


def build_ground(lay, rng):
    HX, HY, PX, PY0, PY1 = lay.HX, lay.HY, lay.PX, lay.PY0, lay.PY1
    ns2, ew2, band = lay.spine_ns / 2, lay.spine_ew / 2, lay.band
    P = Painter()
    P.paint((-HX, -HY, HX, HY), "Ground_Concrete")
    wing_x = PX + WALL_T + lay.TRUN + WALL_T + 14
    P.paint((-HX + band, ew2, -ns2, HY - band), "Ground_ConcreteLight")          # NW court field
    P.paint((ns2, ew2, HX - band, HY - band), "Ground_Asphalt")                 # NE court field
    P.paint((-HX + band, -HY + band, -wing_x, -ew2), "Ground_Asphalt")          # SW wing
    P.paint((wing_x, -HY + band, HX - band, -ew2), "Ground_ConcreteLight")      # SE wing
    for r in ((-HX, -HY, HX, -HY + band), (-HX, HY - band, HX, HY),
              (-HX, -HY, -HX + band, HY), (HX - band, -HY, HX, HY)):
        P.paint(r, "Ground_PaverDark")                                          # perimeter promenade
    P.paint((-HX, -ew2, HX, ew2), "Ground_PaverDark")                           # E-W spine
    P.paint((-ns2, ew2, ns2, HY), "Ground_PaverDark")                           # N-S spine
    P.paint((-60, PY0 + WALL_T + 14, 60, 50), "Ground_PaverBrick")              # forecourt above grand stair
    for side, v in lay.side_streets.items():                                     # brick entry mats
        if side in "NS":
            y0 = HY - band if side == "N" else -HY
            P.paint((v - 32, y0, v + 32, y0 + band), "Ground_PaverBrick")
        else:
            x0 = HX - band if side == "E" else -HX
            P.paint((x0, v - 32, x0 + band, v + 32), "Ground_PaverBrick")
    ap = 14
    P.paint((-PX - WALL_T - ap, PY1 - WALL_T - ap, PX + WALL_T + ap, PY0 + WALL_T + ap), "Ground_ConcreteLight")
    for sgn in (1, -1):
        x0 = PX + WALL_T
        x1 = PX + WALL_T + lay.TRUN + WALL_T + ap
        r = (min(sgn * x0, sgn * x1), lay.TY0 - WALL_T - ap, max(sgn * x0, sgn * x1), lay.TY1 + WALL_T + ap)
        P.paint(r, "Ground_ConcreteLight")
    # holes: pit + stair trenches
    P.paint((-PX - WALL_T, PY1 - WALL_T, PX + WALL_T, PY0 + WALL_T), None)
    for sgn in (1, -1):
        x0, x1 = PX + WALL_T, PX + WALL_T + lay.TRUN
        P.paint((min(sgn * x0, sgn * x1), lay.TY0 - WALL_T, max(sgn * x0, sgn * x1), lay.TY1 + WALL_T), None)

    names = {"Ground_Concrete": "Plaza_Concrete", "Ground_ConcreteLight": "Plaza_ConcreteLight",
             "Ground_Asphalt": "Plaza_Asphalt", "Ground_PaverDark": "Plaza_Promenade",
             "Ground_PaverBrick": "Plaza_BrickPavers"}
    cnt = {}
    plaza_pieces = []
    for rect, tag in sorted(P.pieces, key=lambda p: (p[1], p[0][1], p[0][0])):
        cnt[tag] = cnt.get(tag, 0) + 1
        slab("%s_%02d" % (names[tag], cnt[tag]), COLL["MAIN_GROUND"], rect, 0.0, tag, side_mat="Curb_Granite")
        plaza_pieces.append((rect, tag))

    # ---- city ground: streets (base) + sidewalks / block pads ----
    FX, FY = lay.FX, lay.FY
    EXT = 1500.0
    base_ring = Painter()
    base_ring.paint((-EXT, -EXT, EXT, EXT), "Ground_Street")
    base_ring.paint((-HX, -HY, HX, HY), None)
    # split big pieces into <= 1500 chunks (Roblox mesh size limit 2048)
    k = 0
    for rect, tag in base_ring.pieces:
        x0, y0, x1, y1 = rect
        nx = max(1, math.ceil((x1 - x0) / 1500))
        ny = max(1, math.ceil((y1 - y0) / 1500))
        for i in range(nx):
            for j in range(ny):
                k += 1
                r = (x0 + (x1 - x0) * i / nx, y0 + (y1 - y0) * j / ny,
                     x0 + (x1 - x0) * (i + 1) / nx, y0 + (y1 - y0) * (j + 1) / ny)
                slab("City_Street_%02d" % k, COLL["MAIN_GROUND"], r, Z_STREET, "Ground_Street", thick=4.0)
    pads = Painter()
    R = max(FX, FY) + 360
    pads.paint((-FX - 360, -FY - 360, FX + 360, FY + 360), "Ground_Sidewalk")
    pads.paint((-HX, -HY, HX, HY), None)
    streets = street_rects(lay)
    for r in streets:
        pads.paint(r, None)
    k = 0
    for rect, tag in pads.pieces:
        x0, y0, x1, y1 = rect
        nx = max(1, math.ceil((x1 - x0) / 1500))
        ny = max(1, math.ceil((y1 - y0) / 1500))
        for i in range(nx):
            for j in range(ny):
                k += 1
                r = (x0 + (x1 - x0) * i / nx, y0 + (y1 - y0) * j / ny,
                     x0 + (x1 - x0) * (i + 1) / nx, y0 + (y1 - y0) * (j + 1) / ny)
                slab("City_Sidewalk_%02d" % k, COLL["MAIN_GROUND"], r, 0.0, "Ground_Sidewalk",
                     side_mat="Curb_Granite", thick=0.6 + 0.01)
    return plaza_pieces, streets


def street_rects(lay):
    HX, HY, FX, FY = lay.HX, lay.HY, lay.FX, lay.FY
    out = []
    # perimeter ring
    out += [(-HX - ST, HY, HX + ST, HY + ST), (-HX - ST, -HY - ST, HX + ST, -HY),
            (HX, -HY, HX + ST, HY), (-HX - ST, -HY, -HX, HY)]
    reach_x = FX + MG_OFF
    reach_y = FY + MG_OFF
    # corner extensions (streets continue outward from each corner to the midground wall)
    for sx in (1, -1):
        for sy in (1, -1):
            # E-W street continuing outward in x
            xa, xb = sx * (HX + ST), sx * reach_x
            ya, yb = (HY, HY + ST) if sy > 0 else (-HY - ST, -HY)
            out.append((min(xa, xb), ya, max(xa, xb), yb))
            # N-S street continuing outward in y
            ya, yb = sy * (HY + ST), sy * reach_y
            xa, xb = (HX, HX + ST) if sx > 0 else (-HX - ST, -HX)
            out.append((xa, min(ya, yb), xb, max(ya, yb)))
    # mid-side streets
    for side, v in lay.side_streets.items():
        if side == "N":
            out.append((v - ST / 2, HY + ST, v + ST / 2, reach_y))
        elif side == "S":
            out.append((v - ST / 2, -reach_y, v + ST / 2, -HY - ST))
        elif side == "E":
            out.append((HX + ST, v - ST / 2, reach_x, v + ST / 2))
        else:
            out.append((-reach_x, v - ST / 2, -HX - ST, v + ST / 2))
    return out


# ----------------------------------------------------------------------------------------------
# GROUND DETAILS (curbs, joints, drains, paint)
# ----------------------------------------------------------------------------------------------


def build_ground_details(lay, plaza_pieces, rng):
    HX, HY = lay.HX, lay.HY
    col = COLL["GROUND_DETAILS"]
    # expansion joints on concrete / asphalt fields
    jm = MB()
    for rect, tag in plaza_pieces:
        if tag not in ("Ground_Concrete", "Ground_ConcreteLight"):
            continue
        x0, y0, x1, y1 = rect
        step = 40.0
        x = math.ceil((x0 + 0.1) / step) * step
        while x < x1 - 0.1:
            jm.box(x - 0.12, y0, 0, x + 0.12, y1, 0.035, "Joint_Dark", skip=("-z",))
            x += step
        y = math.ceil((y0 + 0.1) / step) * step
        while y < y1 - 0.1:
            jm.box(x0, y - 0.12, 0, x1, y + 0.12, 0.035, "Joint_Dark", skip=("-z",))
            y += step
    jm.build("Plaza_ExpansionJoints", col)

    # drainage channels + granite edging between zones
    dm = MB()
    ns2, ew2, band = lay.spine_ns / 2, lay.spine_ew / 2, lay.band
    lines = []
    for sx in (-1, 1):
        lines.append(((sx * ns2, ew2), (sx * ns2, HY - band)))
    for sy in (-1, 1):
        lines.append(((-HX + band, sy * ew2), (HX - band, sy * ew2)))
    lines += [((-HX + band, -HY + band), (HX - band, -HY + band)), ((-HX + band, HY - band), (HX - band, HY - band)),
              ((-HX + band, -HY + band), (-HX + band, HY - band)), ((HX - band, -HY + band), (HX - band, HY - band))]
    for (ax, ay), (bx, by) in lines:
        if abs(ax - bx) < 1e-3:
            dm.box(ax - 0.8, min(ay, by), 0, ax + 0.8, max(ay, by), 0.04, "Grate_Metal", skip=("-z",))
            dm.box(ax - 1.6, min(ay, by), 0, ax - 0.8, max(ay, by), 0.05, "Curb_Granite", skip=("-z",))
            dm.box(ax + 0.8, min(ay, by), 0, ax + 1.6, max(ay, by), 0.05, "Curb_Granite", skip=("-z",))
        else:
            dm.box(min(ax, bx), ay - 0.8, 0, max(ax, bx), ay + 0.8, 0.04, "Grate_Metal", skip=("-z",))
            dm.box(min(ax, bx), ay - 1.6, 0, max(ax, bx), ay - 0.8, 0.05, "Curb_Granite", skip=("-z",))
            dm.box(min(ax, bx), ay + 0.8, 0, max(ax, bx), ay + 1.6, 0.05, "Curb_Granite", skip=("-z",))
    dm.build("Plaza_DrainChannels", col)

    # plaza outer curb
    cm = MB()
    c = 1.4
    cm.box(-HX, HY - c, Z_STREET, HX, HY, 0.12, "Curb_Granite")
    cm.box(-HX, -HY, Z_STREET, HX, -HY + c, 0.12, "Curb_Granite")
    cm.box(-HX, -HY + c, Z_STREET, -HX + c, HY - c, 0.12, "Curb_Granite")
    cm.box(HX - c, -HY + c, Z_STREET, HX, HY - c, 0.12, "Curb_Granite")
    cm.build("Plaza_Curb", col)

    # street paint: crossings at the four corner intersections (incl. Shibuya-style diagonals),
    # side-street mouths, and the dashed centre line
    pm = MB()
    zt = Z_STREET + 0.03

    def zebra_across_x(x0, x1, yc, depth=13.0):
        # crossing a street that runs along y (path along x): bars long in y
        n = int((x1 - x0) / 3.4)
        for i in range(n):
            a = x0 + 1 + i * 3.4
            pm.box(a, yc - depth / 2, Z_STREET, a + 1.7, yc + depth / 2, zt, "Paint_White", skip=("-z",))

    def zebra_across_y(y0, y1, xc, depth=13.0):
        n = int((y1 - y0) / 3.4)
        for i in range(n):
            a = y0 + 1 + i * 3.4
            pm.box(xc - depth / 2, a, Z_STREET, xc + depth / 2, a + 1.7, zt, "Paint_White", skip=("-z",))

    for sx in (1, -1):
        for sy in (1, -1):
            ix0, ix1 = sorted((sx * HX, sx * (HX + ST)))
            iy0, iy1 = sorted((sy * HY, sy * (HY + ST)))
            # four arms
            zebra_across_y(iy0, iy1, sx * (HX - 9))            # across the E-W street, plaza side
            zebra_across_y(iy0, iy1, sx * (HX + ST + 9))       # across the E-W street, outer side
            zebra_across_x(ix0, ix1, sy * (HY - 9))
            zebra_across_x(ix0, ix1, sy * (HY + ST + 9))
            # scramble diagonals
            cxm, cym = (ix0 + ix1) / 2, (iy0 + iy1) / 2
            for diag in (1, -1):
                ang = math.atan2(diag, 1)
                dvec = Vector((math.cos(ang), math.sin(ang), 0))
                nvec = Vector((-dvec.y, dvec.x, 0))
                Ld = ST * math.sqrt(2) - 14
                n = int(Ld / 3.4)
                for i in range(n):
                    o = Vector((cxm, cym, (Z_STREET + zt) / 2)) + dvec * (-Ld / 2 + 1.7 + i * 3.4)
                    M = trans(o.x, o.y, o.z) @ rot_z(ang)
                    pm.obox(M, 1.7, 11.0, zt - Z_STREET, "Paint_White", skip=("-z",))
    for side, v in lay.side_streets.items():
        if side in "NS":
            s = 1 if side == "N" else -1
            zebra_across_x(v - ST / 2, v + ST / 2, s * (HY + ST + SW + 9))
            zebra_across_y(s * HY if s > 0 else -HY - ST, s * HY + ST if s > 0 else -HY, v)
        else:
            s = 1 if side == "E" else -1
            zebra_across_y(v - ST / 2, v + ST / 2, s * (HX + ST + SW + 9))
            zebra_across_x(HX if s > 0 else -HX - ST, HX + ST if s > 0 else -HX, v)
    # dashed centre line around the ring (skipping intersections / crossings)
    def dashes_x(y, xa, xb):
        x = xa
        while x + 6 < xb:
            pm.box(x, y - 0.25, Z_STREET, x + 6, y + 0.25, zt, "Paint_White", skip=("-z",))
            x += 13

    def dashes_y(x, ya, yb):
        y = ya
        while y + 6 < yb:
            pm.box(x - 0.25, y, Z_STREET, x + 0.25, y + 6, zt, "Paint_White", skip=("-z",))
            y += 13
    for sy in (1, -1):
        dashes_x(sy * (HY + ST / 2), -HX + 20, HX - 20)
    for sx in (1, -1):
        dashes_y(sx * (HX + ST / 2), -HY + 20, HY - 20)
    pm.build("Street_Paint", col)


# ----------------------------------------------------------------------------------------------
# PRACTICE AREA
# ----------------------------------------------------------------------------------------------


def railing(mb, p0, p1, post_step=6.0, h=RAIL_H, glass=False):
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    L = d.length
    if L < 0.5:
        return
    n = max(1, int(math.ceil(L / post_step)))
    for i in range(n + 1):
        p = p0 + d * (i / n)
        mb.box(p.x - 0.2, p.y - 0.2, p.z, p.x + 0.2, p.y + 0.2, p.z + h, "Metal_Rail")
    up = Vector((0, 0, h))
    mb.beam(p0 + up, p1 + up, 0.5, 0.3, "Metal_Rail")
    mb.beam(p0 + up * 0.5, p1 + up * 0.5, 0.18, 0.18, "Metal_Rail")
    mb.beam(p0 + Vector((0, 0, 0.35)), p1 + Vector((0, 0, 0.35)), 0.18, 0.18, "Metal_Rail")


def straight_stair(mb, w, n, tread, z_top, riser, z_floor, nosing=True):
    """Local frame: x across [0,w], descends toward +y."""
    for j in range(n):
        top = z_top - (j + 1) * riser
        mb.box(0, j * tread, z_floor, w, (j + 1) * tread, top, "Concrete_Light", skip=("-z",))
        if nosing:
            mb.box(0, (j + 1) * tread - 0.6, top, w, (j + 1) * tread - 0.05, top + 0.04, "Grate_Metal", skip=("-z",))


def build_practice(lay, rng):
    PX, PY0, PY1 = lay.PX, lay.PY0, lay.PY1
    zf = -PIT_D
    T = WALL_T
    # ---- floor ----
    fl = MB()
    fl.box(-PX, PY1, zf - 4, PX, PY0, zf, "Practice_Floor", skip=("-z",))
    fl.build("Practice_Floor", COLL["FLOOR"], origin=(0, (PY0 + PY1) / 2, zf))
    tf = MB()
    for sgn in (1, -1):
        x0, x1 = sorted((sgn * PX, sgn * (PX + T + 3)))
        tf.box(x0, lay.TY0, zf - 4, x1, lay.TY1, zf, "Practice_Floor", skip=("-z",))
    tf.build("Practice_Floor_TrenchLinks", COLL["FLOOR"])
    fd = MB()
    bw = 5.0
    y_clean = PY0 - lay.STAIRZ
    for r in ((-PX, PY1, PX, PY1 + bw), (-PX, PY1 + bw, -PX + bw, y_clean), (PX - bw, PY1 + bw, PX, y_clean)):
        fd.box(*r[:2], zf, *r[2:], zf + 0.03, "Practice_FloorBorder", skip=("-z",))
    step = 30.0
    x = -PX + bw + step
    while x < PX - bw - 1:
        fd.box(x - 0.15, PY1 + bw, zf, x + 0.15, y_clean, zf + 0.03, "Joint_Dark", skip=("-z",))
        x += step
    y = PY1 + bw + step
    while y < y_clean - 1:
        fd.box(-PX + bw, y - 0.15, zf, PX - bw, y + 0.15, zf + 0.03, "Joint_Dark", skip=("-z",))
        y += step
    fd.build("Practice_Floor_Joints", COLL["FLOOR"])

    # ---- grand stair (north, inside the pit) + flanking seating terraces ----
    GW = 64.0
    st = MB()
    with st.xf(trans(-GW / 2, PY0, 0) @ Matrix.Scale(-1, 4, Vector((0, 1, 0)))):
        # local +y descends (mirrored so it runs toward world -y)
        straight_stair(st, GW, 7, 2.0, 0.0, 1.0, zf)
        st.box(0, 14, zf, GW, 26, -8.0, "Concrete_Light", skip=("-z",))
        st.box(0, 25.4, -8.0, GW, 25.95, -7.96, "HEX_Orange_LED", skip=("-z",))
        with st.xf(trans(0, 26, 0)):
            straight_stair(st, GW, 7, 2.0, -8.0, 1.0, zf)
    st.box(-GW / 2 - 3, PY0, -4, GW / 2 + 3, PY0 + T, 0, "Ground_ConcreteLight", skip=("-z",))
    st.build("Stair_Grand_North", COLL["STAIRS"], origin=(0, PY0 - 20, zf))
    ch = MB()
    prof = [(PY0, zf), (PY0, 1.0), (PY0 - 14, -6.0), (PY0 - 26, -6.0), (PY0 - 40, zf + 1.0), (PY0 - 40, zf)]
    rl = MB()
    for sgn in (1, -1):
        x0, x1 = sorted((sgn * GW / 2, sgn * (GW / 2 + 3)))
        ch.extrude_x(prof, x0, x1, "Concrete_Wall")
        xc = sgn * (GW / 2 + 1.5)
        pts = [(xc, PY0 + T, 1.0), (xc, PY0, 1.0), (xc, PY0 - 14, -6.0), (xc, PY0 - 26, -6.0), (xc, PY0 - 40, zf + 1)]
        for a, b in zip(pts[:-1], pts[1:]):
            railing(rl, a, b, post_step=7)
    ch.build("Stair_Grand_CheekWalls", COLL["RETAINING_WALLS"])
    te = MB()
    for sgn in (1, -1):
        x0, x1 = sorted((sgn * (GW / 2 + 3), sgn * 112))
        for k in range(8):
            ya, yb = PY0 - 40 + 5 * k, PY0 - 40 + 5 * (k + 1)
            top = zf + 1.75 * (k + 1)
            te.box(x0, ya, zf, x1, yb, top, "Concrete_Light", skip=("-z",))
            te.box(x0, ya, top, x1, ya + 0.5, top + 0.04, "Grate_Metal", skip=("-z",))
    te.build("Practice_SeatingTerraces", COLL["STAIRS"])

    # ---- side stairs in trenches (east + west) ----
    for sgn, tag in ((1, "East"), (-1, "West")):
        sm = MB()
        xs = PX + T + lay.TRUN  # top of stair (plaza level)
        # local frame: x across trench (TY0->TY1), +y = descending toward the pit
        if sgn > 0:
            M = frame_matrix((xs, lay.TY1, 0), (0, -1), (-1, 0))
        else:
            M = frame_matrix((-xs, lay.TY0, 0), (0, 1), (1, 0))
        with sm.xf(M):
            straight_stair(sm, lay.TW, 15, 2.5, 0.0, 1.0, zf)
        sm.build("Stair_%s" % tag, COLL["STAIRS"])

    # ---- retaining walls ----
    wm = MB()
    gx = GW / 2 + 3
    walls = [
        (-PX - T, PY0, -gx, PY0 + T), (gx, PY0, PX + T, PY0 + T),          # north (gap = grand stair)
        (-PX - T, PY1 - T, PX + T, PY1),                                   # south
    ]
    for sgn in (1, -1):
        x0, x1 = sorted((sgn * PX, sgn * (PX + T)))
        walls.append((x0, PY1 - T, x1, lay.TY0))
        walls.append((x0, lay.TY1, x1, PY0 + T))
        tx0, tx1 = sorted((sgn * (PX + T), sgn * (PX + T + lay.TRUN)))
        walls.append((tx0, lay.TY0 - T, tx1, lay.TY0))
        walls.append((tx0, lay.TY1, tx1, lay.TY1 + T))
    for (x0, y0, x1, y1) in walls:
        wm.box(x0, y0, zf - 4, x1, y1, 0, "Concrete_Wall", skip=("-z",))
        wm.box(x0 - 0.3, y0 - 0.3, 0, x1 + 0.3, y1 + 0.3, 0.6, "Concrete_Light")
    # inner-face accents: pilasters, navy base band, orange LED line
    acc = MB()

    def inner_face(x0, y0, x1, y1, nrm, skip_world_x=None):
        # (x0,y0)-(x1,y1) is a wall face line; nrm = (nx,ny) points into the pit
        nx, ny = nrm
        L = math.hypot(x1 - x0, y1 - y0)
        ax, ay = (x1 - x0) / L, (y1 - y0) / L
        # frame: a along face, t inward to wall => t axis = -nrm
        M = frame_matrix((x0, y0, 0), (ax, ay), (-nx, -ny))
        if M.to_3x3().determinant() < 0:
            M = frame_matrix((x1, y1, 0), (-ax, -ay), (-nx, -ny))
        with acc.xf(M):
            acc.box(0, -0.12, zf, L, 0, zf + 1.4, "HEX_Navy", skip=("+y",))
            acc.box(0, -0.2, -1.7, L, 0, -1.4, "HEX_Orange_LED", skip=("+y",))
            n = int(L / 30)
            for i in range(1, n + 1):
                a = L * i / (n + 1)
                wx = (M @ Vector((a, 0, 0))).x
                if skip_world_x and skip_world_x[0] < wx < skip_world_x[1]:
                    continue
                acc.box(a - 1.3, -1.2, zf, a + 1.3, 0, 0, "Concrete_Wall", skip=("+y",))
    inner_face(-PX, PY1, PX, PY1, (0, 1), skip_world_x=(-82, 82))
    for sgn in (1, -1):
        inner_face(sgn * PX, PY1, sgn * PX, lay.TY0, (-sgn, 0))
        inner_face(sgn * PX, lay.TY1, sgn * PX, PY0, (-sgn, 0))
        inner_face(sgn * 112, PY0, sgn * PX, PY0, (0, -1))
    wm.build("Practice_RetainingWalls", COLL["RETAINING_WALLS"])
    acc.build("Practice_WallAccents", COLL["RETAINING_WALLS"])
    # HEX feature wall (hexagon relief) on the south wall
    hx = MB()
    r = 2.6
    cols = int(150 / (1.5 * r))
    M = Matrix(((1, 0, 0, 0), (0, 0, 1, PY1), (0, 1, 0, 0), (0, 0, 0, 1)))  # local z -> world +y
    with hx.xf(M):
        for c in range(cols):
            x = -75 + c * 1.5 * r
            off = (math.sqrt(3) / 2 * r) if c % 2 else 0
            for rw in range(3):
                zc = -12.0 + off + rw * math.sqrt(3) * r
                if zc > -3.5:
                    continue
                depth = 0.25 + 0.35 * rng.random()
                m = "Concrete_Light"
                rr = rng.random()
                if rr < 0.12:
                    m = "HEX_Navy"
                elif rr < 0.16:
                    m = "HEX_Orange_LED"
                hx.cyl(x, zc, r * 0.9, 0, depth, 6, m)
    hx.build("Practice_HexFeatureWall", COLL["RETAINING_WALLS"])

    # ---- railings ----
    zr = 0.6
    for (x0, y0, x1, y1) in walls:
        if abs(x1 - x0) > abs(y1 - y0):
            yc = (y0 + y1) / 2
            railing(rl, (x0 + 0.5, yc, zr), (x1 - 0.5, yc, zr))
        else:
            xc = (x0 + x1) / 2
            railing(rl, (xc, y0 + 0.5, zr), (xc, y1 - 0.5, zr))
    rl.build("Practice_Railings", COLL["RAILINGS"])

    # ---- practice-area light masts ----
    for i, (x, y, rz) in enumerate(((-PX - 9, PY0 + 9, math.pi), (PX + 9, PY0 + 9, math.pi),
                                     (-PX - 9, PY1 - 9, 0), (PX + 9, PY1 - 9, 0))):
        instance("LightMast_Practice", (x, y, 0), rz, COLL["STRUCTURES"], name="LightMast_Practice_%02d" % (i + 1))


# ----------------------------------------------------------------------------------------------
# CITY
# ----------------------------------------------------------------------------------------------
# ----------------------------------------------------------------------------------------------
# TOKYO / SHIBUYA ARCHITECTURE KIT  (foreground + midground city wall)
#   local building frame: x along the street [0,w], y = depth [0,d], front face y=0 faces -y.
#   "face frames": a along a facade, t<0 = outward (toward the street), z up.
# ----------------------------------------------------------------------------------------------
TEX_DIR = os.path.join(SCRIPT_DIR, "textures")
ATLAS = {"H": (4, 16, "signs_h.png"), "V": (16, 4, "signs_v.png"), "S": (4, 4, "screens.png")}
CELLS = {"shop": ("H", range(0, 44)), "name": ("H", range(44, 56)), "misc": ("H", range(56, 64)),
         "vshop": ("V", range(0, 32)), "vstack": ("V", range(32, 48)), "vdir": ("V", range(48, 56)),
         "vneon": ("V", range(56, 64)), "ad": ("S", range(0, 16))}
ATLAS_MATS = {"SignH": ("H", 0.0), "SignH_Lit": ("H", 1.5), "SignV": ("V", 0.0), "SignV_Lit": ("V", 1.5),
              "Screen_Atlas": ("S", 2.2)}
SIGN_COLL = {"Storefront": "SIGNS_STOREFRONT", "Directory": "SIGNS_STOREFRONT", "Floor": "SIGNS_FLOOR",
             "Blade": "SIGNS_BLADE_VERTICAL", "Vertical": "SIGNS_BLADE_VERTICAL", "Rooftop": "SIGNS_ROOFTOP",
             "BuildingName": "SIGNS_BUILDING_NAME"}
SCREEN_COLL = {"Landmark": "SCREENS_LANDMARK", "Large": "SCREENS_LARGE", "Medium": "SCREENS_MEDIUM",
               "Small": "SCREENS_SMALL"}
OBJ_COUNT = {}
SCREENS = []
SIGNS = []


def atlas_mat(name):
    atlas, strength = ATLAS_MATS[name]
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    nt = m.node_tree
    p = _principled(m)
    p.inputs["Roughness"].default_value = 0.45 if atlas != "S" else 0.25
    path = os.path.join(TEX_DIR, ATLAS[atlas][2])
    if os.path.exists(path):
        img = bpy.data.images.load(path, check_existing=True)
        tx = nt.nodes.new("ShaderNodeTexImage")
        tx.image = img
        nt.links.new(tx.outputs["Color"], p.inputs["Base Color"])
        if strength > 0:
            nt.links.new(tx.outputs["Color"], p.inputs["Emission Color"])
            p.inputs["Emission Strength"].default_value = strength
    else:
        p.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1)
        if strength > 0:
            p.inputs["Emission Color"].default_value = (1, 1, 1, 1)
            p.inputs["Emission Strength"].default_value = strength * 0.5
    m.diffuse_color = (0.85, 0.8, 0.75, 1)
    return m


def _next_name(prefix):
    OBJ_COUNT[prefix] = OBJ_COUNT.get(prefix, 0) + 1
    return "%s_%03d" % (prefix, OBJ_COUNT[prefix])


def atlas_uv(atlas, cell):
    nc, nr, _ = ATLAS[atlas]
    col, row = cell % nc, cell // nc
    e = 0.0015
    return (col / nc + e, 1 - (row + 1) / nr + e, (col + 1) / nc - e, 1 - row / nr - e)


def board_object(name, coll, center, facing, w, h, t, mat, atlas, cell, two=False):
    """Thin box whose +Y face (and -Y if two-sided) shows one atlas cell. Object rotated so +Y == facing."""
    u0, v0, u1, v1 = atlas_uv(atlas, cell)
    x, y, z = w / 2, t / 2, h / 2
    V = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    full = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    cc = [(u0 + 0.002, v0 + 0.002)] * 4
    F = [((2, 3, 7, 6), full), ((0, 1, 5, 4), full if two else cc), ((0, 3, 2, 1), cc), ((4, 5, 6, 7), cc),
         ((0, 4, 7, 3), cc), ((1, 2, 6, 5), cc)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(V, [], [f for f, _ in F])
    me.materials.append(get_mat(mat))
    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", [c for _, uvs in F for uv in uvs for c in uv])
    me.update()
    ob = bpy.data.objects.new(name, me)
    COLL[coll].objects.link(ob)
    f = Vector((facing[0], facing[1], 0)).normalized()
    ob.location = center
    ob.rotation_euler = (0, 0, math.atan2(-f.x, f.y))
    return ob


def curved_screen(name, coll, M, cx, cy, r, a0, a1, z0, z1, cell, seg=14):
    """Curved display (arc around (cx,cy) in building-local coords). UV runs 0..1 across the arc."""
    u0, v0, u1, v1 = atlas_uv("S", cell)
    verts, faces, uvs = [], [], []
    ro, ri = r + 0.25, r - 0.25
    wc = M @ Vector((cx + r * math.cos((a0 + a1) / 2), cy + r * math.sin((a0 + a1) / 2), (z0 + z1) / 2))
    flip = M.to_3x3().determinant() < 0

    def P(rad, ang, z):
        v = M @ Vector((cx + rad * math.cos(ang), cy + rad * math.sin(ang), z))
        verts.append(tuple(v - wc))
        return len(verts) - 1

    def add(idx, uv):
        if flip:
            idx, uv = idx[::-1], uv[::-1]
        faces.append(idx)
        uvs.extend(uv)
    cc = [(u0 + 0.002, v0 + 0.002)] * 4
    for i in range(seg):
        A, B = a0 + (a1 - a0) * i / seg, a0 + (a1 - a0) * (i + 1) / seg
        ua, ub = u0 + (u1 - u0) * i / seg, u0 + (u1 - u0) * (i + 1) / seg
        add([P(ro, A, z0), P(ro, B, z0), P(ro, B, z1), P(ro, A, z1)], [(ua, v0), (ub, v0), (ub, v1), (ua, v1)])
        add([P(ri, B, z0), P(ri, A, z0), P(ri, A, z1), P(ri, B, z1)], cc)
        add([P(ri, A, z1), P(ro, A, z1), P(ro, B, z1), P(ri, B, z1)], cc)
        add([P(ri, B, z0), P(ro, B, z0), P(ro, A, z0), P(ri, A, z0)], cc)
    add([P(ri, a0, z0), P(ro, a0, z0), P(ro, a0, z1), P(ri, a0, z1)][::-1], cc)
    add([P(ro, a1, z0), P(ri, a1, z0), P(ri, a1, z1), P(ro, a1, z1)][::-1], cc)
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.materials.append(get_mat("Screen_Atlas"))
    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", [c for uv in uvs for c in uv])
    me.update()
    ob = bpy.data.objects.new(name, me)
    COLL[coll].objects.link(ob)
    ob.location = wc
    ob["display"] = "curved: UV 0..1 across the arc (use a texture / flipbook; SurfaceGui needs a flat part)"
    SCREENS.append(ob)
    return ob


def pick(rng, table):
    tot = sum(w for _, w in table)
    r = rng.uniform(0, tot)
    for k, w in table:
        r -= w
        if r <= 0:
            return k
    return table[-1][0]


class Bld:
    """One building: architecture mesh + LED mesh + requests for separate signs/screens/instances."""

    def __init__(self, name, M, rng, wall):
        self.name, self.M, self.rng, self.wall = name, M, rng, wall
        self.mb, self.led = MB(), MB()
        self.mb.M = M.copy()
        self.led.M = M.copy()
        self.F = Matrix.Identity(4)
        self._fs = []
        self.signs, self.screens, self.inst, self.roofs, self.curved = [], [], [], [], []
        self.H = 0.0
        self.roof_mat = rng.choice(["Roof_Gray", "Roof_Dark"])

    @contextmanager
    def at(self, F):
        self._fs.append(self.F)
        self.F = self.F @ F
        try:
            with self.mb.xf(F), self.led.xf(F):
                yield
        finally:
            self.F = self._fs.pop()

    def sign(self, cat, a, t, z, w, h, thick, cells, lit, facing=(0, -1, 0), two=False):
        atlas, rngc = CELLS[cells]
        cell = self.rng.choice(list(rngc))
        c = self.F @ Vector((a, t, z))
        f = self.F.to_3x3() @ Vector(facing)
        self.signs.append((cat, c, f, w, h, thick, atlas, cell, lit, two))

    def screen(self, cat, a, z, w, h, base_t=0.0, frame=True):
        if frame:
            self.mb.box(a - w / 2 - 0.7, base_t - 0.8, z - h / 2 - 0.7, a + w / 2 + 0.7, max(base_t, 0.0) + 0.01,
                        z + h / 2 + 0.7, "DarkMetal", skip=("+y",))
        c = self.F @ Vector((a, base_t - 0.8 - 0.26, z))
        f = self.F.to_3x3() @ Vector((0, -1, 0))
        self.screens.append((cat, c, f, w, h))

    def put(self, kind, a, t, z, facing=(0, -1, 0)):
        p = self.F @ Vector((a, t, z))
        f = self.F.to_3x3() @ Vector(facing)
        self.inst.append((kind, p, f))

    # -------------------------------------------------------------------------------------------
    def finish(self, coll, origin_local, eq_density=1.0):
        M = self.M
        R3 = M.to_3x3()
        o = M @ Vector(origin_local)
        ob = self.mb.build(self.name, coll, origin=(o.x, o.y, 0.0))
        ob["height_studs"] = round(self.H, 1)
        if not self.led.empty():
            self.led.build(self.name + "_LED", COLL["LED_STRIPS"], origin=(o.x, o.y, 0.0))
        for (cat, c, f, w, h, thick, atlas, cell, lit, two) in self.signs:
            fw = R3 @ f
            if atlas == "H":
                mat = "SignH_Lit" if lit else "SignH"
            else:
                mat = "SignV_Lit" if lit else "SignV"
            SIGNS.append(board_object(_next_name("Sign_" + cat), SIGN_COLL[cat], M @ c, fw, w, h, thick, mat,
                                      atlas, cell, two))
        for (cat, c, f, w, h) in self.screens:
            if cat is None:
                cat = "Landmark" if w >= 40 else "Large" if w >= 22 else "Medium" if w >= 7 else "Small"
            ob_s = board_object(_next_name("Billboard_" + cat), SCREEN_COLL[cat], M @ c, R3 @ f, w, h, 0.5,
                                "Screen_Atlas", "S", self.rng.randrange(16))
            ob_s["display_face"] = "local +Y (Roblox: Front)"
            SCREENS.append(ob_s)
        for (cx, cy, r, a0, a1, z0, z1) in self.curved:
            curved_screen(_next_name("Billboard_LandmarkCurved"), "SCREENS_LANDMARK", M, cx, cy, r, a0, a1, z0, z1,
                          self.rng.randrange(16))
        for (kind, p, f) in self.inst:
            fw = R3 @ f
            instance(kind, M @ p, math.atan2(fw.x, -fw.y), coll, parent=ob)
        rng = self.rng
        for (rect, z, blocked) in self.roofs:
            x0, y0, x1, y1 = rect
            if x1 - x0 < 6 or y1 - y0 < 6:
                continue
            area = (x1 - x0) * (y1 - y0)
            n = int(min(8, area / 230.0) * eq_density) + (1 if rng.random() < 0.6 else 0)
            kinds = ["HVAC_S", "HVAC_S", "HVAC_S", "HVAC_L", "Vent", "Vent", "CoolingTower", "SatDish"]
            taken = list(blocked)
            for _ in range(n):
                kind = rng.choice(kinds)
                fw_, fd_ = EQUIP_FOOT[kind]
                rot = rng.choice((0, 1))
                if rot:
                    fw_, fd_ = fd_, fw_
                for _try in range(12):
                    px = rng.uniform(x0 + fw_ / 2, max(x0 + fw_ / 2, x1 - fw_ / 2))
                    py = rng.uniform(y0 + fd_ / 2, max(y0 + fd_ / 2, y1 - fd_ / 2))
                    r = (px - fw_ / 2 - 0.7, py - fd_ / 2 - 0.7, px + fw_ / 2 + 0.7, py + fd_ / 2 + 0.7)
                    if r[0] < x0 - 0.5 or r[2] > x1 + 0.5 or r[1] < y0 - 0.5 or r[3] > y1 + 0.5:
                        continue
                    if any(not (r[2] <= b_[0] or r[0] >= b_[2] or r[3] <= b_[1] or r[1] >= b_[3]) for b_ in taken):
                        continue
                    taken.append(r)
                    yaw = math.atan2(R3[1][0], R3[0][0]) + (math.pi / 2 if rot else 0)
                    instance(kind, M @ Vector((px, py, z)), yaw, coll, parent=ob)
                    break
        return ob


# ---------------------------------------------------------------------------------------------
# materials / palettes for the kit
# ---------------------------------------------------------------------------------------------
WALL_TILES = ["Tile_White", "Beige_Tile", "Tile_Gray", "OffWhite", "Tile_Brown", "Concrete_Exposed",
              "Concrete_Light", "Charcoal", "Stucco_Old", "Tile_White", "Tile_Gray"]
NOREN = ["Noren_Navy", "Noren_Red", "Noren_White"]
STRIPES = [("Stripe_A", "Stripe_B", "OffWhite"), ("Stripe_C", "OffWhite", "Stripe_B"),
           ("Stripe_D", "Stripe_A", "OffWhite"), ("Stripe_B", "Stripe_C", "OffWhite")]
UNIT_W = {"shop": (7, 12), "konbini": (16, 21), "resto": (7, 10), "arcade": (14, 22), "service": (6, 8.5),
          "lobby": (7, 9.5), "vending": (8, 11)}
MIX_ZAKKYO = [("shop", 5), ("resto", 3), ("service", 1), ("vending", 1), ("lobby", 1)]
MIX_MODERN = [("shop", 6), ("lobby", 1), ("resto", 1)]
MIX_LOW = [("konbini", 3), ("shop", 3), ("resto", 2), ("vending", 1)]
MIX_SIDE = [("shop", 3), ("resto", 2), ("service", 2), ("vending", 2)]


# ---------------------------------------------------------------------------------------------
# street level (shallow facade illusions of businesses)
# ---------------------------------------------------------------------------------------------


def fascia_sign(b, a0, a1, SB, lit=None, cells="shop", frac=0.9):
    w = (a1 - a0) * frac
    if w < 2.5:
        return
    h = 3.0
    lit = b.rng.random() < 0.55 if lit is None else lit
    zc = SF_TOP + 0.55 + h / 2
    t = min(0.0, SB - 0.3) - 0.3
    b.mb.box(a0, t + 0.25, zc - h / 2 - 0.25, a1, SB, zc + h / 2 + 0.25, "Charcoal", skip=("+y",))
    b.sign("Storefront", (a0 + a1) / 2, t, zc, w, h, 0.5, cells, lit)


def blade(b, a, z0, z1, proj=3.0, cells="vshop", lit=None, cat="Blade", off=1.0):
    """Projecting two-sided vertical sign (perpendicular to the facade) on brackets."""
    h = z1 - z0
    if h < 3:
        return
    z = z0 + 0.6
    while z < z1 - 0.6:
        b.mb.box(a - 0.2, -off - 0.1, z, a + 0.2, 0, z + 0.4, "DarkMetal", skip=("+y",))
        z += 7.0
    b.mb.box(a - 0.2, -off - 0.1, z1 - 1.0, a + 0.2, 0, z1 - 0.6, "DarkMetal", skip=("+y",))
    lit = b.rng.random() < 0.6 if lit is None else lit
    b.sign(cat, a, -off - proj / 2, (z0 + z1) / 2, proj, h, 0.7, cells, lit, facing=(1, 0, 0), two=True)


def u_shop(b, a0, a1, SB):
    mb, rng = b.mb, b.rng
    W = a1 - a0
    rec = W > 8 and SB < 1.0 and rng.random() < 0.7
    tg = SB - 1.6 if rec else SB - 0.1
    dc = a0 + W * rng.choice((0.3, 0.5, 0.7))
    dc = min(max(dc, a0 + DOOR_W / 2 + 0.5), a1 - DOOR_W / 2 - 0.5)
    d0, d1 = dc - DOOR_W / 2, dc + DOOR_W / 2
    mb.box(a0, tg - 0.2, Z0, a1, SB, 0.45, "Concrete_Dark", skip=("+y",))
    for g0, g1 in ((a0, d0 - 0.2), (d1 + 0.2, a1)):
        if g1 - g0 > 0.3:
            mb.box(g0, tg - 0.1, 0.45, g1, tg, SF_TOP, "Glass_Store")
            k = max(1, int((g1 - g0) / 3.4))
            for j in range(1, k):
                m = g0 + (g1 - g0) * j / k
                mb.box(m - 0.12, tg - 0.25, 0.45, m + 0.12, tg, SF_TOP, "DarkMetal")
    if rec:
        for x in (d0 - 0.2, d1 + 0.2):
            mb.box(x - 0.1, tg, 0.0, x + 0.1, SB, DOOR_H + 0.3, "Glass_Store")
        mb.box(d0 - 0.2, tg - 0.1, DOOR_H + 0.3, d1 + 0.2, tg, SF_TOP, "Glass_Store")
        mb.box(d0 - 0.2, tg, DOOR_H + 0.3, d1 + 0.2, SB, DOOR_H + 0.5, "Soffit")
        mb.box(d0, SB - 0.3, 0, d1, SB - 0.05, DOOR_H, "Door_Glass")
        b.led.box(dc - 0.7, (tg + SB) / 2 - 0.4, DOOR_H + 0.22, dc + 0.7, (tg + SB) / 2 + 0.4, DOOR_H + 0.3,
                  "Downlight")
    else:
        mb.box(d0, tg - 0.25, 0, d1, tg - 0.05, DOOR_H, "Door_Glass")
        b.led.box(dc - 0.6, tg - 0.6, DOOR_H + 0.4, dc + 0.6, tg - 0.25, DOOR_H + 0.7, "Lamp_Warm")
    mb.box(a0, tg - 0.3, SF_TOP - 0.45, a1, tg, SF_TOP, "DarkMetal")
    fascia_sign(b, a0, a1, SB)
    r = rng.random()
    if r < 0.3 and SB < 1.0:
        depth = rng.uniform(2.4, 3.6)
        M = trans((a0 + a1) / 2, -depth / 2 + SB - 0.3, SF_TOP - 0.4) @ Matrix.Rotation(math.radians(-20), 4, "X")
        mb.obox(M, a1 - a0 - 0.3, depth + 0.4, 0.2, rng.choice(AWNINGS))
    if rng.random() < 0.25:
        b.sign("Storefront", d1 + 1.4 if d1 + 2.4 < a1 else d0 - 1.4, min(SB, 0.0) - 1.2, 2.5, 1.5, 4.4, 0.35, "vshop",
               False)


def u_konbini(b, a0, a1, SB):
    mb, rng = b.mb, b.rng
    mb.box(a0, SB - 0.15, 0.3, a1, SB, SF_TOP, "Glass_Konbini")
    k = max(2, int((a1 - a0) / 4))
    for j in range(k + 1):
        m = a0 + (a1 - a0) * j / k
        mb.box(m - 0.15, SB - 0.35, 0, m + 0.15, SB, SF_TOP, "Aluminum")
    dc = (a0 + a1) / 2
    mb.box(dc - 3.4, SB - 0.45, 0, dc + 3.4, SB - 0.3, DOOR_H + 0.2, "Door_Glass")
    mb.box(dc - 3.6, SB - 0.6, DOOR_H + 0.2, dc + 3.6, SB - 0.2, DOOR_H + 0.9, "Aluminum")
    st = rng.choice(STRIPES)
    t = min(0.0, SB - 0.3) - 0.35
    mb.box(a0, t + 0.3, SF_TOP, a1, SB, GF_H - 0.3, "OffWhite", skip=("+y",))
    for i, sm in enumerate(st):
        z0 = SF_TOP + 0.3 + i * 1.25
        mb.box(a0, t, z0, a1, t + 0.32, z0 + 1.15, sm, skip=("+y",))
    b.sign("Storefront", a1 - 3.4, t - 0.25, SF_TOP + 2.2, 5.6, 2.8, 0.4, "misc", True)
    b.led.box(a0, SB - 0.6, SF_TOP - 0.2, a1, SB - 0.35, SF_TOP, "LED_White", skip=("+y",))


def u_resto(b, a0, a1, SB):
    mb, rng = b.mb, b.rng
    tw = SB - 0.35
    mb.box(a0, tw, 0, a1, SB, SF_TOP, "Wood_Dark", skip=("+y",))
    for z in (2.0, 4.5, 7.0, 9.0):
        mb.box(a0, tw - 0.12, z, a1, tw, z + 0.15, "Charcoal", skip=("+y",))
    dc = a1 - DOOR_W / 2 - 0.7
    mb.box(a0 + 0.7, tw - 0.15, 3.2, dc - DOOR_W / 2 - 0.8, tw, 7.6, "Glass_Store")
    mb.box(dc - DOOR_W / 2, tw - 0.2, 0, dc + DOOR_W / 2, tw - 0.05, DOOR_H - 0.3, "Door_Dark")
    mb.box(dc - DOOR_W / 2 - 0.4, tw - 0.75, DOOR_H - 2.4, dc + DOOR_W / 2 + 0.4, tw - 0.6, DOOR_H + 0.2,
           rng.choice(NOREN))
    mb.box(dc - DOOR_W / 2 - 0.5, tw - 0.8, DOOR_H + 0.2, dc + DOOR_W / 2 + 0.5, tw, DOOR_H + 0.45, "Wood_Dark")
    lx = dc - DOOR_W / 2 - 1.3
    if lx > a0 + 0.8:
        b.mb.box(lx - 0.1, tw - 1.2, 8.6, lx + 0.1, tw, 8.8, "DarkMetal")
        b.led.cyl(lx, tw - 1.1, 0.8, 6.6, 8.5, 8, "Lantern_Red", bottom=True)
    if rng.random() < 0.55:
        b.sign("Storefront", a0 + 1.2, tw - 1.4, 2.6, 1.6, 4.6, 0.35, "vshop", True)
    fascia_sign(b, a0, a1, SB, cells="shop")


def u_arcade(b, a0, a1, SB):
    mb, rng = b.mb, b.rng
    mb.box(a0, SB - 0.1, 0, a1, SB, SF_TOP + 0.4, "Interior_Lit", skip=("+y",))
    mb.box(a0 + 1.5, SB - 2.5, 0, a0 + 4.5, SB - 0.1, 5.8, "Metal_Panel")
    mb.box(a1 - 4.5, SB - 2.5, 0, a1 - 1.5, SB - 0.1, 5.8, "Metal_Panel")
    t = min(SB, 1.0) - 0.5
    led = rng.choice(["Bulb_Strip", "LED_Magenta", "LED_Cyan"])
    b.led.box(a0, t - 0.3, 0, a0 + 0.45, t, SF_TOP + 0.6, led)
    b.led.box(a1 - 0.45, t - 0.3, 0, a1, t, SF_TOP + 0.6, led)
    b.led.box(a0, t - 0.3, SF_TOP + 0.15, a1, t, SF_TOP + 0.6, led)
    if a1 - a0 > 12:
        b.screen("Small", a0 + 3.0, 8.0, 3.4, 4.6, base_t=SB - 2.6 if SB > 2.6 else 0.0)
        b.screen("Small", a1 - 3.0, 8.0, 3.4, 4.6, base_t=SB - 2.6 if SB > 2.6 else 0.0)
    fascia_sign(b, a0, a1, SB, lit=True, cells="misc" if rng.random() < 0.5 else "shop")


def u_service(b, a0, a1, SB):
    mb, rng = b.mb, b.rng
    top = min(DOOR_H + 1.4, SF_TOP - 0.6)
    mb.box(a0, SB - 0.25, 0, a1, SB, top, "Shutter", skip=("+y",))
    z = 0.8
    while z < top - 0.3:
        mb.box(a0, SB - 0.32, z, a1, SB - 0.25, z + 0.12, "Charcoal", skip=("+y",))
        z += 0.9
    mb.box(a0, SB - 0.8, top, a1, SB, top + 1.0, "Shutter", skip=("+y",))
    mb.box(a0, SB - 0.1, top + 1.0, a1, SB, SF_TOP, b.wall, skip=("+y",))
    mb.cyl(a1 - 0.6, SB - 0.6, 0.3, Z0, GF_H, 6, "Pipe_Gray")
    mb.box(a0 + 0.5, SB - 0.7, 3.0, a0 + 1.9, SB - 0.25, 5.0, "AC_Unit")
    if rng.random() < 0.4:
        fascia_sign(b, a0, a1, SB, lit=False)


def u_lobby(b, a0, a1, SB):
    mb, rng = b.mb, b.rng
    dc = (a0 + a1) / 2
    mb.box(a0, SB - 0.12, 0.2, a1, SB, SF_TOP, "Glass_Store")
    mb.box(dc - 2.6, SB - 0.35, 0, dc + 2.6, SB - 0.15, DOOR_H, "Door_Glass")
    for x in (dc - 2.7, dc, dc + 2.7):
        mb.box(x - 0.14, SB - 0.4, 0, x + 0.14, SB - 0.1, DOOR_H + 0.2, "Aluminum")
    mb.box(dc - 2.8, SB - 0.4, DOOR_H, dc + 2.8, SB - 0.1, DOOR_H + 0.35, "Aluminum")
    if SB < 1.5:
        mb.box(a0 - 0.6, -2.8, SF_TOP - 0.7, a1 + 0.6, SB, SF_TOP - 0.15, "DarkMetal")
        b.led.box(a0, -2.5, SF_TOP - 0.78, a1, -2.2, SF_TOP - 0.7, "Downlight", skip=("+z",))
    if a1 - a0 > 7.5:
        b.sign("Directory", a0 + 1.3, SB - 0.45, 3.6, 1.9, 5.2, 0.3, "vdir", True)
    b.sign("BuildingName", dc, min(0.0, SB - 0.3) - 0.3, SF_TOP + 2.0, min(a1 - a0 - 0.6, 8.5), 2.1, 0.3, "name",
           rng.random() < 0.4)


def u_vending(b, a0, a1, SB):
    mb, rng = b.mb, b.rng
    mb.box(a0, SB - 0.15, 0, a1, SB, SF_TOP, "Tile_Gray", skip=("+y",))
    n = max(1, int((a1 - a0 - 0.6) / 3.6))
    x = (a0 + a1) / 2 - (n - 1) * 3.6 / 2
    for i in range(n):
        b.put(rng.choice(["Vending_Drink", "Vending_Drink", "Vending_Red"]), x + i * 3.6, SB - 0.15 - 1.25, 0.0)
    b.led.box((a0 + a1) / 2 - 1.5, SB - 0.6, SF_TOP - 0.5, (a0 + a1) / 2 + 1.5, SB - 0.15, SF_TOP - 0.3,
              "Lamp_Warm")
    if rng.random() < 0.5:
        fascia_sign(b, a0, a1, SB, cells="misc")


UNITS = {"shop": u_shop, "konbini": u_konbini, "resto": u_resto, "arcade": u_arcade, "service": u_service,
         "lobby": u_lobby, "vending": u_vending}


def street_level(b, L, SB, mix, lobby=True):
    rng = b.rng
    units = []
    pos = 0.0
    while L - pos > 0.5:
        k = pick(rng, mix)
        lo, hi = UNIT_W[k]
        if L - pos < lo:
            if units:
                units[-1][2] = L
            else:
                units.append(["shop", pos, L])
            break
        w = rng.uniform(lo, hi)
        if L - pos - w < 6:
            w = L - pos
        units.append([k, pos, pos + w])
        pos += w
    if lobby and len(units) >= 2 and not any(u[0] == "lobby" for u in units):
        i = rng.randrange(len(units))
        if units[i][2] - units[i][1] < 13:
            units[i][0] = "lobby"
    pm = rng.choice(["Concrete_Dark", "Charcoal", b.wall])
    bounds = [units[0][1]] + [u[2] for u in units]
    for bd in bounds:
        a0, a1 = max(0.0, bd - 0.6), min(L, bd + 0.6)
        b.mb.box(a0, -0.25, Z0, a1, SB + 0.01, GF_H, pm, skip=("+y",))
    if SB > 1.0:
        a = 3.0
        while a < L - 2:
            b.led.box(a - 0.45, SB / 2 - 0.45, GF_H - 0.08, a + 0.45, SB / 2 + 0.45, GF_H - 0.01, "Downlight",
                      skip=("+z",))
            a += 6.0
    for k, u0, u1 in units:
        UNITS[k](b, u0 + 0.6, u1 - 0.6, SB)
    return units


# ---------------------------------------------------------------------------------------------
# upper-floor facade modules
# ---------------------------------------------------------------------------------------------


def fac_punched(b, L, zs, ze, fh, n, win_w, win_h, sill, frame="DarkMetal", sill_mat="Concrete_Light",
                ac=0.12, sticker=0.12, glass="Glass_Dark", mull=True):
    mb, rng = b.mb, b.rng
    if n <= 0 or win_w < 1.2:
        return
    sp = L / n
    nfl = int((ze - zs) / fh + 0.01)
    for k in range(nfl):
        zf = zs + k * fh
        wb, wt = zf + sill, zf + sill + win_h
        if wt > ze - 0.4:
            break
        for i in range(n):
            c = sp * (i + 0.5)
            x0, x1 = c - win_w / 2, c + win_w / 2
            mb.box(x0, -0.06, wb, x1, 0, wt, glass, skip=("+y",))
            mb.box(x0 - 0.3, -0.35, wb, x0, 0, wt, frame, skip=("+y",))
            mb.box(x1, -0.35, wb, x1 + 0.3, 0, wt, frame, skip=("+y",))
            mb.box(x0 - 0.3, -0.35, wt, x1 + 0.3, 0, wt + 0.3, frame, skip=("+y",))
            mb.box(x0 - 0.45, -0.6, wb - 0.3, x1 + 0.45, 0, wb, sill_mat, skip=("+y",))
            if mull and win_w > 4.2:
                mb.box(c - 0.12, -0.25, wb, c + 0.12, 0, wt, frame, skip=("+y",))
            if rng.random() < ac:
                ax = x1 + 1.6 if x1 + 3.0 < sp * (i + 1) else c
                mb.box(ax - 1.2, -1.5, wb - 2.2, ax + 1.2, -0.2, wb - 0.5, "AC_Unit")
                mb.box(ax - 1.3, -1.6, wb - 2.4, ax + 1.3, 0, wb - 2.2, "DarkMetal", skip=("+y",))
            if rng.random() < sticker:
                hh = min(2.2, win_h * 0.36)
                b.sign("Floor", c, -0.12, wt - 0.3 - hh / 2, win_w * 0.92, hh, 0.12, "shop", False)


def floor_signs(b, L, zs, fh, nfl, sill, p=0.3, t=-0.45, lit_p=0.45):
    rng = b.rng
    for k in range(nfl):
        if rng.random() > p:
            continue
        zf = zs + k * fh
        h = max(1.4, sill - 0.9)
        w = rng.uniform(0.45, 0.9) * L
        a = rng.uniform(w / 2 + 0.5, max(w / 2 + 0.5, L - w / 2 - 0.5))
        b.sign("Floor", a, t, zf + sill / 2 - 0.1, w, h, 0.3, "shop", rng.random() < lit_p)


def fac_curtain(b, L, zs, ze, fh, fin_step, fin_d, fin_mat, band_mat, band_h, band_d, led_every=0,
                led_mat="LED_White"):
    mb = b.mb
    n = max(1, round(L / fin_step))
    for i in range(n + 1):
        a = L * i / n
        mb.box(max(0, a - 0.22), -fin_d, zs, min(L, a + 0.22), 0, ze, fin_mat, skip=("+y",))
    nfl = int((ze - zs) / fh + 0.01)
    for k in range(nfl + 1):
        z = zs + k * fh
        if z + band_h > ze + 0.01:
            z = ze - band_h
        mb.box(0, -band_d, z, L, 0, z + band_h, band_mat, skip=("+y",))
        if led_every and k % led_every == 0 and k < nfl:
            b.led.box(0, -band_d - 0.12, z + band_h * 0.25, L, -band_d, z + band_h * 0.7, led_mat, skip=("+y",))


def fac_balcony(b, L, zs, ze, fh, panel, unit_w, ac=0.7):
    mb, rng = b.mb, b.rng
    n = max(1, round(L / unit_w))
    uw = L / n
    for i in range(n + 1):
        a = uw * i
        mb.box(max(0, a - 0.7), -0.2, zs, min(L, a + 0.7), 0, ze, b.wall, skip=("+y",))
    nfl = int((ze - zs) / fh + 0.01)
    for k in range(nfl):
        z = zs + k * fh
        mb.box(0, -3.6, z, L, 0, z + 0.6, "Concrete_Light", skip=("+y",))
        mb.box(0, -3.6, z + 0.6, L, -3.3, z + 3.6, panel, skip=("+y",))
        mb.box(0, -3.65, z + 3.6, L, -3.25, z + 3.8, "Aluminum", skip=("+y",))
        for i in range(1, n):
            a = uw * i
            mb.box(a - 0.12, -3.3, z + 0.6, a + 0.12, -0.2, z + fh - 1.0, "Balcony_Panel", skip=("+y",))
        for i in range(n):
            if rng.random() < ac:
                x = uw * i + 0.9
                mb.box(x, -2.0, z + 0.6, x + 2.3, -0.5, z + 2.3, "AC_Unit")
    for a0 in (0.0, L - 0.5):
        mb.box(a0, -3.6, zs, a0 + 0.5, 0, zs + nfl * fh, b.wall, skip=("+y",))


def fac_louver(b, L, zs, ze, fh, fin_mat="Concrete_Light", step=2.6):
    mb = b.mb
    nfl = int((ze - zs) / fh + 0.01)
    for k in range(nfl):
        z = zs + k * fh
        mb.box(0, -1.0, z, L, 0, z + 3.6, b.wall, skip=("+y",))
    mb.box(0, -1.0, zs + nfl * fh, L, 0, ze, b.wall, skip=("+y",))
    n = max(2, round(L / step))
    for i in range(n + 1):
        a = L * i / n
        mb.box(max(0, a - 0.25), -1.7, zs, min(L, a + 0.25), 0, ze, fin_mat, skip=("+y",))


def lattice(mb, cx, y, z, w, h, lift, depth=3.2):
    """Rooftop advertising structure: steel lattice behind a sign plane at local y (facing -y)."""
    n = max(2, int(w / 6) + 1)
    xs = [cx - w / 2 + w * i / (n - 1) for i in range(n)]
    top = z + lift + h
    for x in xs:
        mb.box(x - 0.3, y + 0.2, z, x + 0.3, y + 0.8, top, "DarkMetal")
        mb.box(x - 0.3, y + depth, z, x + 0.3, y + depth + 0.6, z + lift + h * 0.7, "DarkMetal")
        mb.beam((x, y + 0.5, top - 0.3), (x, y + depth + 0.3, z + lift + h * 0.7), 0.3, 0.3, "DarkMetal")
        mb.beam((x, y + 0.5, z + 0.3), (x, y + depth + 0.3, z + 0.3), 0.3, 0.3, "DarkMetal")
    for zz in (z + lift - 0.3, z + lift + h * 0.5, top - 0.2):
        mb.box(cx - w / 2 - 0.3, y + 0.2, zz - 0.2, cx + w / 2 + 0.3, y + 0.8, zz + 0.2, "DarkMetal")
    for i in range(n - 1):
        mb.beam((xs[i], y + 0.5, z + lift), (xs[i + 1], y + 0.5, top - 0.4), 0.22, 0.22, "DarkMetal")
        mb.beam((xs[i + 1], y + 0.5, z + lift), (xs[i], y + 0.5, top - 0.4), 0.22, 0.22, "DarkMetal")
    mb.box(cx - w / 2 - 0.5, y - 1.5, z + lift - 0.9, cx + w / 2 + 0.5, y + 0.2, z + lift - 0.7, "Grate_Metal")


def roof(b, rect, z, sign_p=0.3, tank_p=0.4, big_sign=False, screen_p=0.0, ph=None):
    mb, rng = b.mb, b.rng
    x0, y0, x1, y1 = rect
    W, D = x1 - x0, y1 - y0
    parapet(mb, rect, z, ph if ph is not None else rng.uniform(1.6, 3.0), 0.6, b.wall)
    blocked = []
    if W > 12 and D > 14:
        pw, pdp = min(W - 4, rng.uniform(7, 12)), min(D - 6, rng.uniform(7, 11))
        px = rng.uniform(x0 + 2, x1 - 2 - pw)
        py = rng.uniform(y0 + D * 0.45, y1 - 2 - pdp)
        phh = rng.uniform(8.5, 10.5)
        mb.box(px, py, z, px + pw, py + pdp, z + phh, b.wall, skip=("-z",), fm={"+z": "Roof_Dark"})
        mb.box(px + 1.2, py - 0.12, z, px + 1.2 + DOOR_W, py, z + DOOR_H, "Door_Dark")
        parapet(mb, (px, py, px + pw, py + pdp), z + phh, 0.8, 0.3, b.wall, coping=None)
        blocked.append((px - 1, py - 1, px + pw + 1, py + pdp + 1))
        if rng.random() < tank_p:
            b.put("WaterTank", px + pw / 2, py + pdp / 2, z + phh, (0, -1, 0))
    elif rng.random() < tank_p and W > 8 and D > 8:
        b.put("WaterTank", x0 + W * 0.7, y0 + D * 0.7, z, (0, -1, 0))
        blocked.append((x0 + W * 0.7 - 4, y0 + D * 0.7 - 4, x0 + W * 0.7 + 4, y0 + D * 0.7 + 4))
    if (rng.random() < sign_p or big_sign) and W >= 12:
        sw = min(W - 2, rng.uniform(14, 28) if not big_sign else W - 3)
        sh = rng.uniform(5, 9) if not big_sign else rng.uniform(10, 16)
        lift = rng.uniform(3, 6)
        cx = x0 + W / 2
        lattice(mb, cx, y0 + 2.5, z, sw, sh, lift)
        if rng.random() < screen_p:
            with b.at(trans(0, y0 + 2.5 + 0.8 + 0.26 - 0.3, 0)):
                b.screen(None, cx, z + lift + sh / 2, sw, sh, base_t=0.0, frame=False)
        else:
            b.sign("Rooftop", cx, y0 + 2.5 - 0.05, z + lift + sh / 2, sw, sh, 0.5,
                   "name" if rng.random() < 0.35 else "shop", rng.random() < 0.6)
        blocked.append((cx - sw / 2 - 1, y0, cx + sw / 2 + 1, y0 + 7))
    if rng.random() < 0.25:
        b.put("Antenna", x0 + W * rng.uniform(0.2, 0.8), y0 + D * rng.uniform(0.5, 0.9), z, (0, -1, 0))
    b.roofs.append(((x0 + 1.2, y0 + 1.2, x1 - 1.2, y1 - 1.2), z, blocked))


def ext_stair(mb, L, zs, ze, fh, a0=None):
    """Japanese exterior steel emergency stair with solid panel guards (on a side face)."""
    a0 = L * 0.55 if a0 is None else a0
    nfl = int((ze - zs) / fh + 0.01)
    zs_ = [zs + k * fh for k in range(nfl)]
    if not zs_:
        return
    for k, z in enumerate(zs_):
        mb.box(a0, -3.6, z - 0.3, a0 + 11, -0.4, z, "DarkMetal")
        mb.box(a0, -3.7, z, a0 + 11, -3.5, z + 3.2, "Metal_Panel")
        if k + 1 < len(zs_):
            if k % 2 == 0:
                mb.beam((a0 + 1.2, -2.0, z + 0.1), (a0 + 9.8, -2.0, zs_[k + 1] - 0.2), 1.8, 0.3, "DarkMetal")
            else:
                mb.beam((a0 + 9.8, -2.0, z + 0.1), (a0 + 1.2, -2.0, zs_[k + 1] - 0.2), 1.8, 0.3, "DarkMetal")
    for a in (a0, a0 + 11):
        mb.box(a - 0.15, -3.7, Z0, a + 0.15, -3.4, zs_[-1] + 3.2, "DarkMetal")


def side_face(b, rect, side, z_from, z_to, gap, stair=False):
    """Exposed side wall: storefronts (if an alley/street), sparse windows, pipes, AC stacks, maybe an ad."""
    rng = b.rng
    F, L = face_matrix(rect, side)
    with b.at(F):
        if gap and z_from < 1:
            street_level(b, L, 0.4, MIX_SIDE, lobby=False)
            z_from = GF_H
        z_from = max(z_from, GF_H)
        if z_to - z_from < 6:
            return
        n = max(1, int(L / 16))
        fac_punched(b, L, z_from, z_to, FL_H, n, 3.2, 4.2, 4.0, ac=0.25, sticker=0.0, mull=False)
        a = L * rng.uniform(0.15, 0.35)
        b.mb.cyl(a, -0.5, 0.32, max(Z0, z_from - GF_H), z_to + 0.8, 6, "Pipe_Gray")
        b.mb.cyl(a + 1.1, -0.45, 0.22, max(Z0, z_from - GF_H), z_to + 0.8, 6, "Pipe_Gray")
        if stair and gap and L > 24:
            ext_stair(b.mb, L, GF_H, z_to, FL_H, a0=L * 0.45)
        elif z_to - z_from > 30 and rng.random() < 0.28:
            sw = min(L * 0.7, rng.uniform(14, 26))
            sh = min(z_to - z_from - 6, sw * rng.uniform(0.6, 1.3))
            if rng.random() < 0.55:
                b.screen("Medium" if sw < 22 else "Large", L * 0.62, z_to - 4 - sh / 2, sw, sh)
            else:
                b.sign("Floor", L * 0.62, -0.5, z_to - 4 - sh / 2, sw, min(sh, sw * 0.3), 0.4, "shop", True)


def body(b, w, d, H, SB, exp, core_front):
    """Ground-floor core (set back SB from the street line) + upper mass with soffit."""
    sbl = 0.4 if exp[0] else 0.0
    sbr = 0.4 if exp[1] else 0.0
    b.mb.box(sbl, SB, Z0, w - sbr, d, GF_H, b.wall, skip=("-z",))
    b.mb.box(0, 0, GF_H, w, d, H, b.wall, fm={"-y": core_front, "-z": "Soffit", "+z": b.roof_mat})


def sides(b, rect, H, exp, stair=False):
    for side, gap, nh in (("left", exp[0], exp[2]), ("right", exp[1], exp[3])):
        if gap or nh < H - 8:
            side_face(b, rect, side, 0.0 if gap else nh, H, gap, stair=stair)


def name_sign(b, w, H, lit=None, t=-0.6):
    sw = min(w * 0.7, 20)
    b.sign("BuildingName", w / 2, t, H - 2.6, sw, sw * 0.18, 0.35, "name",
           b.rng.random() < 0.5 if lit is None else lit)


# ---------------------------------------------------------------------------------------------
# foreground building types
# ---------------------------------------------------------------------------------------------


def t_zakkyo(b, w, d, fl, fh, exp):
    """Japanese multi-tenant commercial building (zakkyo): tile, punched windows, tenant signs, kanban."""
    rng = b.rng
    SB = rng.choice((0.4, 0.4, 2.8))
    H = GF_H + fl * fh
    step = fl >= 6 and rng.random() < 0.3
    Hm = H - 2 * fh if step else H
    b.H = H
    body(b, w, d, Hm, SB, exp, b.wall)
    street_level(b, w, SB, MIX_ZAKKYO)
    n = max(1, int(w / rng.uniform(7.0, 10.0)))
    win_w = min(w / n - 2.4, rng.uniform(3.8, 6.5))
    sill = rng.uniform(3.2, 3.9)
    fac_punched(b, w, GF_H, Hm, fh, n, win_w, rng.uniform(4.4, 5.8), sill,
                frame=rng.choice(["DarkMetal", "Aluminum"]), ac=0.14, sticker=0.2)
    floor_signs(b, w, GF_H, fh, int((Hm - GF_H) / fh), sill, p=0.38)
    if step:
        b.mb.box(0, 4, Hm, w, d, H, b.wall, fm={"+z": b.roof_mat})
        with b.at(trans(0, 4, 0)):
            fac_punched(b, w, Hm, H, fh, n, win_w, 5.0, sill, ac=0.2, sticker=0.0)
        b.mb.box(0, 0, Hm, w, 0.4, Hm + 3.0, "Metal_Panel")
        roof(b, (0, 4, w, d), H, sign_p=0.25, tank_p=0.5)
        b.roofs.append(((1, 0.8, w - 1, 3.2), Hm, []))
    else:
        roof(b, (0, 0, w, d), H, sign_p=0.35, tank_p=0.45)
    r = rng.random()
    edge = 0.7 if rng.random() < 0.5 else w - 0.7
    if r < 0.5:
        blade(b, edge, GF_H + 1.5, min(Hm - 2, GF_H + rng.uniform(26, 62)), proj=rng.uniform(3.2, 4.4),
              cells="vstack" if rng.random() < 0.6 else "vshop", cat="Vertical")
    elif r < 0.85:
        z = GF_H + 1.0
        while z + 8 < Hm - 2:
            if rng.random() < 0.6:
                blade(b, edge, z, z + rng.uniform(6, 9), proj=2.6)
            z += rng.uniform(10, 16)
    if rng.random() < 0.3:
        name_sign(b, w, Hm)
    sides(b, (0, 0, w, d), Hm, exp, stair=True)
    return H


def t_pencil(b, w, d, fl, fh, exp):
    """Very narrow 'pencil building' squeezed between neighbours."""
    rng = b.rng
    H = GF_H + fl * fh
    b.H = H
    modern = rng.random() < 0.5
    body(b, w, d, H, 0.4, exp, "Glass_Dark" if modern else b.wall)
    street_level(b, w, 0.4, [("shop", 3), ("resto", 2), ("lobby", 1)], lobby=False)
    if modern:
        fac_curtain(b, w, GF_H, H, fh, w / max(2, round(w / 4.0)), 0.5, "Aluminum", "Aluminum", 0.7, 0.45)
    else:
        fac_punched(b, w, GF_H, H, fh, 1, w - 4.8, fh - 4.6, 2.8, ac=0.25, sticker=0.3)
        floor_signs(b, w, GF_H, fh, fl, 2.8, p=0.3)
    if rng.random() < 0.75:
        edge = 0.7 if rng.random() < 0.5 else w - 0.7
        blade(b, edge, GF_H + 1, min(H - 3, GF_H + rng.uniform(28, 60)), proj=3.4,
              cells=rng.choice(["vstack", "vshop", "vneon"]), cat="Vertical")
    elif w > 12:
        sh = min(H - GF_H - 8, rng.uniform(22, 40))
        b.screen("Medium", w / 2, H - 4 - sh / 2, w - 3, sh, base_t=-0.5)
    roof(b, (0, 0, w, d), H, sign_p=0.45, tank_p=0.5)
    sides(b, (0, 0, w, d), H, exp)
    return H


def t_mansion(b, w, d, fl, fh, exp):
    """Shops below, Japanese apartment balconies above (partitions, AC units, frosted panels)."""
    rng = b.rng
    SB = rng.choice((0.4, 2.6))
    H = GF_H + fl * fh
    b.H = H
    body(b, w, d, H, SB, exp, "Glass_Dark")
    street_level(b, w, SB, MIX_ZAKKYO)
    fac_balcony(b, w, GF_H, H, fh, rng.choice(["Balcony_Panel", "Concrete_Light", "OffWhite", "Tile_Gray"]),
                rng.uniform(8.5, 11))
    roof(b, (0, 0, w, d), H, sign_p=0.15, tank_p=0.6)
    sides(b, (0, 0, w, d), H, exp)
    return H


def t_modern(b, w, d, fl, fh, exp):
    """Newer fashion / commercial building: glass + fins or louvers, name sign, medium screens."""
    rng = b.rng
    SB = 2.8
    H = GF_H + fl * fh
    b.H = H
    pod = fl >= 7 and rng.random() < 0.5
    Hp = GF_H + 3 * fh if pod else H
    glass = rng.choice(["Glass_Dark", "Glass_Blue", "Glass_Silver"])
    fin = rng.choice(["Aluminum", "DarkMetal", "OffWhite"])
    body(b, w, d, Hp, SB, exp, glass)
    street_level(b, w, SB, MIX_MODERN)
    vertical = rng.random() < 0.55
    led_every = 2 if rng.random() < 0.3 else 0
    led = rng.choice(["LED_White", "LED_White", "LED_Cyan"])
    if vertical:
        fac_curtain(b, w, GF_H, Hp, fh, rng.uniform(1.8, 3.0), 1.2, fin, fin, 0.6, 0.6, led_every, led)
    else:
        fac_curtain(b, w, GF_H, Hp, fh, 14.0, 0.5, fin, fin, 1.4, 1.8, led_every, led)
    if pod:
        r2 = (3.0, 5.0, w - 3.0, d - 2.0)
        b.mb.box(*r2[:2], Hp, *r2[2:], H, b.wall, fm={"-y": glass, "-x": glass, "+x": glass, "+z": b.roof_mat})
        with b.at(trans(3.0, 5.0, 0)):
            fac_curtain(b, w - 6, Hp, H, fh, 3.2, 0.8, fin, fin, 0.5, 0.5, led_every, led)
        roof(b, r2, H, sign_p=0.3, tank_p=0.2)
        b.roofs.append(((1, 1, w - 1, 4.5), Hp, []))
    else:
        roof(b, (0, 0, w, d), H, sign_p=0.3, tank_p=0.2)
    if rng.random() < 0.55 and w > 20:
        sw = min(w * 0.6, rng.uniform(10, 18))
        sh = sw * rng.uniform(0.55, 0.8)
        b.screen("Medium", w * rng.choice((0.3, 0.5, 0.7)), GF_H + 4 + sh / 2 + rng.uniform(0, 12), sw, sh,
                 base_t=-1.3)
    name_sign(b, w, Hp, lit=True, t=-2.0)
    sides(b, (0, 0, w, d), Hp, exp)
    return H


def t_louver(b, w, d, fl, fh, exp):
    """1970s/80s Tokyo commercial: concrete ribbon windows + vertical concrete fins, lots of tenant signs."""
    rng = b.rng
    SB = rng.choice((0.4, 2.6))
    H = GF_H + fl * fh
    b.H = H
    body(b, w, d, H, SB, exp, "Glass_Dark")
    street_level(b, w, SB, MIX_ZAKKYO)
    fac_louver(b, w, GF_H, H, fh, fin_mat=rng.choice(["Concrete_Light", "OffWhite", b.wall]),
               step=rng.uniform(2.2, 3.4))
    floor_signs(b, w, GF_H, fh, fl, 3.6, p=0.4, t=-1.9, lit_p=0.55)
    if rng.random() < 0.45:
        blade(b, 0.7 if rng.random() < 0.5 else w - 0.7, GF_H + 1, min(H - 2, GF_H + rng.uniform(24, 50)),
              proj=3.6, cells="vstack", cat="Vertical", off=1.9)
    roof(b, (0, 0, w, d), H, sign_p=0.4, tank_p=0.5)
    sides(b, (0, 0, w, d), H, exp, stair=True)
    return H


def t_arcade(b, w, d, fl, fh, exp):
    """Game-centre / karaoke / entertainment building: panel facade covered in lit signs and screens."""
    rng = b.rng
    fh = 13.0
    H = GF_H + fl * fh
    b.H = H
    b.wall = rng.choice(["Metal_Panel", "FadedBlack", "Charcoal"])
    body(b, w, d, H, 3.0, exp, b.wall)
    street_level(b, w, 3.0, [("arcade", 4), ("shop", 1)], lobby=False)
    nr = int(w / 3.2)
    for i in range(1, nr):
        a = w * i / nr
        b.mb.box(a - 0.2, -0.4, GF_H, a + 0.2, 0, H, "DarkMetal", skip=("+y",))
    for k in range(fl):
        zf = GF_H + k * fh
        b.mb.box(0, -0.5, zf, w, 0, zf + 0.5, "DarkMetal", skip=("+y",))
        r = rng.random()
        if r < 0.4:
            b.sign("Floor", w / 2, -0.9, zf + fh / 2, w * 0.72, fh * 0.5, 0.5, rng.choice(["misc", "shop"]), True)
        elif r < 0.65:
            sw = w * rng.uniform(0.35, 0.55)
            b.screen("Medium", w * rng.choice((0.3, 0.7)), zf + fh / 2, sw, min(fh - 3, sw * 0.6), base_t=-0.5)
    led = rng.choice(["LED_Magenta", "LED_Cyan", "LED_Purple", "Bulb_Strip"])
    for a in (0.0, w - 0.5):
        b.led.box(a, -0.9, GF_H, a + 0.5, -0.4, H, led, skip=("+y",))
    b.led.box(0, -0.9, H - 0.8, w, -0.4, H - 0.3, led, skip=("+y",))
    blade(b, 1.2, GF_H + 2, H - 3, proj=3.8, cells="vneon", lit=True, cat="Vertical")
    blade(b, w - 1.2, GF_H + 2, H - 3, proj=3.8, cells="vneon", lit=True, cat="Vertical")
    roof(b, (0, 0, w, d), H, big_sign=True, tank_p=0.2, screen_p=0.3)
    sides(b, (0, 0, w, d), H, exp)
    return H


def t_low(b, w, d, fl, fh, exp):
    """Low older commercial (2-4 floors) - konbini / shops below, big rooftop ad structure above."""
    rng = b.rng
    H = GF_H + fl * fh
    b.H = H
    body(b, w, d, H, 0.4, exp, b.wall)
    street_level(b, w, 0.4, MIX_LOW if w >= 18 else MIX_ZAKKYO)
    n = max(1, int(w / 8.0))
    fac_punched(b, w, GF_H, H, fh, n, min(w / n - 2.6, 5.5), 4.8, 3.4, ac=0.2, sticker=0.35)
    floor_signs(b, w, GF_H, fh, fl, 3.4, p=0.5)
    roof(b, (0, 0, w, d), H, big_sign=rng.random() < 0.8, tank_p=0.4, screen_p=0.45)
    sides(b, (0, 0, w, d), H, exp)
    return H


def t_back(b, w, d0, d1, H, Hfront):
    """Secondary building behind a shallow frontage building -> overlapping, layered silhouettes."""
    rng = b.rng
    b.H = H
    style = rng.random()
    core = "Glass_Dark" if style < 0.4 else b.wall
    b.mb.box(0, d0, Z0, w, d1, H, b.wall, skip=("-z",), fm={"-y": core, "+z": b.roof_mat})
    zs = GF_H + max(0, int((Hfront - GF_H) / FL_H) - 1) * FL_H
    with b.at(trans(0, d0, 0)):
        if style < 0.4:
            fac_balcony(b, w, zs, H, FL_H, "Balcony_Panel", 10.0, ac=0.5)
        else:
            n = max(1, int(w / 9))
            fac_punched(b, w, zs, H, FL_H, n, min(w / n - 2.6, 5.0), 5.0, 3.4, ac=0.15, sticker=0.1, mull=False)
            if rng.random() < 0.35:
                blade(b, w - 0.7, max(zs, Hfront + 2), H - 3, proj=3.4, cells="vstack", cat="Vertical")
    roof(b, (0, d0, w, d1), H, sign_p=0.35, tank_p=0.5)
    return H


FG_TYPES = {
    # type: (weight, width range, floors range, depth range, floor height)
    "zakkyo": (30, (16, 28), (5, 10), (36, 70), 11.5),
    "pencil": (14, (11, 17), (7, 12), (30, 55), 11.5),
    "mansion": (10, (28, 42), (7, 11), (40, 70), 11.0),
    "modern": (11, (28, 50), (6, 12), (50, 84), 12.0),
    "louver": (11, (24, 40), (5, 9), (45, 75), 12.0),
    "arcade": (7, (34, 56), (4, 6), (55, 84), 13.0),
    "low": (11, (20, 32), (2, 4), (35, 60), 11.5),
}
T_FUNCS = {"zakkyo": t_zakkyo, "pencil": t_pencil, "mansion": t_mansion, "modern": t_modern, "louver": t_louver,
           "arcade": t_arcade, "low": t_low}


# ---------------------------------------------------------------------------------------------
# landmarks
# ---------------------------------------------------------------------------------------------


def lm_stacked(b, w, d, exp):
    """LANDMARK 2: narrow building covered in vertically stacked advertisements."""
    rng = b.rng
    fh = 12.0
    H = GF_H + 12 * fh
    b.H = H
    b.wall = "Charcoal"
    body(b, w, d, H, 3.0, exp, "Glass_Dark")
    street_level(b, w, 3.0, [("arcade", 2), ("shop", 1)], lobby=False)
    for a in (0.0, w):
        b.mb.box(a - 0.6, -2.4, GF_H, a + 0.6, 0, H + 6, "DarkMetal")
    z = GF_H + 2.0
    k = 0
    while z < H - 6:
        h = rng.uniform(9, 15)
        if z + h > H - 2:
            h = H - 2 - z
        if h < 5:
            break
        b.mb.box(0, -2.4, z - 0.6, w, -1.8, z - 0.3, "DarkMetal")
        if k % 2 == 0:
            b.screen("Large" if w - 3 >= 22 else "Medium", w / 2, z + h / 2, w - 3, h - 1, base_t=-1.8,
                     frame=False)
        else:
            b.sign("Floor", w / 2, -2.4, z + h / 2, w - 3, h - 1.5, 0.5, rng.choice(["shop", "misc"]), True)
        z += h + 0.9
        k += 1
    blade(b, w + 0.2, GF_H + 4, H - 6, proj=4.5, cells="vneon", lit=True, cat="Vertical", off=2.4)
    roof(b, (0, 0, w, d), H, big_sign=True, tank_p=0.0, screen_p=1.0)
    sides(b, (0, 0, w, d), H, exp)
    return H


def lm_glass_ent(b, w, d, exp):
    """LANDMARK 3: dark glass entertainment tower with illuminated horizontal bands and screens."""
    rng = b.rng
    fh = 12.0
    fl = 15
    H = GF_H + fl * fh
    b.H = H
    b.wall = "FadedBlack"
    body(b, w, d, H, 3.2, exp, "Glass_Dark")
    street_level(b, w, 3.2, [("arcade", 2), ("shop", 2), ("lobby", 1)])
    fac_curtain(b, w, GF_H, H, fh, 3.0, 0.6, "DarkMetal", "FadedBlack", 0.9, 0.9)
    for k in range(0, fl, 2):
        z = GF_H + k * fh
        b.led.box(0, -1.05, z + 0.15, w, -0.9, z + 0.75, "LED_Cyan" if k % 4 == 0 else "LED_White", skip=("+y",))
    b.screen("Medium", w * 0.3, GF_H + 2.5 * fh, w * 0.42, w * 0.3, base_t=-0.9)
    b.screen("Medium", w * 0.72, GF_H + 5.5 * fh, w * 0.38, w * 0.42, base_t=-0.9)
    b.screen("Landmark" if w * 0.86 >= 40 else "Large", w / 2, GF_H + 10.5 * fh, w * 0.86, w * 0.5, base_t=-0.9)
    name_sign(b, w, H, lit=True, t=-1.2)
    b.mb.box(w * 0.2, d * 0.3, H, w * 0.8, d * 0.8, H + 10, "FadedBlack", skip=("-z",))
    b.led.box(w * 0.2 - 0.1, d * 0.3 - 0.12, H + 8.6, w * 0.8 + 0.1, d * 0.3, H + 9.4, "LED_Magenta", skip=("-z",))
    roof(b, (0, 0, w, d), H, sign_p=0.0, tank_p=0.0, ph=2.0)
    sides(b, (0, 0, w, d), H, exp)
    return H


def lm_block(b, w, d, exp):
    """LANDMARK 4: traditional rectangular Tokyo commercial block crammed with small businesses/signs."""
    rng = b.rng
    fh = 11.5
    fl = 8
    H = GF_H + fl * fh
    b.H = H
    b.wall = "Beige_Tile"
    body(b, w, d, H, 0.4, exp, b.wall)
    street_level(b, w, 0.4, [("shop", 4), ("resto", 4), ("vending", 1), ("lobby", 1)])
    n = int(w / 7.5)
    fac_punched(b, w, GF_H, H, fh, n, 4.4, 5.2, 3.6, frame="Aluminum", ac=0.2, sticker=0.3)
    floor_signs(b, w, GF_H, fh, fl, 3.6, p=0.9, lit_p=0.6)
    for a in (w * 0.18, w * 0.42, w * 0.66, w * 0.9):
        z0 = GF_H + rng.uniform(1, 12)
        blade(b, a, z0, min(H - 2, z0 + rng.uniform(10, 30)), proj=3.4,
              cells=rng.choice(["vshop", "vstack"]), cat="Blade" if rng.random() < 0.5 else "Vertical")
    blade(b, 0.7, GF_H + 1, H - 2, proj=4.2, cells="vstack", lit=True, cat="Vertical")
    name_sign(b, w, H, lit=False)
    roof(b, (0, 0, w * 0.5, d), H, big_sign=True, tank_p=0.6)
    roof(b, (w * 0.5, 0, w, d), H, big_sign=True, tank_p=0.3, screen_p=1.0)
    sides(b, (0, 0, w, d), H, exp, stair=True)
    return H


def lm_rooftop(b, w, d, exp):
    """LANDMARK 6: medium-height commercial building carrying a huge structural rooftop advert."""
    rng = b.rng
    fh = 11.5
    fl = 6
    H = GF_H + fl * fh
    b.H = H
    b.wall = "Tile_Gray"
    body(b, w, d, H, 2.6, exp, b.wall)
    street_level(b, w, 2.6, MIX_ZAKKYO)
    n = int(w / 8.5)
    fac_punched(b, w, GF_H, H, fh, n, 5.2, 5.0, 3.5, ac=0.2, sticker=0.25)
    floor_signs(b, w, GF_H, fh, fl, 3.5, p=0.5)
    parapet(b.mb, (0, 0, w, d), H, 2.0, 0.6, b.wall)
    sw, sh, lift = w - 4, 30.0, 7.0
    lattice(b.mb, w / 2, 4.0, H, sw, sh, lift, depth=6.0)
    with b.at(trans(0, 4.0 + 0.8 + 0.26 - 0.3, 0)):
        b.screen("Landmark", w / 2, H + lift + sh / 2, sw, sh, base_t=0.0, frame=False)
    b.roofs.append(((2, 14, w - 2, d - 2), H, []))
    sides(b, (0, 0, w, d), H, exp)
    return H


def corner_face_storefronts(b, rect, faces, SB, mix):
    for f in faces:
        F, L = face_matrix(rect, f)
        with b.at(F):
            street_level(b, L, SB, mix)


def lm_curved(b, S):
    """LANDMARK 1 (corner): rounded commercial building with a huge CURVED display facing the plaza."""
    rng = b.rng
    R = 36.0
    fh = 12.0
    fl = 10
    H = GF_H + fl * fh
    b.H = H
    b.wall = "OffWhite"
    arc = [(R + R * math.cos(math.radians(a)), R + R * math.sin(math.radians(a))) for a in range(190, 270, 10)]
    poly = [(R, 0), (S, 0), (S, S), (0, S), (0, R)] + arc
    SB = 3.0
    # ground core (inset) + upper body
    poly_in = [(R, SB), (S, SB), (S, S), (SB, S), (SB, R)] + [
        (R + (R - SB) * math.cos(math.radians(a)), R + (R - SB) * math.sin(math.radians(a))) for a in range(190, 270, 10)]
    b.mb.prism(poly_in, Z0, GF_H, "Glass_Store", side_mats=["Glass_Store", "OffWhite", "OffWhite"] +
               ["Glass_Store"] * (len(poly_in) - 3), top=False)
    b.mb.prism(poly, GF_H, H, "Glass_Dark", side_mats=["Glass_Dark", "OffWhite", "OffWhite"] +
               ["Glass_Dark"] * (len(poly) - 3), top_mat="Roof_Dark")
    b.mb.prism(poly, GF_H - 0.01, GF_H, "Soffit", top=False, bottom=True)
    # floor bands around the whole outline (white aluminium), LED every 3rd floor
    for k in range(fl + 1):
        z = GF_H + k * fh - (0.9 if k == fl else 0)
        for i in range(len(poly)):
            a, c = poly[i], poly[(i + 1) % len(poly)]
            e = Vector((c[0] - a[0], c[1] - a[1], 0))
            L = e.length
            e.normalize()
            if abs(a[0] - S) < 0.1 and abs(c[0] - S) < 0.1 or abs(a[1] - S) < 0.1 and abs(c[1] - S) < 0.1:
                continue
            with b.at(frame_matrix((a[0], a[1], 0), (e.x, e.y), (-e.y, e.x))):
                b.mb.box(-0.3, -1.1, z, L + 0.3, 0, z + 0.9, "Aluminum", skip=("+y",))
                if k % 3 == 1:
                    b.led.box(-0.3, -1.22, z + 0.3, L + 0.3, -1.1, z + 0.6, "LED_White", skip=("+y",))
    for f, (o, ax, ay, L) in {"front": ((R, 0), (1, 0), (0, 1), S - R), "left": ((0, S), (0, -1), (1, 0), S - R)}.items():
        with b.at(frame_matrix((o[0], o[1], 0), ax, ay)):
            street_level(b, L, SB, MIX_MODERN)
            fac_curtain(b, L, GF_H, H, fh, 3.0, 0.8, "Aluminum", "Aluminum", 0.01, 0.01)
    # pillars at the arc ground floor
    for a in range(195, 270, 15):
        x, y = R + (R - 0.6) * math.cos(math.radians(a)), R + (R - 0.6) * math.sin(math.radians(a))
        b.mb.box(x - 0.6, y - 0.6, Z0, x + 0.6, y + 0.6, GF_H, "OffWhite")
    # the curved screen + its curved backing frame
    zs0, zs1 = GF_H + 14, GF_H + 14 + 50
    b.mb.prism([(R + (R + 0.3) * math.cos(math.radians(a)), R + (R + 0.3) * math.sin(math.radians(a)))
                for a in (200, 215, 230, 245, 260)] +
               [(R + (R + 1.2) * math.cos(math.radians(a)), R + (R + 1.2) * math.sin(math.radians(a)))
                for a in (260, 245, 230, 215, 200)], zs0 - 1.2, zs1 + 1.2, "DarkMetal", bottom=True)
    b.curved.append((R, R, R + 1.6, math.radians(201), math.radians(259), zs0, zs1))
    b.sign("BuildingName", S * 0.62, -1.4, H - 3.0, 22, 4.0, 0.4, "name", True)
    b.mb.box(S * 0.4, S * 0.35, H, S * 0.85, S * 0.85, H + 9, "OffWhite", skip=("-z",))
    b.roofs.append(((R, 4, S - 4, S * 0.33), H, []))
    b.roofs.append(((4, R, S * 0.38, S - 4), H, []))
    return H


def lm_corner_wrap(b, S):
    """LANDMARK 5 (corner): two huge displays wrapping the corner (one per street face)."""
    rng = b.rng
    fh = 12.0
    fl = 11
    H = GF_H + fl * fh
    b.H = H
    b.wall = "Charcoal"
    SB = 3.0
    b.mb.box(SB, SB, Z0, S, S, GF_H, b.wall, skip=("-z",))
    b.mb.box(0, 0, GF_H, S, S, H, b.wall, fm={"-y": "Glass_Dark", "-x": "Glass_Dark", "-z": "Soffit",
                                             "+z": "Roof_Dark"})
    sw, sh = 54.0, 52.0
    zc = GF_H + 12 + sh / 2
    for f in ("front", "left"):
        F, L = face_matrix((0, 0, S, S), f)
        with b.at(F):
            street_level(b, L, SB, MIX_MODERN)
            fac_curtain(b, L, GF_H, H, fh, 3.0, 0.7, "DarkMetal", "Charcoal", 0.7, 0.7)
            if f == "front":
                b.mb.box(-2.6, -2.6, zc - sh / 2 - 1, sw - 2, 0, zc + sh / 2 + 1, "DarkMetal")
                b.screen("Landmark", sw / 2 - 3.1, zc, sw, sh, base_t=-1.8, frame=False)
            else:
                b.mb.box(L - sw + 2, -2.6, zc - sh / 2 - 1, L + 2.6, 0, zc + sh / 2 + 1, "DarkMetal")
                b.screen("Landmark", L - sw / 2 + 2.6, zc, sw, sh, base_t=-1.8, frame=False)
            b.led.box(0, -0.85, H - 1.2, L, -0.7, H - 0.6, "LED_White", skip=("+y",))
    b.sign("BuildingName", S * 0.7, -1.2, H - 4, 20, 3.6, 0.4, "name", True)
    roof(b, (0, 0, S, S), H, sign_p=0.0, tank_p=0.0)
    return H


def lm_dept(b, S):
    """Corner: chamfered Tokyo department store - stone fins, horizontal LED bands, giant vertical sign."""
    rng = b.rng
    C = 30.0
    fh = 12.5
    fl = 9
    H = GF_H + fl * fh
    b.H = H
    b.wall = "Beige_Tile"
    poly = [(C, 0), (S, 0), (S, S), (0, S), (0, C)]
    SB = 2.8
    k = math.sqrt(0.5)
    poly_in = [(C + SB, SB), (S, SB), (S, S), (SB, S), (SB, C + SB)]
    b.mb.prism(poly_in, Z0, GF_H, "Glass_Store", top=False)
    b.mb.prism(poly, GF_H, H, b.wall, side_mats=["Glass_Dark", b.wall, b.wall, "Glass_Dark", b.wall],
               top_mat="Roof_Dark")
    b.mb.prism(poly, GF_H - 0.01, GF_H, "Soffit", top=False, bottom=True)
    faces = {"front": frame_matrix((C, 0, 0), (1, 0), (0, 1)), "left": frame_matrix((0, S, 0), (0, -1), (1, 0)),
             "chamfer": frame_matrix((0, C, 0), (k, -k), (k, k))}
    for f, F in faces.items():
        L = C * math.sqrt(2) if f == "chamfer" else S - C
        with b.at(F):
            street_level(b, L, SB, MIX_MODERN)
            if f == "chamfer":
                for i in range(fl):
                    z = GF_H + i * fh
                    b.mb.box(0, -0.6, z, L, 0, z + 1.2, "Concrete_Light", skip=("+y",))
                    b.led.box(0, -0.75, z + 0.4, L, -0.6, z + 0.8, "HEX_Orange_LED" if i % 3 == 0 else "LED_White",
                              skip=("+y",))
                # giant flat vertical sign on the chamfer, facing the plaza diagonal
                b.mb.box(L / 2 - 4.6, -1.6, GF_H + 5, L / 2 + 4.6, 0, H + 9, "DarkMetal", skip=("+y",))
                b.sign("Vertical", L / 2, -1.9, (GF_H + 6 + H + 8) / 2, 8.0, H + 2 - GF_H, 0.6, "vshop", True)
            else:
                fac_curtain(b, L, GF_H, H, fh, 2.4, 1.5, "Beige_Tile", "Beige_Tile", 1.2, 1.5)
                if f == "front":
                    b.screen("Large", L * 0.55, GF_H + 5 * fh, 26, 16, base_t=-1.5)
    b.sign("BuildingName", S * 0.65, -1.8, H - 3, 22, 4, 0.4, "name", True)
    roof(b, (C, C * 0.5, S - 2, S - 2), H, big_sign=True, tank_p=0.3)
    return H


def lm_round_old(b, S):
    """Corner: 1980s round-corner fashion building - ribbon windows, cylinder topped by a vertical sign fin."""
    rng = b.rng
    R = 22.0
    fh = 12.0
    b.wall = "Tile_White"
    sp = dict(core="Glass_Dark", wall="Tile_White", fh=fh, bay=16, pier_w=0.6, pier_d=0.5, sill=4.2, win_h=5.0,
              band_d=1.0, mull=True, roof="Roof_Gray")
    ha, hb = GF_H + 7 * fh, GF_H + 6 * fh
    ht = GF_H + 9 * fh
    b.H = ht
    for rect, face, h in (((R, 0, S, 40), "front", ha), ((0, R, 40, S), "left", hb)):
        x0, y0, x1, y1 = rect
        b.mb.box(x0, y0, Z0, x1, y1, h, b.wall, skip=("-z",), fm={"-y" if face == "front" else "-x": "Glass_Dark",
                                                                   "+z": "Roof_Gray"})
        F, L = face_matrix(rect, face)
        with b.at(F):
            street_level(b, L, 0.4, MIX_ZAKKYO)
            facade_grid(b.mb, rng, L, GF_H, h, sp)
            floor_signs(b, L, GF_H, fh, int((h - GF_H) / fh), 4.2, p=0.45, t=-1.3)
        roof(b, rect, h, sign_p=0.6, tank_p=0.5)
    b.mb.box(38, 38, Z0, S, S, min(ha, hb) - fh, b.wall, skip=("-z",))
    n = 20
    b.mb.cyl(R, R, R, Z0, ht, n, "Glass_Dark", top=True, top_mat="Roof_Dark")
    z = GF_H
    while z < ht - 1:
        b.mb.cyl(R, R, R + 1.0, z, z + 4.2, n, "Tile_White", top=True, bottom=True)
        z += fh
    b.mb.cyl(R, R, R + 0.2, 0.4, SF_TOP, n, "Glass_Store", top=False)
    b.mb.cyl(R, R, R + 3.5, SF_TOP, SF_TOP + 0.8, n, "DarkMetal", top=True, bottom=True)
    b.led.cyl(R, R, R + 1.1, ht - 1.2, ht - 0.6, n, "LED_Magenta", top=False)
    dv = Vector((-1, -1, 0)).normalized()
    p = Vector((R, R, 0)) + dv * (R + 1.0)
    F = trans(p.x, p.y) @ rot_z(math.atan2(dv.y, dv.x) + math.pi / 2)
    with b.at(F):
        b.mb.box(-4.4, -1.4, ht - 34, 4.4, 2.0, ht + 24, "DarkMetal")
        b.sign("Vertical", 0.0, -1.7, ht - 5, 7.6, 56, 0.6, "vneon", True)
        b.screen("Large", 0.0, GF_H + 3 * fh, 18, 22, base_t=-0.6)
    b.roofs.append(((R * 0.4, R * 0.4, R * 1.6, R * 1.6), ht, []))
    return ht


LM_FUNCS = {"lm_stacked": lm_stacked, "lm_glass_ent": lm_glass_ent, "lm_block": lm_block, "lm_rooftop": lm_rooftop}
LANDMARKS = {
    # side: (world coordinate along the side, type, width)
    "N": [(-30, "lm_stacked", 30), (-300, "modern", 46), (330, "arcade", 50)],
    "E": [(-170, "lm_glass_ent", 54), (230, "arcade", 46)],
    "S": [(280, "lm_rooftop", 58), (-30, "modern", 48)],
    "W": [(160, "lm_block", 74), (-270, "modern", 44)],
}
CORNERS = {"NE": "lm_curved", "SE": "lm_corner_wrap", "NW": "lm_dept", "SW": "lm_round_old"}
CORNER_FUNCS = {"lm_curved": lm_curved, "lm_corner_wrap": lm_corner_wrap, "lm_dept": lm_dept,
                "lm_round_old": lm_round_old}


# ---------------------------------------------------------------------------------------------
# row planning + build
# ---------------------------------------------------------------------------------------------


def plan_row(rng, length, reserved, alley_p=0.13):
    reserved = sorted(reserved, key=lambda r: r[0])
    items = []
    cur = 0.0
    types = [(k, v[0]) for k, v in FG_TYPES.items()]

    def fill(a0, a1):
        pos = a0
        first = True
        prev = None
        while a1 - pos > 10:
            if not first and rng.random() < alley_p and a1 - pos > 34:
                g = rng.uniform(6, 10)
                items.append(dict(kind="gap", a0=pos, a1=pos + g))
                pos += g
            typ = pick(rng, types)
            if typ == prev:
                typ = pick(rng, types)
            lo, hi = FG_TYPES[typ][1]
            w = rng.uniform(lo, hi)
            if a1 - pos - w < 11:
                w = a1 - pos
                if w > hi * 1.3:
                    typ = "modern" if w > 40 else "zakkyo"
                    if w > 56:
                        half = w / 2
                        items.append(dict(kind="bldg", typ=typ, a0=pos, a1=pos + half))
                        pos += half
                        w = a1 - pos
            items.append(dict(kind="bldg", typ=typ, a0=pos, a1=pos + w))
            prev = typ
            pos += w
            first = False
        if a1 - pos > 0.01:
            if items and abs(items[-1]["a1"] - pos) < 1e-6:
                items[-1]["a1"] = a1
            else:
                items.append(dict(kind="gap", a0=pos, a1=a1))

    for r0, r1, it in reserved:
        if r0 > cur:
            fill(cur, r0)
        it = dict(it)
        it["a0"], it["a1"] = r0, r1
        items.append(it)
        cur = r1
    if length > cur:
        fill(cur, length)
    return items


UTIL = {}


def build_foreground(lay, rng):
    frames = side_frames(lay)
    out = []
    for side in ("N", "E", "S", "W"):
        M, length, to_a = frames[side]
        a = to_a(lay.side_streets[side])
        reserved = [(a - ST / 2 - SW, a + ST / 2 + SW, dict(kind="gap", street=True))]
        for (vv, typ, w) in LANDMARKS[side]:
            aa = to_a(vv)
            r = (aa - w / 2, aa + w / 2)
            if r[0] < 20 or r[1] > length - 20:
                continue
            if any(not (r[1] <= q[0] - 10 or r[0] >= q[1] + 10) for q in reserved):
                continue
            reserved.append((r[0], r[1], dict(kind="bldg", typ=typ, landmark=True)))
        items = plan_row(rng, length, reserved)
        # heights first (irregular skyline, flanks of side streets tall enough for the skybridge)
        prevH = 0.0
        for i, it in enumerate(items):
            if it["kind"] != "bldg":
                prevH = 0.0
                continue
            typ = it["typ"]
            near_street = any(items[j]["kind"] == "gap" and items[j].get("street")
                              for j in (i - 1, i + 1) if 0 <= j < len(items))
            if near_street and typ in ("low", "pencil", "mansion"):
                typ = it["typ"] = "zakkyo"
            if typ in FG_TYPES:
                _, _, flr, dr, fh = FG_TYPES[typ]
                fl = rng.randint(*flr)
                H = GF_H + fl * fh
                if abs(H - prevH) < 8:
                    fl = min(flr[1] + 1, fl + 2) if rng.random() < 0.5 else max(flr[0], fl - 2)
                if near_street:
                    fl = max(fl, 4)
                it["fl"], it["fh"] = fl, fh
                it["d"] = max(rng.uniform(*dr), 46 if near_street else 0)
                it["H"] = GF_H + fl * fh
            else:
                it["d"] = rng.uniform(60, 80)
                it["H"] = {"lm_stacked": GF_H + 144, "lm_glass_ent": GF_H + 180, "lm_block": GF_H + 92,
                           "lm_rooftop": GF_H + 69}[typ]
            prevH = it["H"]
        idx = 0
        for i, it in enumerate(items):
            if it["kind"] != "bldg":
                if (not it.get("street") and 0 < i < len(items) - 1 and items[i - 1]["kind"] == "bldg"
                        and items[i + 1]["kind"] == "bldg"):
                    UTIL.setdefault(side, []).append(("alley", M, it["a0"], it["a1"]))
                continue
            idx += 1
            lg = i == 0 or items[i - 1]["kind"] == "gap"
            rg = i == len(items) - 1 or items[i + 1]["kind"] == "gap"
            lh = 0.0 if lg else items[i - 1]["H"]
            rh = 0.0 if rg else items[i + 1]["H"]
            exp = (lg, rg, lh, rh)
            w = it["a1"] - it["a0"]
            d = it["d"]
            typ = it["typ"]
            Mb = M @ trans(it["a0"], 0, 0)
            name = "FG_%s_%02d_%s" % (side, idx, typ.replace("lm_", "Landmark_").title().replace("_", ""))
            b = Bld(name, Mb, rng, rng.choice(WALL_TILES))
            if typ in LM_FUNCS:
                LM_FUNCS[typ](b, w, d, exp)
            else:
                T_FUNCS[typ](b, w, d, it["fl"], it["fh"], exp)
            ob = b.finish(COLL["BUILDINGS_FOREGROUND"], (w / 2, d / 2, 0))
            ob["type"] = typ
            out.append(ob)
            # secondary building behind shallow frontage buildings (layered silhouettes)
            if d < 58 and rng.random() < 0.75:
                d0, d1 = d + 1.0, FG_MAX_D
                Hb = b.H + rng.uniform(14, 64)
                Hb = GF_H + round((Hb - GF_H) / FL_H) * FL_H
                bb = Bld(name + "_Back", Mb, rng, rng.choice(WALL_TILES))
                t_back(bb, w, d0, d1, Hb, b.H)
                bb.finish(COLL["BUILDINGS_FOREGROUND"], (w / 2, (d0 + d1) / 2, 0), eq_density=0.6)
    # corner landmarks
    S = 92.0
    corner_frames = {
        "NE": frame_matrix((lay.FX, lay.FY, 0), (1, 0), (0, 1)),
        "NW": frame_matrix((-lay.FX, lay.FY, 0), (-1, 0), (0, 1)),
        "SE": frame_matrix((lay.FX, -lay.FY, 0), (1, 0), (0, -1)),
        "SW": frame_matrix((-lay.FX, -lay.FY, 0), (-1, 0), (0, -1)),
    }
    for key, M in corner_frames.items():
        typ = CORNERS[key]
        b = Bld("FG_Corner_%s_%s" % (key, typ.replace("lm_", "Landmark_").title().replace("_", "")), M, rng,
                "OffWhite")
        CORNER_FUNCS[typ](b, S)
        ob = b.finish(COLL["BUILDINGS_FOREGROUND"], (S / 2, S / 2, 0))
        ob["type"] = typ
        out.append(ob)
    return out


def build_utility_lines(lay, rng):
    """Overhead cables across alleys / side streets + utility poles along the side streets."""
    for side, entries in UTIL.items():
        mb = MB()
        for kind, M, a0, a1 in entries:
            with mb.xf(M):
                if kind == "alley":
                    for _ in range(rng.randint(2, 4)):
                        t = rng.uniform(3, 30)
                        z = rng.uniform(19, 30)
                        tm = t + rng.uniform(-3, 3)
                        mid = ((a0 + a1) / 2, tm, z - rng.uniform(0.8, 1.8))
                        mb.beam((a0 + 0.2, t, z), mid, 0.12, 0.12, "Cable")
                        mb.beam(mid, (a1 - 0.2, t + rng.uniform(-3, 3), z), 0.12, 0.12, "Cable")
        if not mb.empty():
            mb.build("UtilityLines_%s" % side, COLL["STRUCTURES"])
    # poles: along both sidewalks of each mid-side street, cables between them
    frames = side_frames(lay)
    for side in ("N", "E", "S", "W"):
        M, length, to_a = frames[side]
        a = to_a(lay.side_streets[side])
        mb = MB()
        poles = []
        for sgn in (-1, 1):
            ax = a + sgn * (ST / 2 + 3.0)
            for t in (8.0, 40.0, 72.0):
                poles.append((ax, t))
                instance("UtilityPole", M @ Vector((ax, t, 0)), math.atan2(M[1][0], M[0][0]), COLL["STRUCTURES"],
                         name=_next_name("UtilityPole"))
        with mb.xf(M):
            for sgn in (-1, 1):
                ax = a + sgn * (ST / 2 + 3.0)
                for t0, t1 in ((8.0, 40.0), (40.0, 72.0)):
                    for dz, dx in ((33.0, -1.6), (33.0, 1.6), (30.5, -1.2), (30.5, 1.2)):
                        mid = (ax + dx, (t0 + t1) / 2, dz - 1.4)
                        mb.beam((ax + dx, t0, dz), mid, 0.1, 0.1, "Cable")
                        mb.beam(mid, (ax + dx, t1, dz), 0.1, 0.1, "Cable")
                # drop cables to the buildings
                for t in (8.0, 40.0, 72.0):
                    bx = a + sgn * (ST / 2 + SW)
                    mb.beam((ax, t, 30.5), (bx, t + 2, 24.0), 0.1, 0.1, "Cable")
            for t in (40.0, 72.0):
                mb.beam((a - ST / 2 - 3.0, t, 33.0), (a, t, 31.0), 0.1, 0.1, "Cable")
                mb.beam((a, t, 31.0), (a + ST / 2 + 3.0, t, 33.0), 0.1, 0.1, "Cable")
        mb.build("UtilityLines_SideStreet_%s" % side, COLL["STRUCTURES"])


# ---------------------------------------------------------------------------------------------
# midground (medium-detail Tokyo buildings)
# ---------------------------------------------------------------------------------------------


def mg_tokyo(b, w, d, H, exp_l, exp_r, vista_as, full_street=True):
    rng = b.rng
    b.H = H
    style = pick(rng, [("tile", 4), ("mansion", 2), ("glass", 2), ("louver", 2)])
    core = b.wall if style == "tile" else "Glass_Dark"
    if full_street:  # only where a street / alley vista ends on this building
        body(b, w, d, H, 0.4, (exp_l, exp_r, 0.0, 0.0), core)
        street_level(b, w, 0.4, [("shop", 4), ("resto", 2), ("service", 2), ("vending", 1)], lobby=False)
    else:
        body(b, w, d, H, 0.0, (False, False, 0.0, 0.0), core)
        storefront(b.mb, rng, w, dict(wall=b.wall, fascia="Charcoal"), simple=True)
    if style == "tile":
        sp = spec_zakkyo(rng)
        sp["wall"] = b.wall
        sp["core"] = "Glass_Dark"
        sp["ac"] = 0.0
        sp["ledge"] = False
        sp["bay"] = rng.uniform(8, 11)
        sp["pier_w"] = rng.uniform(3.0, 4.2)
        facade_grid(b.mb, rng, w, GF_H, H, sp)
    elif style == "mansion":
        fac_balcony(b, w, GF_H, H, 11.0, rng.choice(["Balcony_Panel", "Concrete_Light", "OffWhite"]), 12.0, ac=0.0)
    elif style == "glass":
        fac_curtain(b, w, GF_H, H, 12.0, 4.5, 0.6, "Aluminum", "Aluminum", 0.6, 0.5)
    else:
        fac_louver(b, w, GF_H, H, 12.0, step=3.6)
    if rng.random() < 0.18:
        z0 = GF_H + rng.uniform(10, 40)
        blade(b, w - 0.7 if rng.random() < 0.5 else 0.7, z0, min(H - 4, z0 + rng.uniform(25, 45)), proj=4.0,
              cells=rng.choice(["vstack", "vshop", "vneon"]), cat="Vertical")
    for va in vista_as:
        if 12 < va < w - 12:
            sw = min(w - 8, rng.uniform(36, 50))
            sh = sw * rng.uniform(0.5, 0.62)
            b.screen("Landmark" if sw >= 40 else "Large", va, rng.uniform(44, 64) + sh / 2, sw, sh, base_t=-1.0)
    roof(b, (0, 0, w, d), H, sign_p=0.3 if H < 170 else 0.08, tank_p=0.4, screen_p=0.25)
    return H


def build_midground(lay, rng):
    out = []
    frames = side_frames(lay, off=MG_OFF, ext=MG_OFF + 240)
    vistas = {"N": [lay.side_streets["N"], lay.HX + ST / 2, -lay.HX - ST / 2],
              "S": [lay.side_streets["S"], lay.HX + ST / 2, -lay.HX - ST / 2],
              "E": [lay.side_streets["E"], lay.HY + ST / 2, -lay.HY - ST / 2],
              "W": [lay.side_streets["W"], lay.HY + ST / 2, -lay.HY - ST / 2]}
    vista_ok = {"N": [0, 1], "S": [0, 2], "E": [0], "W": [0, 1]}
    for side in ("N", "E", "S", "W"):
        M, length, to_a = frames[side]
        if side in ("E", "W"):
            ay_n = lay.FY + MG_OFF - 0.5
            if side == "E":
                M = frame_matrix((lay.FX + MG_OFF, ay_n, 0), (0, -1), (1, 0))
                to_a = (lambda an: (lambda v: an - v))(ay_n)
            else:
                M = frame_matrix((-(lay.FX + MG_OFF), -ay_n, 0), (0, 1), (-1, 0))
                to_a = (lambda an: (lambda v: v + an))(ay_n)
            length = 2 * ay_n
        vista_a = [to_a(v) for i, v in enumerate(vistas[side]) if i in vista_ok[side]]
        all_open = [to_a(v) for v in vistas[side]]
        pos = 0.0
        idx = 0
        while pos < length - 10:
            w = rng.uniform(30, 72)
            if length - pos - w < 24:
                w = length - pos
            a0, a1 = pos, pos + w
            idx += 1
            d = rng.uniform(70, 110)
            H = rng.choice([rng.uniform(60, 120), rng.uniform(100, 170), rng.uniform(150, 240)])
            H = GF_H + round((H - GF_H) / FL_H) * FL_H
            Mb = M @ trans(a0, 0, 0)
            b = Bld("MG_%s_%02d" % (side, idx), Mb, rng, rng.choice(WALL_TILES))
            full = any(a0 - 45 < va < a1 + 45 for va in all_open) or rng.random() < 0.15
            mg_tokyo(b, w, d, H, idx == 1, length - a1 < 1, [va - a0 for va in vista_a], full_street=full)
            out.append(b.finish(COLL["BUILDINGS_MIDGROUND"], (w / 2, d / 2, 0), eq_density=0.45))
            pos = a1
    return out


def face_matrix(rect, face):
    ofn, ax, ay, lfn, _ = FACE_FRAMES[face]
    o = ofn(*rect)
    return frame_matrix((o[0], o[1], 0), ax, ay), lfn(*rect)


def side_frames(lay, off=0.0, ext=0.0):
    """Row frames for N/E/S/W (front faces the plaza). ext extends rows past the corners."""
    HX, HY, FX, FY = lay.HX, lay.HY, lay.FX + off, lay.FY + off
    ax_n = HX - SW + ext
    ay_n = HY - SW + ext
    return {
        "N": (frame_matrix((-ax_n, FY, 0), (1, 0), (0, 1)), 2 * ax_n, lambda v: v + ax_n),
        "S": (frame_matrix((ax_n, -FY, 0), (-1, 0), (0, -1)), 2 * ax_n, lambda v: ax_n - v),
        "E": (frame_matrix((FX, ay_n, 0), (0, -1), (1, 0)), 2 * ay_n, lambda v: ay_n - v),
        "W": (frame_matrix((-FX, -ay_n, 0), (0, 1), (-1, 0)), 2 * ay_n, lambda v: v + ay_n),
    }



def skyline_ads(mb, rng, lay, cx, cy, fw, fd, H, inner_x, inner_y):
    """Big digital billboards high up on the park-facing side of background towers."""
    p = 0.9 if H > 260 else 0.7 if H > 200 else 0.35
    if rng.random() > p:
        return
    ex, ey = abs(cx) - inner_x, abs(cy) - inner_y
    faces = []
    if ex >= ey or rng.random() < 0.35:
        faces.append(("x", -math.copysign(1, cx)))
    if ey > ex or rng.random() < 0.35:
        faces.append(("y", -math.copysign(1, cy)))
    for i, (axis, sgn) in enumerate(faces):
        face_w = fd if axis == "x" else fw
        if rng.random() < 0.6:
            sw = face_w * rng.uniform(0.78, 0.95)
            sh = sw * rng.uniform(0.55, 0.85)
        else:
            sw = face_w * rng.uniform(0.4, 0.55)
            sh = sw * rng.uniform(2.0, 3.2)
        zc = min(H * rng.uniform(0.72, 0.9), H - 5 - sh / 2)
        if zc - sh / 2 < 45:
            continue
        off = (fw if axis == "x" else fd) / 2
        if axis == "x":
            c = Vector((cx + sgn * (off + 1.3), cy, zc))
            facing = (sgn, 0, 0)
            mb.box(cx + sgn * off, cy - sw / 2 - 1, zc - sh / 2 - 1, cx + sgn * (off + 1.0), cy + sw / 2 + 1,
                   zc + sh / 2 + 1, "DarkMetal")
        else:
            c = Vector((cx, cy + sgn * (off + 1.3), zc))
            facing = (0, sgn, 0)
            mb.box(cx - sw / 2 - 1, cy + sgn * off, zc - sh / 2 - 1, cx + sw / 2 + 1, cy + sgn * (off + 1.0),
                   zc + sh / 2 + 1, "DarkMetal")
        ob = board_object(_next_name("Billboard_Skyline"), "SCREENS_SKYLINE", c, facing, sw, sh, 0.6,
                          "Screen_Atlas", "S", rng.randrange(16))
        ob["display_face"] = "local +Y (Roblox: Front)"
        SCREENS.append(ob)


def build_skyline(lay, rng):
    FX, FY = lay.FX, lay.FY
    inner_x, inner_y = FX + MG_OFF + 150, FY + MG_OFF + 150
    outer = max(FX, FY) + 820
    cell = 80.0
    sectors = {}
    x = -outer
    while x < outer:
        y = -outer
        while y < outer:
            cx, cy = x + cell / 2, y + cell / 2
            if abs(cx) < inner_x and abs(cy) < inner_y:
                y += cell
                continue
            if rng.random() < 0.62:
                dist = max(abs(cx) - inner_x, abs(cy) - inner_y)
                fw = rng.uniform(30, 56)
                fd = rng.uniform(30, 56)
                jx = rng.uniform(-(cell - fw) / 2, (cell - fw) / 2)
                jy = rng.uniform(-(cell - fd) / 2, (cell - fd) / 2)
                H = rng.uniform(110, 240) + rng.random() ** 4 * 300 + dist * 0.10
                ang = math.atan2(cy, cx)
                key = int(((ang + math.pi) / (2 * math.pi)) * 16) % 16
                sectors.setdefault(key, []).append((cx + jx, cy + jy, fw, fd, H))
            y += cell
        x += cell
    out = []
    mats = ["Skyline_A", "Skyline_B", "Skyline_C", "Skyline_Glass"]
    ad_rng = random.Random(SEED + 404)  # separate stream: adding ads never moves a tower
    for key in sorted(sectors):
        mb = MB()
        for (cx, cy, fw, fd, H) in sectors[key]:
            skyline_ads(mb, ad_rng, lay, cx, cy, fw, fd, H, inner_x, inner_y)
            m = rng.choice(mats)
            mb.box(cx - fw / 2, cy - fd / 2, Z0, cx + fw / 2, cy + fd / 2, H, m, skip=("-z",))
            # coarse horizontal bands
            z = 40.0 + rng.uniform(0, 20)
            step = rng.choice([24.0, 36.0, 48.0, 1e9, 1e9])
            while z < H - 10:
                mb.box(cx - fw / 2 - 0.4, cy - fd / 2 - 0.4, z, cx + fw / 2 + 0.4, cy + fd / 2 + 0.4, z + 1.2,
                       "Skyline_Band", skip=("-z",))
                z += step
            r = rng.random()
            if r < 0.4:
                s = rng.uniform(0.55, 0.8)
                h2 = rng.uniform(20, 70)
                mb.box(cx - fw * s / 2, cy - fd * s / 2, H, cx + fw * s / 2, cy + fd * s / 2, H + h2, m, skip=("-z",))
                H += h2
            elif r < 0.6:
                mb.box(cx - fw * 0.3, cy - fd * 0.3, H, cx + fw * 0.3, cy + fd * 0.3, H + 8, "Skyline_Band",
                       skip=("-z",))
            if H > 380:
                mb.box(cx - 0.4, cy - 0.4, H, cx + 0.4, cy + 0.4, H + 30, "Skyline_Band")
                mb.box(cx - 0.8, cy - 0.8, H + 30, cx + 0.8, cy + 0.8, H + 31.5, "LED_Red")
        xs = [s[0] for s in sectors[key]]
        ys = [s[1] for s in sectors[key]]
        o = (sum(xs) / len(xs), sum(ys) / len(ys), 0)
        ob = mb.build("Skyline_Sector_%02d" % (key + 1), COLL["SKYLINE_BACKGROUND"], origin=o)
        out.append(ob)
    return out


def build_elevated(lay, rng):
    """Elevated rail viaduct behind the west frontage + glass skybridge over the north side street."""
    FX, FY = lay.FX, lay.FY
    xc = -(FX + 100.0)
    y0, y1 = -(FY + MG_OFF + 60), FY + MG_OFF + 60
    zd = 30.0
    mb = MB()
    mb.box(xc - 8, y0, zd - 3.0, xc + 8, y1, zd, "Concrete_Wall", fm={"+z": "Roof_Dark"})
    for s_ in (-1, 1):
        mb.box(xc + s_ * 8 - (0.9 if s_ > 0 else 0), y0, zd, xc + s_ * 8 + (0 if s_ > 0 else 0.9), y1, zd + 2.6,
               "Concrete_Light")
        mb.box(xc + s_ * 8.05 - 0.05, y0, zd - 2.2, xc + s_ * 8.05 + 0.05, y1, zd - 1.9, "LED_White",
               skip=("-z",))
    for rx in (-3.2, 3.2):
        for off in (-0.75, 0.75):
            mb.box(xc + rx + off - 0.15, y0, zd, xc + rx + off + 0.15, y1, zd + 0.5, "DarkMetal", skip=("-z",))
    side_w = lay.side_streets["W"]
    y = y0 + 20
    while y < y1 - 10:
        if not (side_w - ST / 2 - 4 < y < side_w + ST / 2 + 4) and not (lay.HY - 4 < abs(y) < lay.HY + ST + 4):
            mb.box(xc - 4.5, y - 2.5, Z0, xc + 4.5, y + 2.5, zd - 3, "Concrete_Wall", skip=("-z",))
            mb.box(xc - 7.5, y - 3.0, zd - 6, xc + 7.5, y + 3.0, zd - 3, "Concrete_Wall")
        # catenary portal
        mb.box(xc - 7.6, y - 0.3, zd, xc - 7.0, y + 0.3, zd + 14, "DarkMetal")
        mb.box(xc + 7.0, y - 0.3, zd, xc + 7.6, y + 0.3, zd + 14, "DarkMetal")
        mb.box(xc - 7.6, y - 0.3, zd + 13.4, xc + 7.6, y + 0.3, zd + 14, "DarkMetal")
        y += 40.0
    for rx in (-3.2, 3.2):
        mb.box(xc + rx - 0.06, y0, zd + 11.8, xc + rx + 0.06, y1, zd + 11.95, "DarkMetal", skip=("-z",))
    mb.build("RailViaduct_West", COLL["STRUCTURES"], origin=(xc, 0, 0))

    v = lay.side_streets["N"]
    gx0, gx1 = v - ST / 2 - SW - 6, v + ST / 2 + SW + 6
    yb0, yb1 = FY + 26, FY + 40
    zb0, zb1 = 30.0, 40.0
    sb = MB()
    sb.box(gx0, yb0, zb0, gx1, yb1, zb0 + 1.4, "Concrete_Light")
    sb.box(gx0, yb0, zb1 - 1.0, gx1, yb1, zb1, "Metal_Panel")
    sb.box(gx0 + 0.3, yb0 + 0.3, zb0 + 1.4, gx1 - 0.3, yb1 - 0.3, zb1 - 1.0, "Glass_Blue")
    n = int((gx1 - gx0) / 5)
    for i in range(n + 1):
        x = gx0 + (gx1 - gx0) * i / n
        for yy in (yb0, yb1):
            sb.box(x - 0.25, yy - 0.35, zb0 + 1.4, x + 0.25, yy + 0.35, zb1 - 1.0, "DarkMetal")
    sb.box(gx0, yb0 - 0.4, zb0 + 1.6, gx1, yb0 + 0.1, zb0 + 1.9, "LED_Cyan")
    sb.build("Skybridge_North", COLL["STRUCTURES"], origin=((gx0 + gx1) / 2, (yb0 + yb1) / 2, zb0))


def build_plaza_masts(lay):
    HX, HY = lay.HX, lay.HY
    inset = 14.0
    pts = []
    nx = max(2, int(2 * HX / 170))
    ny = max(2, int(2 * HY / 170))
    for i in range(nx + 1):
        x = -HX + inset + (2 * HX - 2 * inset) * i / nx
        pts.append((x, HY - inset, math.pi))
        pts.append((x, -HY + inset, 0))
    for j in range(1, ny):
        y = -HY + inset + (2 * HY - 2 * inset) * j / ny
        pts.append((HX - inset, y, math.pi / 2))
        pts.append((-HX + inset, y, -math.pi / 2))
    k = 0
    for (x, y, rz) in pts:
        k += 1
        instance("LightMast_Plaza", (x, y, 0), rz, COLL["STRUCTURES"], name="LightMast_Plaza_%02d" % k)


# ----------------------------------------------------------------------------------------------
# reference (not exported)
# ----------------------------------------------------------------------------------------------


def build_reference(lay, court):
    col = COLL["REF"]

    def dummy(name, x, y, z, rz=0.0):
        mb = MB()
        with mb.xf(trans(x, y, z) @ rot_z(rz)):
            mb.box(-1.0, -0.5, 0, -0.05, 0.5, 2.0, "Ref_Avatar")
            mb.box(0.05, -0.5, 0, 1.0, 0.5, 2.0, "Ref_Avatar")
            mb.box(-1.0, -0.5, 2.0, 1.0, 0.5, 4.0, "Ref_Avatar")
            mb.box(-2.0, -0.5, 2.0, -1.05, 0.5, 4.0, "Ref_Avatar")
            mb.box(1.05, -0.5, 2.0, 2.0, 0.5, 4.0, "Ref_Avatar")
            mb.box(-0.6, -0.6, 4.0, 0.6, 0.6, 5.2, "Ref_Avatar")
        return mb.build(name, col, origin=(x, y, z))

    dummy("Ref_Avatar_5studs_Plaza", -180, 110, 0.0, 0.6)
    dummy("Ref_Avatar_5studs_Pit", 120, lay.PY1 + 70, -PIT_D, 2.4)
    dummy("Ref_Avatar_5studs_GrandStair", 6, lay.PY0 + 4, 0.0, 3.1)
    dummy("Ref_Avatar_5studs_Sidewalk", 40, lay.FY - 6, 0.0, 0.0)
    # court capacity ghosts (exact court footprint, no markings) -> proves the room exists
    g = COLL["GHOSTS"]
    CL, CW = lay.CL, lay.CW
    ghosts = []
    ns2, ew2, band, gap = lay.spine_ns / 2, lay.spine_ew / 2, lay.band, 15
    for sx in (-1, 1):
        for i in range(2):
            for j in range(2):
                cx = sx * (ns2 + gap + lay.slot_l / 2 + i * (lay.slot_l + gap))
                cy = ew2 + gap + lay.slot_w / 2 + j * (lay.slot_w + gap)
                ghosts.append((cx, cy, CL, CW, 0.02, "Ref_CourtGhost"))
    wing_x = lay.PX + WALL_T + lay.TRUN + WALL_T + 14
    for sx in (-1, 1):
        cx = sx * (wing_x + (lay.HX - band - wing_x) / 2)
        cy = -ew2 - gap - lay.slot_l / 2
        ghosts.append((cx, cy, CW, CL, 0.02, "Ref_CourtGhost"))
    # practice: 1 full + 1 half
    y_clean0, y_clean1 = lay.PY1 + 5, lay.PY0 - lay.STAIRZ
    cyp = (y_clean0 + y_clean1) / 2
    ghosts.append((-lay.PX + 5 + lay.slot_l / 2, cyp, CL, CW, -PIT_D + 0.05, "Ref_CourtGhost"))
    hx = -lay.PX + 5 + lay.slot_l + 10 + lay.half_l / 2
    ghosts.append((hx, cyp, CL / 2, CW, -PIT_D + 0.05, "Ref_HalfCourtGhost"))
    for k, (cx, cy, sx_, sy_, z, m) in enumerate(ghosts):
        mb = MB()
        mb.box(cx - sx_ / 2, cy - sy_ / 2, z, cx + sx_ / 2, cy + sy_ / 2, z + 0.06, m, skip=("-z",))
        mb.build("CourtGhost_%02d" % (k + 1), g)
    return len(ghosts) - 2


# ----------------------------------------------------------------------------------------------
# lighting / render / export
# ----------------------------------------------------------------------------------------------


def setup_world():
    sc = bpy.context.scene
    w = bpy.data.worlds.new("HEX_Sky")
    sc.world = w
    try:
        w.use_nodes = True
    except Exception:
        pass
    nt = w.node_tree
    bg = nt.nodes.get("Background") or nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.get("World Output") or nt.nodes.new("ShaderNodeOutputWorld")
    sky = nt.nodes.new("ShaderNodeTexSky")
    for t in ("MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "NISHITA"):
        try:
            sky.sky_type = t
            break
        except Exception:
            continue
    sky.sun_disc = False
    sun_el = math.radians(38)
    sun_az = math.radians(200)  # from +Y (north) clockwise -> sun in the south-south-west
    s = Vector((math.sin(sun_az) * math.cos(sun_el), math.cos(sun_az) * math.cos(sun_el), math.sin(sun_el)))
    sky.sun_elevation = sun_el
    sky.sun_rotation = math.atan2(s.x, s.y)
    try:
        sky.altitude = 40.0
    except Exception:
        pass
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.22
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 4.2
    sun.angle = math.radians(1.2)
    sun.color = (1.0, 0.95, 0.88)
    so = bpy.data.objects.new("Sun", sun)
    sc.collection.objects.link(so)
    # sun pointing: Blender sun shines along its local -Z
    so.rotation_euler = (-s).to_track_quat("-Z", "Y").to_euler()
    sc.render.engine = "CYCLES"
    sc.cycles.samples = RENDER_SAMPLES
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 4
    sc.cycles.diffuse_bounces = 2
    sc.cycles.glossy_bounces = 2
    sc.cycles.transmission_bounces = 2
    sc.render.resolution_x, sc.render.resolution_y = RENDER_RES
    sc.render.resolution_percentage = 100
    try:
        sc.view_settings.view_transform = "AgX"
        sc.view_settings.look = "AgX - Medium High Contrast"
    except Exception:
        pass
    sc.view_settings.exposure = -0.3


def add_camera(name, loc, target, lens=None, vfov=None, ortho=None):
    sc = bpy.context.scene
    cd = bpy.data.cameras.new(name)
    cd.clip_start = 0.5
    cd.clip_end = 8000
    if ortho:
        cd.type = "ORTHO"
        cd.ortho_scale = ortho
    elif vfov:
        cd.sensor_fit = "VERTICAL"
        cd.angle = math.radians(vfov)
    else:
        cd.lens = lens or 24
    ob = bpy.data.objects.new(name, cd)
    sc.collection.objects.link(ob)
    ob.location = loc
    d = Vector(target) - Vector(loc)
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return ob


def setup_cameras(lay):
    HX, HY, FX, FY, PX, PY0, PY1 = lay.HX, lay.HY, lay.FX, lay.FY, lay.PX, lay.PY0, lay.PY1
    cams = {}
    cams["01_top_plaza"] = add_camera("CAM_01_top_plaza", (0, 0, 3000), (0, 0.001, 0), ortho=2 * FX + 140)
    cams["02_top_city"] = add_camera("CAM_02_top_city", (0, 0, 4000), (0, 0.001, 0), ortho=2 * (FX + 1000))
    cams["03_aerial_se"] = add_camera("CAM_03_aerial_se", (FX + 170, -FY - 230, 470), (-30, 40, 0), lens=20)
    cams["04_aerial_sw_low"] = add_camera("CAM_04_aerial_sw_low", (-HX + 25, -HY + 25, 95),
                                         (HX * 0.35, HY * 0.45, 20), lens=20)
    cams["05_player_nw_field"] = add_camera("CAM_05_player_nw_field", (-HX * 0.55, HY * 0.45, 9),
                                           (FX, FY, 75), vfov=70)
    cams["06_player_pit_floor"] = add_camera("CAM_06_player_pit_floor", (110, PY1 + 45, -PIT_D + 8),
                                            (-10, PY0, -2), vfov=70)
    cams["07_player_pit_rim"] = add_camera("CAM_07_player_pit_rim", (-PX + 30, PY0 + 22, 12),
                                          (PX * 0.4, PY1 - 60, -12), vfov=70)
    cams["08_player_se_wing"] = add_camera("CAM_08_player_se_wing", (HX * 0.62, -HY * 0.62, 9),
                                          (-FX, FY, 90), vfov=70)
    cams["09_player_edge_street"] = add_camera("CAM_09_player_edge_street", (HX - 2, HY - 70, 9),
                                              (HX + ST / 2, FY + MG_OFF, 40), vfov=70)
    cams["10_capacity_top"] = add_camera("CAM_10_capacity_top", (0, 0, 3000), (0, 0.001, 0), ortho=2 * HX + 60)
    cams["11_scale_storefront"] = add_camera("CAM_11_scale_storefront", (22, FY - 34, 9), (52, FY + 4, 14),
                                             vfov=70)
    cams["12_scale_grand_stair"] = add_camera("CAM_12_scale_grand_stair", (-14, PY0 + 22, 12), (20, PY0 - 70, -14),
                                              vfov=70)
    cams["13_player_north_frontage"] = add_camera("CAM_13_player_north_frontage", (-170, HY - 60, 8),
                                                  (-60, FY + 10, 26), vfov=70)
    cams["14_player_west_frontage"] = add_camera("CAM_14_player_west_frontage", (-HX + 45, 40, 8),
                                                 (-FX - 10, 170, 28), vfov=70)
    cams["15_player_side_street"] = add_camera("CAM_15_player_side_street", (lay.side_streets["N"] - 8, HY + 26, 8),
                                               (lay.side_streets["N"] + 6, FY + MG_OFF, 30), vfov=70)
    cams["16_player_south_frontage"] = add_camera("CAM_16_player_south_frontage", (170, -HY + 50, 8),
                                                  (300, -FY - 10, 30), vfov=70)
    cams["17_player_se_corner"] = add_camera("CAM_17_player_se_corner", (HX - 95, -HY + 55, 8),
                                             (FX + 30, -FY - 30, 45), vfov=70)
    cams["18_player_nw_corner"] = add_camera("CAM_18_player_nw_corner", (-HX + 95, HY - 55, 8),
                                             (-FX - 30, FY + 30, 45), vfov=70)
    cams["19_player_east_frontage"] = add_camera("CAM_19_player_east_frontage", (HX - 50, -40, 8),
                                                 (FX + 10, -175, 50), vfov=70)
    w = RENDER_RES[0]
    cams["01_top_plaza"]["res"] = (w, int(w * (2 * FY + 140) / (2 * FX + 140)))
    cams["02_top_city"]["res"] = (w, w)
    cams["10_capacity_top"]["res"] = (w, int(w * (2 * HY + 60) / (2 * HX + 60)))
    return cams


def render_views(cams):
    sc = bpy.context.scene
    outdir = os.path.join(REPO_DIR, "renders")
    os.makedirs(outdir, exist_ok=True)
    want = set(RENDER_VIEWS.split(",")) if RENDER_VIEWS else None
    ref_layer = bpy.context.view_layer.layer_collection.children[COLL["REF"].name]
    ghost_layer = ref_layer.children[COLL["GHOSTS"].name]
    for key, cam in cams.items():
        if want and key not in want and key.split("_", 1)[0] not in want:
            continue
        ghosts = key.startswith("10_")
        COLL["GHOSTS"].hide_render = not ghosts
        COLL["REF"].hide_render = False
        sc.camera = cam
        res = cam.get("res")
        sc.render.resolution_x, sc.render.resolution_y = tuple(res) if res else RENDER_RES
        sc.render.filepath = os.path.join(outdir, key + ".png")
        t = time.time()
        bpy.ops.render.render(write_still=True)
        print("   rendered %s in %.1fs" % (key, time.time() - t))
    COLL["GHOSTS"].hide_render = True


def all_objects(coll):
    obs = list(coll.objects)
    for c in coll.children:
        obs += all_objects(c)
    return obs


def tri_count(ob):
    if ob.type != "MESH":
        return 0
    return sum(len(p.vertices) - 2 for p in ob.data.polygons)


def export_fbx():
    outdir = os.path.join(REPO_DIR, "export", "fbx")
    os.makedirs(outdir, exist_ok=True)
    files = []
    order = ["MAIN_GROUND", "PRACTICE_AREA", "GROUND_DETAILS", "BUILDINGS_FOREGROUND", "BUILDINGS_MIDGROUND",
             "SKYLINE_BACKGROUND", "SIGNAGE", "BILLBOARD_SCREENS", "STRUCTURES"]
    for i, key in enumerate(order):
        obs = all_objects(COLL[key])
        bpy.ops.object.select_all(action="DESELECT")
        for o in obs:
            o.select_set(True)
        path = os.path.join(outdir, "HEX_City_%02d_%s.fbx" % (i + 1, key))
        bpy.ops.export_scene.fbx(filepath=path, use_selection=True, object_types={"MESH", "EMPTY"},
                                 apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS",
                                 axis_forward="-Z", axis_up="Y", use_mesh_modifiers=True,
                                 mesh_smooth_type="FACE", use_triangles=True, add_leaf_bones=False,
                                 bake_anim=False, path_mode="COPY", embed_textures=True)
        files.append(os.path.relpath(path, REPO_DIR))
        print("   exported", path)
    bpy.ops.object.select_all(action="DESELECT")
    return files


# ----------------------------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------------------------


def main():
    t0 = time.time()
    rng = random.Random(SEED)
    setup_scene()
    court = detect_court()
    if court:
        CL, CW = court["L"], court["W"]
        if CL < 40 or CL > 800:
            print("!! court size %.1f looks wrong for studs - check FBX units; using defaults" % CL)
            CL, CW = DEFAULT_COURT_L, DEFAULT_COURT_W
        CL, CW = max(CL, 1.0), max(CW, 1.0)
    else:
        print("== no court FBX found in court/ - using default court budget %.0f x %.0f studs"
              % (DEFAULT_COURT_L, DEFAULT_COURT_W))
        CL, CW = DEFAULT_COURT_L, DEFAULT_COURT_W
    # never shrink below the default budget: the map stays huge even for small courts
    lay = compute_layout(max(CL, DEFAULT_COURT_L), max(CW, DEFAULT_COURT_W))
    lay.CL, lay.CW = CL, CW
    print("== layout: plaza %.0f x %.0f, practice floor %.0f x %.0f @ %.0f"
          % (2 * lay.HX, 2 * lay.HY, 2 * lay.PX, lay.PY0 - lay.PY1, -PIT_D))
    build_library()
    plaza_pieces, streets = build_ground(lay, rng)
    build_practice(lay, rng)
    build_ground_details(lay, plaza_pieces, rng)
    build_plaza_masts(lay)
    # the city wall uses its own random streams so the plaza / practice area / skyline never change
    fg_rng = random.Random(SEED + 101)
    fg = build_foreground(lay, fg_rng)
    build_utility_lines(lay, fg_rng)
    mg = build_midground(lay, random.Random(SEED + 202))
    sky_rng = random.Random(SEED + 303)
    st_path = os.path.join(SCRIPT_DIR, "skyline_rng_state.json")
    if os.path.exists(st_path):  # frozen state -> identical background skyline across architecture passes
        st = json.load(open(st_path))
        sky_rng.setstate((st[0], tuple(st[1]), st[2]))
    sky = build_skyline(lay, sky_rng)
    build_elevated(lay, rng)
    n_main_courts = build_reference(lay, court)
    setup_world()
    cams = setup_cameras(lay)

    # hide reference collections in the saved file (viewport toggles stay available)
    lc = bpy.context.view_layer.layer_collection.children[COLL["REF"].name]
    lc.children[COLL["GHOSTS"].name].hide_viewport = True
    COLL["GHOSTS"].hide_render = True

    # report
    report = dict(units="1 Blender unit = 1 Roblox stud",
                  court=dict(source=court["path"] if court else "default budget (no FBX found)",
                             length=round(CL, 1), width=round(CW, 1)),
                  plaza=dict(size=[2 * lay.HX, 2 * lay.HY], level=0.0),
                  practice_area=dict(floor_size=[2 * lay.PX, lay.PY0 - lay.PY1], clean_floor_size=[
                      2 * lay.PX, lay.PY0 - lay.STAIRZ - lay.PY1], level=-PIT_D,
                      access=["Stair_Grand_North (64 wide)", "Stair_East (36 wide)", "Stair_West (36 wide)"]),
                  frontage_line=[lay.FX, lay.FY], street_width=ST, sidewalk_width=SW,
                  court_capacity_main_level=n_main_courts, collections={})
    worst = []
    for key in ("MAIN_GROUND", "PRACTICE_AREA", "GROUND_DETAILS", "BUILDINGS_FOREGROUND", "BUILDINGS_MIDGROUND",
                "SKYLINE_BACKGROUND", "SIGNAGE", "BILLBOARD_SCREENS", "STRUCTURES"):
        obs = all_objects(COLL[key])
        tris = [tri_count(o) for o in obs]
        report["collections"][key] = dict(objects=len(obs), triangles=sum(tris), max_tris_per_object=max(tris or [0]))
        for o in obs:
            if o.type == "MESH":
                dims = o.dimensions
                if max(dims) > 2048 or tri_count(o) > 20000:
                    worst.append((o.name, [round(v) for v in dims], tri_count(o)))
    report["over_roblox_limits"] = worst
    report["screens"] = sorted(o.name for o in SCREENS)
    cnt = {}
    for o in SIGNS + SCREENS:
        key = o.name.rsplit("_", 1)[0]
        cnt[key] = cnt.get(key, 0) + 1
    report["sign_and_screen_counts"] = dict(sorted(cnt.items()))
    report["total_triangles"] = sum(v["triangles"] for v in report["collections"].values())
    with open(os.path.join(REPO_DIR, "blender", "layout_report.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "screens"}, indent=1))

    if DO_SAVE:
        path = os.path.join(REPO_DIR, "blender", "HEX_City_Map.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
        print("== saved", path)
    if DO_EXPORT:
        report["fbx"] = export_fbx()
    if DO_RENDER:
        render_views(cams)
    print("== done in %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
