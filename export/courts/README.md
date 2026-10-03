# HEX! Tokyo courts (full + half)

Only the courts and hoops: no props, benches, scoreboards, lights or surroundings.

| File | Contents |
|---|---|
| `HEX_Tokyo_FullCourt.fbx` | `HEX_Tokyo_FullCourt` → Floor, Lines, Graphics, Border, Hoop_A, Hoop_B |
| `HEX_Tokyo_HalfCourt.fbx` | `HEX_Tokyo_HalfCourt` → Floor, Lines, Graphics, Border, Hoop |
| `optional_*_NetAnchors.fbx` | RimAnchor / PhysicsNode points at the new net nodes, with the same names as v4 (only if your net script needs them) |

**Scale:** this file uses the same unit convention as `HEX_Basketball_Court_v4.fbx` (metre FBX units, each mesh
scaled ×3.571), so **import it exactly the way you imported v4** and it will match.

- Playing surface 120.7 × 66.4.
- Rim centre ±53.68, rim top 8.795.
- Backboard face ±54.93, 6.53 × 3.82.
- The court lines are your v4 line geometry.

**Look:** dark graphite surface, deep navy keys, cool-white lines, one thin sakura-pink perimeter line, and a
geometric sakura blossom at centre court. No text, no orange decoration. Hoops: square clear backboard, orange rim,
white net (60 separate cords per hoop, named `NetCord_<A|B>_L<level>_<i>_<j>` like v4), smooth graphite/navy
pole and arm.

**After import:** paste `roblox/HEX_Court_Setup.lua` into the Command Bar (makes the glass transparent and stops
lines/graphics/net cords from colliding).

Rebuild from source: `blender --background --python blender/build_hex_courts.py -- [--render]`
