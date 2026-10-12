--!strict
export type DamageRequest = { BaseDamage: number, Armor: number }
export type DamageResult = { FinalDamage: number }
local CombatService = {}
function CombatService.ApplyDamage(request: DamageRequest): DamageResult
	assert(request.BaseDamage >= 0 and request.Armor >= 0, "Damage and armor must be nonnegative")
	return { FinalDamage = request.BaseDamage * 100 / (100 + request.Armor) }
end
return CombatService
