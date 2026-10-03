# HEX! Tokyo sakura tree

`HEX_Sakura_Tree.fbx` → `HEX_Sakura_Tree`
- `Trunk`: leaning trunk with spiral bark ridges, light lenticel streaks and 7 flared roots.
- `Branches`: 5 limbs that branch down to the twigs.
- `Canopy_01…09`: 731 fluffy blossom clumps, shaded white on top to deep pink underneath. About 1,000 five-petal
  sakura flowers with notched petals sit on the clumps and in bunches at the twig tips. The crown is split into
  parts of 9k triangles or fewer each.
- `Petals_Ground`: 420 fallen petals.
- `Petals_Air`: 90 drifting petals.

**Size:** about 26 studs tall, with a crown about 38 studs wide. The canopy bottom is about 10 studs up, so players
walk under it.

**Cost:** about 68k triangles in total, using one embedded palette texture, the same colour pipeline as the courts.

**Import:** import it exactly like the courts. Then paste `roblox/HEX_SakuraTree_Setup.lua` into the Command Bar.
The trunk and branches stay solid, while the blossoms and petals don't collide and can't be hit by raycasts.

Delete `Petals_Ground` and `Petals_Air` if you only want the tree.

Rebuild or vary from source: `blender --background --python blender/build_hex_sakura_tree.py -- [--render]`.
Change `SEED` in the script to get a different tree.
