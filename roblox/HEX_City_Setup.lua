--[[
	HEX! City — one-time setup. Paste into the Studio Command Bar after importing the FBX files.
	- anchors everything
	- LED strips -> Neon
	- signs, screens, skyline, ground paint, cables: no collision / no queries / no shadows (performance)
	- buildings: Box collision (cheap and solid)
]]
local n = 0
for _, d in ipairs(workspace:GetDescendants()) do
	if d:IsA("MeshPart") then
		local name = d.Name
		d.Anchored = true
		if name:match("_LED") then
			d.Material = Enum.Material.Neon
			d.CastShadow = false
		end
		if name:match("^Billboard_") or name:match("^Sign_") or name:match("^Skyline_")
			or name:match("ExpansionJoints") or name:match("DrainChannels") or name:match("Street_Paint")
			or name:match("UtilityLines") or name:match("_LED") then
			d.CanCollide = false
			d.CanQuery = false
			d.CastShadow = false
		end
		if name:match("^FG_") or name:match("^MG_") then
			pcall(function() d.CollisionFidelity = Enum.CollisionFidelity.Box end)
		end
		n += 1
	end
end
print("HEX city setup done on " .. n .. " MeshParts")
