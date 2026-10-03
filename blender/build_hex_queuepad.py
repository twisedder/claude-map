"""
HEX! Tokyo queue pad (stand on it to join a game) — matches the Tokyo courts.

Dark graphite pad with a soft rounded rim, deep-navy inset top, cool-white rings and a sakura-pink
accent ring (separate part so a script can light it up). No text, no blossom.
Same units/colour pipeline as build_hex_courts.py (import exactly like the courts).

Run: blender --background --python blender/build_hex_queuepad.py -- [--render]
"""
import os
import sys
import math
import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_hex_courts as C  # noqa: E402  (shared materials, mesh builder, palette bake, export)

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

R_PAD = 3.6      # outer radius (≈ 7 studs across: one avatar stands comfortably)
H_PAD = 0.32     # thickness above the floor
N = 64           # radial segments


def lathe(mb, prof, m, n=N):
    """Revolve a (r, z) profile (inner->outer->top order) around Z."""
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        for (r0, z0), (r1, z1) in zip(prof[:-1], prof[1:]):
            p = [(r0 * math.cos(a0), r0 * math.sin(a0), z0), (r1 * math.cos(a0), r1 * math.sin(a0), z1),
                 (r1 * math.cos(a1), r1 * math.sin(a1), z1), (r0 * math.cos(a1), r0 * math.sin(a1), z0)]
            mb.face(p, m)


def disc(mb, r, z, m, n=N):
    mb.face([(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z) for i in range(n)], m)


def build():
    coll = bpy.data.collections.new("HEX_Tokyo_QueuePad")
    bpy.context.scene.collection.children.link(coll)
    root = C.empty("HEX_Tokyo_QueuePad", coll)

    # base: graphite body with a softly rounded outer rim (profile goes outer-bottom -> up -> over the top)
    base = C.MB()
    rim = []
    for k in range(7):  # quarter-round rim
        a = math.radians(90 * k / 6)
        rim.append((R_PAD - 0.22 + 0.22 * math.cos(a), H_PAD - 0.22 + 0.22 * math.sin(a)))
    prof = [(R_PAD, 0.0)] + rim + [(R_PAD - 0.5, H_PAD), (R_PAD - 0.55, H_PAD - 0.04)]
    lathe(base, prof[::-1], "Steel_Dark")
    base.build("Pad_Base", coll, root, smooth=True)

    # recessed navy top with a small chamfer step
    top = C.MB()
    lathe(top, [(R_PAD - 0.62, H_PAD - 0.06), (R_PAD - 0.55, H_PAD - 0.04)], "Court_Navy")
    disc(top, R_PAD - 0.62, H_PAD - 0.06, "Court_Navy")
    top.build("Pad_Top", coll, root, smooth=True)

    # cool-white rings (outer boundary + centre standing spot), flat graphics like the court lines
    ln = C.MB()
    z = H_PAD - 0.05
    ln.ring(0, 0, R_PAD - 0.95, R_PAD - 0.82, z, N, "Line_WarmWhite")
    ln.ring(0, 0, 0.95, 1.05, z, N, "Line_WarmWhite")
    for k in range(4):  # four short ticks pointing at the centre
        a = math.pi / 4 + k * math.pi / 2
        c, s = math.cos(a), math.sin(a)
        r0, r1, w = 1.35, 1.95, 0.06
        ln.face([(r0 * c - w * s, r0 * s + w * c, z), (r0 * c + w * s, r0 * s - w * c, z),
                 (r1 * c + w * s, r1 * s - w * c, z), (r1 * c - w * s, r1 * s + w * c, z)], "Line_WarmWhite")
    ln.build("Pad_Lines", coll, root)

    # sakura accent ring: its own part so scripts can glow/tween it (e.g. Neon when occupied)
    glow = C.MB()
    glow.ring(0, 0, R_PAD - 1.18, R_PAD - 1.06, z + 0.004, N, "Sakura")
    glow.build("Pad_GlowRing", coll, root)
    return root


def preview(root):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 32
    sc.cycles.use_denoising = True
    w = bpy.data.worlds.new("w")
    sc.world = w
    w.color = (0.35, 0.38, 0.45)
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.5
    sun.rotation_euler = (math.radians(45), 0.2, math.radians(-30))
    sc.collection.objects.link(sun)
    me = bpy.data.meshes.new("g")  # backdrop: court graphite (render only)
    me.from_pydata([(-30, -30, 0), (30, -30, 0), (30, 30, 0), (-30, 30, 0)], [], [(0, 1, 2, 3)])
    me.materials.append(C.mat("Court_Graphite"))
    sc.collection.objects.link(bpy.data.objects.new("g", me))
    # 5.2-stud reference avatar (render only)
    d = C.MB()
    for b in ((-1.0, -0.5, 0.32, -0.05, 0.5, 2.32), (0.05, -0.5, 0.32, 1.0, 0.5, 2.32), (-1.0, -0.5, 2.32, 1.0, 0.5, 4.32),
              (-2.0, -0.5, 2.32, -1.05, 0.5, 4.32), (1.05, -0.5, 2.32, 2.0, 0.5, 4.32), (-0.6, -0.6, 4.32, 0.6, 0.6, 5.52)):
        d.box(*b, "Concrete")
    d.build("avatar", bpy.context.scene.collection)
    out = os.path.join(C.REPO, "renders", "courts")
    os.makedirs(out, exist_ok=True)
    for name, loc, tgt, ortho in (("queuepad_top", (0, 0, 40), (0, 0.001, 0), 9.0),
                                  ("queuepad_persp", (7.5, -9.5, 6.5), (0, 0, 1.2), None)):
        cd = bpy.data.cameras.new(name)
        cam = bpy.data.objects.new(name, cd)
        sc.collection.objects.link(cam)
        cam.location = loc
        cam.rotation_euler = (Vector(tgt) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        if ortho:
            cd.type = "ORTHO"
            cd.ortho_scale = ortho
            if name == "queuepad_top":
                bpy.data.objects["avatar"].hide_render = True
        else:
            cd.lens = 35
            bpy.data.objects["avatar"].hide_render = False
        sc.camera = cam
        sc.render.resolution_x, sc.render.resolution_y = (1000, 1000) if ortho else (1400, 900)
        sc.render.filepath = os.path.join(out, name + ".png")
        bpy.ops.render.render(write_still=True)
        print("   rendered", name)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    root = build()
    if "--render" in ARGV:
        preview(root)
        for n in ("avatar", "g"):
            if n in bpy.data.objects:
                bpy.data.objects.remove(bpy.data.objects[n])
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "HEX_Tokyo_QueuePad.blend"), compress=True)
    objs = C.all_children(root)
    C.palette_bake(objs, fname="queuepad_palette.png")
    C.to_reference_units(objs)
    C.export(root, "HEX_Tokyo_QueuePad.fbx")


if __name__ == "__main__":
    main()
