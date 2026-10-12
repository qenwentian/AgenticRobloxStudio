--!strict
local AbilityService = {}
function AbilityService.Cast(applyDamage: (any) -> any, baseDamage: number, armor: number): any
	return applyDamage({ BaseDamage = baseDamage, Armor = armor })
end
return AbilityService
