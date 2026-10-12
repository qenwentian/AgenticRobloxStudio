--!strict
local Hud = {}
function Hud.HealthFraction(health: number, maximum: number): number
	assert(maximum > 0, "Maximum health must be positive")
	return math.clamp(health / maximum, 0, 1)
end
return Hud
