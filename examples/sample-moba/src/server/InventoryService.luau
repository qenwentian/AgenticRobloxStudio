--!strict
local InventoryService = {}
function InventoryService.TotalPower(items: { number }): number
	local total = 0
	for _, power in items do
		total += power
	end
	return total
end
return InventoryService
