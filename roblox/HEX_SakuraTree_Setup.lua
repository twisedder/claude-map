--[[ HEX! Tokyo sakura tree — run once in the Studio Command Bar after importing HEX_Sakura_Tree.fbx.
     Trunk + branches stay solid; blossoms and petals never block players or raycasts. ]]
for _, m in ipairs(workspace:GetDescendants()) do
	if m:IsA("Model") and m.Name:match("^HEX_Sakura_Tree") then
		for _, d in ipairs(m:GetDescendants()) do
			if d:IsA("MeshPart") then
				local n = d.Name
				d.Anchored = true
				if n:match("^Trunk") or n:match("^Branches") then
					d.CanCollide = true
					pcall(function() d.CollisionFidelity = Enum.CollisionFidelity.Hull end)
				else
					d.CanCollide = false
					d.CanQuery = false
					d.CanTouch = false
					d.CastShadow = n:match("^Canopy") ~= nil -- blossoms cast shade, loose petals don't
					pcall(function() d.CollisionFidelity = Enum.CollisionFidelity.Box end)
				end
			end
		end
	end
end
print("HEX sakura tree setup done")
