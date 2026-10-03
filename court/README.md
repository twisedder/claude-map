# Put your HEX court here

Drop your correctly-scaled HEX basketball court FBX in this folder (any `*.fbx` name), then rebuild:

```
blender --background --python blender/build_hex_city.py -- --export --render
```

The build script imports the court **first**, measures its real footprint, and grows the map
if your court is bigger than the default budget (150 x 85 studs). It never resizes your court,
and the court is never exported with the map. It is placed in the hidden
`SCALE_REFERENCE (not exported)` collection so you can check the fit in Blender.

You can also point at a file elsewhere with the env var `HEX_COURT_FBX=/path/to/court.fbx`.
