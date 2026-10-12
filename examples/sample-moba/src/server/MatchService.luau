--!strict
local MatchService = {}
function MatchService.CanStart(players: number): boolean
	return players >= 10
end
return MatchService
