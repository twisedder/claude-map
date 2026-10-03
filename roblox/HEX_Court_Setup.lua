--[[ HEX! Tokyo courts — run once in the Studio Command Bar after importing the court FBX files.
     Makes the backboard glass see-through, keeps net cords / graphics from blocking play. ]]
for _, d in ipairs(workspace:GetDescendants()) do
	if d:IsA("MeshPart") then
		local n = d.Name
		d.Anchored = true
		if n:match("Backboard_Glass") then
			d.Transparency = 0.55
			d.Material = Enum.Material.Glass
		end
		if n:match("^NetCord") or n:match("^Graphics") or n == "Lines" or n:match("^Floor_Paint")
			or n:match("^Floor_ThreePoint") then
			d.CanCollide = false
			d.CanQuery = false
			d.CastShadow = false
		end
	end
end
print("HEX court setup done")
