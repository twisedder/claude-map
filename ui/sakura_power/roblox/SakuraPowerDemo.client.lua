-- Demo LocalScript (StarterPlayerScripts): hold left mouse / touch to charge, release to "shoot".
local Players = game:GetService("Players")
local UserInputService = game:GetService("UserInputService")
local RunService = game:GetService("RunService")

local Meter = require(game:GetService("ReplicatedStorage"):WaitForChild("SakuraPowerMeter"))
local meter = Meter.new(Players.LocalPlayer:WaitForChild("PlayerGui"))
meter:SetPerfectWindow(0.86, 0.94)

local CHARGE_TIME = 1.1 -- seconds from empty to full
local charging, power = false, 0

UserInputService.InputBegan:Connect(function(input, processed)
	if processed then return end
	if input.UserInputType == Enum.UserInputType.MouseButton1 or input.UserInputType == Enum.UserInputType.Touch then
		charging, power = true, 0
	end
end)

UserInputService.InputEnded:Connect(function(input)
	if charging and (input.UserInputType == Enum.UserInputType.MouseButton1
		or input.UserInputType == Enum.UserInputType.Touch) then
		charging = false
		local perfect = meter:Release()
		print(("Released at %d%% (%s)"):format(math.floor(power * 100), perfect and "PERFECT" or "off"))
		task.delay(0.8, function()
			if not charging then meter:SetPower(0) end
		end)
	end
end)

RunService.RenderStepped:Connect(function(dt)
	if charging then
		power = math.min(1, power + dt / CHARGE_TIME)
		meter:SetPower(power)
	end
end)
