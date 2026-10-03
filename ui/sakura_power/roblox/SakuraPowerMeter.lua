--!strict
--[[
	HEX! — Sakura Power meter (ModuleScript)

	1. Upload the PNGs in ui/sakura_power/png/ (Creator Hub → Decals/Images) and paste the
	   rbxassetid numbers into ASSETS below.
	2. Put this ModuleScript in ReplicatedStorage, then from a LocalScript:

		local Meter = require(game.ReplicatedStorage.SakuraPowerMeter)
		local meter = Meter.new(game.Players.LocalPlayer:WaitForChild("PlayerGui"))
		meter:SetPerfectWindow(0.86, 0.94)
		meter:SetPower(0.5)            -- 0..1
		meter:Release()                -- flashes + petal burst if inside the perfect window

	Layer order (bottom → top): glow, back, fill (clipped from the bottom) + cap, overlay, marker.
	All 512x1024 layers share one canvas, so they simply stack at the same size.
]]

local TweenService = game:GetService("TweenService")

local ASSETS = {
	back = "rbxassetid://0",    -- sakura_power_back.png     512x1024
	overlay = "rbxassetid://0", -- sakura_power_overlay.png  512x1024
	glow = "rbxassetid://0",    -- sakura_power_glow.png     512x1024
	fill = "rbxassetid://0",    -- sakura_power_fill.png     92x722
	cap = "rbxassetid://0",     -- sakura_power_cap.png      92x28
	marker = "rbxassetid://0",  -- sakura_power_marker.png   160x40
	petal = "rbxassetid://0",   -- sakura_power_petal.png    128x128
}

-- track rectangle inside the 512x1024 canvas (pixels): x 264, y 204, w 92, h 722
local CANVAS = Vector2.new(512, 1024)
local TRACK_POS = Vector2.new(264 / 512, 204 / 1024)
local TRACK_SIZE = Vector2.new(92 / 512, 722 / 1024)

local Meter = {}
Meter.__index = Meter

local function image(name: string, parent: Instance, z: number): ImageLabel
	local img = Instance.new("ImageLabel")
	img.Name = name
	img.BackgroundTransparency = 1
	img.Image = ASSETS[name]
	img.Size = UDim2.fromScale(1, 1)
	img.ZIndex = z
	img.Parent = parent
	return img
end

export type SakuraMeter = typeof(setmetatable({} :: {
	gui: ScreenGui, root: Frame, clip: Frame, fill: ImageLabel, cap: ImageLabel, glow: ImageLabel,
	marker: ImageLabel, label: TextLabel, power: number, lo: number, hi: number,
}, Meter))

