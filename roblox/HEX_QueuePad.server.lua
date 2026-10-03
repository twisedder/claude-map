--[[ HEX! Tokyo queue pad — put this Script in ServerScriptService.
     Works for every imported model named "HEX_Tokyo_QueuePad..." in the workspace.

     Standing on a pad lights its sakura ring and sets attributes on the pad model:
       Occupied  (bool)    – someone is on the pad
       PlayerId  (number)  – UserId of the player on it (0 when empty)
     It also fires the BindableEvent ReplicatedStorage.HEX_QueueChanged(pad, player|nil),
     so your match/queue system can listen without touching this script. ]]

local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local TweenService = game:GetService("TweenService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local IDLE = Color3.fromRGB(227, 163, 184)   -- Sakura
local LIT = Color3.fromRGB(240, 127, 165)    -- Sakura_Bright
local CHECK_EVERY = 0.15
local TWEEN = TweenInfo.new(0.25, Enum.EasingStyle.Quad, Enum.EasingDirection.Out)

local changed = ReplicatedStorage:FindFirstChild("HEX_QueueChanged") or Instance.new("BindableEvent")
changed.Name = "HEX_QueueChanged"
changed.Parent = ReplicatedStorage

local pads = {}

local function setup(model)
	if pads[model] then return end
	local base = model:FindFirstChild("Pad_Base", true)
	local ring = model:FindFirstChild("Pad_GlowRing", true)
	if not (base and ring) then return end
	for _, d in ipairs(model:GetDescendants()) do
		if d:IsA("BasePart") then
			d.Anchored = true
			if d.Name:match("^Pad_Lines") or d.Name:match("^Pad_GlowRing") then
				d.CanCollide = false
				d.CanQuery = false
				d.CastShadow = false
			end
		end
	end
	ring.Material = Enum.Material.Neon
	ring.Color = IDLE
	ring.Transparency = 0.35

	-- round pad: its largest dimension is the diameter (works whatever axis the import used)
	local radius = math.max(base.Size.X, base.Size.Y, base.Size.Z) / 2 * 0.85

	model:SetAttribute("Occupied", false)
	model:SetAttribute("PlayerId", 0)
	pads[model] = { ring = ring, base = base, radius = radius, player = nil }
end

local function scan()
	for _, d in ipairs(workspace:GetDescendants()) do
		if d:IsA("Model") and d.Name:match("^HEX_Tokyo_QueuePad") then setup(d) end
	end
end
scan()
workspace.DescendantAdded:Connect(function(d)
	if d:IsA("Model") and d.Name:match("^HEX_Tokyo_QueuePad") then task.defer(setup, d) end
end)

local function isOn(s, plr)
	local char = plr and plr.Parent and plr.Character
	local root = char and char:FindFirstChild("HumanoidRootPart")
	local hum = char and char:FindFirstChildOfClass("Humanoid")
	if not (root and hum and hum.Health > 0) then return false end
	local d = root.Position - s.base.Position
	return Vector2.new(d.X, d.Z).Magnitude <= s.radius and d.Y > -1 and d.Y < 8
end

local function findPlayer(s, taken)
	for _, plr in ipairs(Players:GetPlayers()) do
		if not taken[plr] and isOn(s, plr) then return plr end
	end
end

local acc = 0
RunService.Heartbeat:Connect(function(dt)
	acc += dt
	if acc < CHECK_EVERY then return end
	acc = 0
	local taken = {}
	for model, s in pairs(pads) do
		if not model.Parent then pads[model] = nil continue end
		-- keep the current player while they stay on the pad (one player per pad)
		local plr = s.player
		if not isOn(s, plr) then plr = findPlayer(s, taken) end
		if plr then taken[plr] = true end
		if plr ~= s.player then
			s.player = plr
			model:SetAttribute("Occupied", plr ~= nil)
			model:SetAttribute("PlayerId", plr and plr.UserId or 0)
			TweenService:Create(s.ring, TWEEN, {
				Color = plr and LIT or IDLE,
				Transparency = plr and 0 or 0.35,
			}):Play()
			changed:Fire(model, plr)
		end
	end
end)