function Meter.new(parent: Instance, height: number?): SakuraMeter
	local h = height or 360
	local gui = Instance.new("ScreenGui")
	gui.Name = "SakuraPowerMeter"
	gui.ResetOnSpawn = false
	gui.IgnoreGuiInset = true
	gui.Parent = parent

	local root = Instance.new("Frame")
	root.Name = "Meter"
	root.BackgroundTransparency = 1
	root.AnchorPoint = Vector2.new(1, 0.5)
	root.Position = UDim2.new(1, -24, 0.5, 0)
	root.Size = UDim2.fromOffset(h * CANVAS.X / CANVAS.Y, h)
	root.Parent = gui

	local glow = image("glow", root, 1)
	glow.ImageTransparency = 1
	image("back", root, 2)

	local track = Instance.new("Frame")
	track.Name = "Track"
	track.BackgroundTransparency = 1
	track.Position = UDim2.fromScale(TRACK_POS.X, TRACK_POS.Y)
	track.Size = UDim2.fromScale(TRACK_SIZE.X, TRACK_SIZE.Y)
	track.ZIndex = 3
	track.Parent = root

	local clip = Instance.new("Frame")
	clip.Name = "Clip"
	clip.BackgroundTransparency = 1
	clip.ClipsDescendants = true
	clip.AnchorPoint = Vector2.new(0, 1)
	clip.Position = UDim2.fromScale(0, 1)
	clip.Size = UDim2.fromScale(1, 0)
	clip.ZIndex = 3
	clip.Parent = track

	-- the fill image always spans the full track height, so its gradient stays fixed while the clip grows
	local fill = image("fill", clip, 3)
	fill.AnchorPoint = Vector2.new(0, 1)
	fill.Position = UDim2.fromScale(0, 1)
	local function syncFill()
		fill.Size = UDim2.new(1, 0, 0, track.AbsoluteSize.Y)
	end
	track:GetPropertyChangedSignal("AbsoluteSize"):Connect(syncFill)
	syncFill()

	local cap = image("cap", track, 4)
	cap.AnchorPoint = Vector2.new(0, 0.5)
	cap.Size = UDim2.fromScale(1, 28 / 722)

	image("overlay", root, 5)

	local marker = image("marker", track, 6)
	marker.AnchorPoint = Vector2.new(0.5, 0.5)
	marker.Size = UDim2.fromScale(160 / 92, 40 / 722)

	local label = Instance.new("TextLabel")
	label.Name = "Percent"
	label.BackgroundTransparency = 1
	label.AnchorPoint = Vector2.new(1, 0.5)
	label.Position = UDim2.fromScale(0.42, 0.94)
	label.Size = UDim2.fromScale(0.5, 0.05)
	label.Font = Enum.Font.GothamBlack
	label.TextScaled = true
	label.TextXAlignment = Enum.TextXAlignment.Right
	label.TextColor3 = Color3.fromRGB(255, 240, 247)
	label.TextStrokeColor3 = Color3.fromRGB(42, 18, 25)
	label.TextStrokeTransparency = 0.2
	label.ZIndex = 7
	label.Text = "0%"
	label.Parent = root

	local self = setmetatable({
		gui = gui, root = root, clip = clip, fill = fill, cap = cap, glow = glow, marker = marker,
		label = label, power = 0, lo = 0.86, hi = 0.94,
	}, Meter)
	self:SetPerfectWindow(self.lo, self.hi)
	self:SetPower(0)
	return self
end

function Meter.SetPower(self: SakuraMeter, p: number)
	p = math.clamp(p, 0, 1)
	self.power = p
	self.clip.Size = UDim2.fromScale(1, p)
	self.cap.Position = UDim2.fromScale(0, 1 - p)
	self.cap.Visible = p > 0.01 and p < 0.995
	self.label.Text = string.format("%d%%", math.floor(p * 100 + 0.5))
	self.glow.ImageTransparency = p >= 0.995 and 0.15 or 1
end

-- perfect-release window (0..1); the marker sits in the middle of it
function Meter.SetPerfectWindow(self: SakuraMeter, lo: number, hi: number)
	self.lo, self.hi = lo, hi
	self.marker.Position = UDim2.fromScale(0.5, 1 - (lo + hi) / 2)
end

function Meter.IsPerfect(self: SakuraMeter): boolean
	return self.power >= self.lo and self.power <= self.hi
end

-- call on release: glow flash + sakura petal burst when perfect. Returns true if perfect.
function Meter.Release(self: SakuraMeter): boolean
	local perfect = self:IsPerfect()
	if perfect then
		self.glow.ImageTransparency = 0
		TweenService:Create(self.glow, TweenInfo.new(0.6, Enum.EasingStyle.Quad), { ImageTransparency = 1 }):Play()
		for _ = 1, 14 do
			local petal = Instance.new("ImageLabel")
			petal.BackgroundTransparency = 1
			petal.Image = ASSETS.petal
			petal.AnchorPoint = Vector2.new(0.5, 0.5)
			petal.Position = UDim2.fromScale(0.6, 1 - self.power * 0.7 - 0.2)
			local s = math.random(14, 26)
			petal.Size = UDim2.fromOffset(s, s)
			petal.Rotation = math.random(0, 360)
			petal.ZIndex = 8
			petal.Parent = self.root
			local tw = TweenService:Create(petal, TweenInfo.new(0.9 + math.random() * 0.5, Enum.EasingStyle.Quad), {
				Position = petal.Position + UDim2.fromOffset(math.random(-140, 60), math.random(-90, 160)),
				Rotation = petal.Rotation + math.random(-220, 220),
				ImageTransparency = 1,
			})
			tw.Completed:Once(function()
				petal:Destroy()
			end)
			tw:Play()
		end
	end
	return perfect
end

function Meter.SetVisible(self: SakuraMeter, visible: boolean)
	self.gui.Enabled = visible
end

function Meter.Destroy(self: SakuraMeter)
	self.gui:Destroy()
end

return Meter
