--!strict
-- StudioSync never overwrites source or modifies the game's DataModel.
local HttpService = game:GetService("HttpService")
local Selection = game:GetService("Selection")
local ScriptEditorService = game:GetService("ScriptEditorService")
local UserInputService = game:GetService("UserInputService")

local toolbar = plugin:CreateToolbar("StudioSync AI")
local toggle = toolbar:CreateButton("StudioSync", "Open shared coordination dashboard", "")
toggle.ClickableWhenViewportHidden = true
local widget = plugin:CreateDockWidgetPluginGuiAsync(
	"StudioSyncAI-v1",
	DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Right, false, false, 430, 650, 300, 350)
)
widget.Title = "StudioSync AI"
toggle.Click:Connect(function()
	widget.Enabled = not widget.Enabled
end)
widget:GetPropertyChangedSignal("Enabled"):Connect(function()
	toggle:SetActive(widget.Enabled)
end)

local background = Color3.fromRGB(22, 25, 32)
local panel = Color3.fromRGB(34, 39, 49)
local foreground = Color3.fromRGB(222, 229, 241)
local accent = Color3.fromRGB(113, 195, 170)
local root = Instance.new("Frame")
root.BackgroundColor3 = background
root.BorderSizePixel = 0
root.Size = UDim2.fromScale(1, 1)
root.Parent = widget
local padding = Instance.new("UIPadding")
padding.PaddingLeft = UDim.new(0, 12)
padding.PaddingRight = UDim.new(0, 12)
padding.PaddingTop = UDim.new(0, 10)
padding.PaddingBottom = UDim.new(0, 10)
padding.Parent = root
local layout = Instance.new("UIListLayout")
layout.Padding = UDim.new(0, 7)
layout.SortOrder = Enum.SortOrder.LayoutOrder
layout.Parent = root

local order = 0
local function label(parent: Instance, text: string, height: number): TextLabel
	order += 1
	local item = Instance.new("TextLabel")
	item.BackgroundTransparency = 1
	item.TextColor3 = foreground
	item.Font = Enum.Font.SourceSans
	item.TextSize = 16
	item.TextXAlignment = Enum.TextXAlignment.Left
	item.TextYAlignment = Enum.TextYAlignment.Top
	item.TextWrapped = true
	item.Text = text
	item.Size = UDim2.new(1, 0, 0, height)
	item.LayoutOrder = order
	item.Parent = parent
	return item
end

local function box(parent: Instance, placeholder: string, height: number): TextBox
	order += 1
	local item = Instance.new("TextBox")
	item.BackgroundColor3 = panel
	item.BorderSizePixel = 0
	item.TextColor3 = foreground
	item.PlaceholderColor3 = Color3.fromRGB(133, 147, 169)
	item.Font = Enum.Font.SourceSans
	item.TextSize = 16
	item.TextXAlignment = Enum.TextXAlignment.Left
	item.TextWrapped = height > 30
	item.MultiLine = height > 30
	item.ClearTextOnFocus = false
	item.PlaceholderText = placeholder
	item.Text = ""
	item.Size = UDim2.new(1, 0, 0, height)
	item.LayoutOrder = order
	item.Parent = parent
	local inset = Instance.new("UIPadding")
	inset.PaddingLeft = UDim.new(0, 8)
	inset.PaddingRight = UDim.new(0, 8)
	inset.Parent = item
	return item
end

local function button(parent: Instance, text: string, callback: () -> ()): TextButton
	order += 1
	local item = Instance.new("TextButton")
	item.BackgroundColor3 = panel
	item.BorderSizePixel = 0
	item.TextColor3 = accent
	item.Font = Enum.Font.SourceSansSemibold
	item.TextSize = 15
	item.Text = text
	item.Size = UDim2.new(1, 0, 0, 30)
	item.LayoutOrder = order
	item.Parent = parent
	item.Activated:Connect(callback)
	return item
end

local heading = label(root, "STUDIOSYNC AI  /  Shared coordination", 23)
heading.Font = Enum.Font.SourceSansSemibold
local connection = label(root, "Disconnected · Pair your local bridge in Settings", 36)
local notice = label(root, "Repository files remain the source of truth in Git + Rojo mode.", 35)
notice.TextColor3 = Color3.fromRGB(153, 168, 191)

local navigation = Instance.new("Frame")
navigation.BackgroundTransparency = 1
navigation.Size = UDim2.new(1, 0, 0, 62)
navigation.LayoutOrder = 4
navigation.Parent = root
local grid = Instance.new("UIGridLayout")
grid.CellSize = UDim2.new(1 / 3, -5, 0, 28)
grid.CellPadding = UDim2.fromOffset(5, 5)
grid.SortOrder = Enum.SortOrder.LayoutOrder
grid.Parent = navigation
local content = Instance.new("ScrollingFrame")
content.BackgroundTransparency = 1
content.BorderSizePixel = 0
content.Size = UDim2.new(1, 0, 1, -180)
content.AutomaticCanvasSize = Enum.AutomaticSize.Y
content.CanvasSize = UDim2.fromOffset(0, 0)
content.ScrollBarThickness = 5
content.LayoutOrder = 5
content.Parent = root
local contentLayout = Instance.new("UIListLayout")
contentLayout.Padding = UDim.new(0, 7)
contentLayout.SortOrder = Enum.SortOrder.LayoutOrder
contentLayout.Parent = content

local address: string = plugin:GetSetting("StudioSyncAddress") or "http://127.0.0.1:8766"
local interval: number = tonumber(plugin:GetSetting("StudioSyncInterval")) or 5
local readOnly: boolean = plugin:GetSetting("StudioSyncReadOnly") == true
local notifications: boolean = plugin:GetSetting("StudioSyncNotifications") ~= false
local token: string? = nil -- Short-lived Studio credential stays in memory, never in plugin settings.
local tokenExpiry = 0
local sessionId: string? = nil
local workspaceId: string? = nil
local developerId: string? = nil
local data: any = nil
local currentTab = "Settings"
local filter = ""
local selectedAgent: any = nil
local selectedTask: any = nil
local selectionResource = ""
local held: { [string]: any } = {}
local running = true
local networkBusy = false
local backoff = 1
local refresh: () -> ()
local render: () -> ()

local function request(method: string, path: string, payload: any?): (boolean, any)
	if
		not string.match(address, "^http://127%.0%.0%.1:%d+$")
		and not string.match(address, "^http://localhost:%d+$")
	then
		return false, "Use a localhost bridge URL with an explicit port"
	end
	local headers: { [string]: string } = { ["Content-Type"] = "application/json" }
	if token then
		headers["X-Bridge-Token"] = token
	end
	local options: any = { Url = address .. path, Method = method, Headers = headers }
	if payload then
		options.Body = HttpService:JSONEncode(payload)
	end
	local ok, response = pcall(function()
		return HttpService:RequestAsync(options)
	end)
	if not ok then
		return false, "HTTP failed. Check local bridge and Studio plugin HTTP permission."
	end
	local decoded, result = pcall(function()
		return HttpService:JSONDecode(response.Body)
	end)
	if not decoded then
		return false, "Bridge returned invalid JSON"
	end
	if not response.Success or result.ok == false then
		local errorData = result.error
		if errorData and errorData.code == "UNAUTHENTICATED" then
			token = nil
		end
		return false,
			if errorData
				then errorData.code .. ": " .. errorData.message
				else "HTTP " .. tostring(response.StatusCode)
	end
	return true, result
end

local function act(operation: string, payload: any, callback: ((any) -> ())?)
	if readOnly then
		notice.Text =
			"Read-only mode is enabled. Change it in Settings to request ownership or send messages."
		return
	end
	if not token then
		notice.Text = "Pair the local bridge first."
		return
	end
	task.spawn(function()
		local ok, result = request("POST", "/actions/" .. operation, payload)
		if not running then
			return
		end
		if ok then
			notice.Text = operation:gsub("_", " ") .. " confirmed by server"
			if callback then
				callback(result.data)
			end
			refresh()
		else
			notice.Text = tostring(result)
		end
	end)
end

local function agentName(id: string?): string
	for _, agent in (if data then data.agents else {}) do
		if agent.id == id then
			return agent.name
		end
	end
	return id or "Unassigned"
end

local function selectedPath()
	local object = Selection:Get()[1]
	selectionResource = if object then "instance:" .. object:GetFullName() else ""
	if object then
		local stable = object:GetAttribute("StudioSyncId")
		if stable then
			-- Existing project-supplied identities can be inspected without mutating the object.
			notice.Text = "Selection identity: "
				.. tostring(stable)
				.. " · Reservations currently use its display path."
		end
	end
end

local function clearContent()
	for _, child in content:GetChildren() do
		if child ~= contentLayout then
			child:Destroy()
		end
	end
	order = 0
end

local function settings()
	label(content, "LOCAL BRIDGE  /  Session pairing", 25)
	local url = box(content, "http://127.0.0.1:8766", 30)
	url.Text = address
	local code = box(content, "One-time code from studiosync bridge start / pair", 30)
	button(content, "Pair and test connection", function()
		address = url.Text:gsub("/+$", "")
		plugin:SetSetting("StudioSyncAddress", address)
		local pairingCode = code.Text
		code.Text = ""
		task.spawn(function()
			local ok, paired = request("POST", "/pair", { code = pairingCode })
			if not running then
				return
			end
			if not ok then
				notice.Text = tostring(paired)
				return
			end
			token, tokenExpiry = paired.token, paired.expires_at
			sessionId = nil
			held = {}
			workspaceId, developerId = paired.workspace_id, paired.developer_id
			local registered, result = request("POST", "/actions/register_agent", {
				display_name = "Studio observer " .. string.sub(developerId :: string, 1, 8),
				workspace_id = workspaceId,
				capabilities = { "studio-monitoring" },
			})
			if registered then
				sessionId = result.data.session.id
			end
			currentTab = "Overview"
			notice.Text = "Paired. Credential expires in 15 minutes; use bridge pair to reconnect."
			refresh()
		end)
	end)
	local rate = box(content, "Refresh seconds (3–30)", 30)
	rate.Text = tostring(interval)
	button(content, "Save refresh interval", function()
		interval = math.clamp(tonumber(rate.Text) or 5, 3, 30)
		plugin:SetSetting("StudioSyncInterval", interval)
	end)
	button(content, "Read-only: " .. (if readOnly then "ON" else "OFF"), function()
		readOnly = not readOnly
		plugin:SetSetting("StudioSyncReadOnly", readOnly)
		render()
	end)
	button(content, "Edit warnings: " .. (if notifications then "ON" else "OFF"), function()
		notifications = not notifications
		plugin:SetSetting("StudioSyncNotifications", notifications)
		render()
	end)
	label(
		content,
		"Project and developer come from the bridge workspace configuration. Run studiosync init in each checkout.\nPairing credentials stay in plugin memory. Backend credentials stay on the developer machine.",
		95
	)
	if developerId then
		label(content, "Developer: " .. developerId .. "\nWorkspace: " .. tostring(workspaceId), 70)
	end
end

render = function()
	if not running then
		return
	end
	clearContent()
	if currentTab == "Settings" then
		settings()
		return
	end
	if not data then
		label(content, "No live project state yet. Pair the local bridge in Settings.", 60)
		return
	end
	if currentTab == "Overview" then
		local online, active, unresolved = 0, 0, 0
		for _, agent in data.agents do
			for _, session in agent.sessions do
				if session.status ~= "OFFLINE" then
					online += 1
				end
			end
		end
		for _, item in data.tasks do
			if item.status ~= "COMPLETED" and item.status ~= "CANCELLED" then
				active += 1
			end
		end
		for _, item in data.conflicts do
			if item.status ~= "RESOLVED" and item.status ~= "DISMISSED" then
				unresolved += 1
			end
		end
		label(
			content,
			string.format(
				"%d connected sessions   ·   %d active tasks\n%d reservations   ·   %d unresolved conflicts",
				online,
				active,
				#data.locks,
				unresolved
			),
			55
		)
		label(content, "Mode: " .. data.project.mode .. "\nAccess: " .. data.role, 45)
		label(
			content,
			"SELECTED OBJECT  /  "
				.. (if selectionResource ~= "" then selectionResource else "Select an object in Studio"),
			60
		)
		local resource = box(content, "Resource: file:, instance:, subtree:, interface: or system:", 30)
		resource.Text = selectionResource
		button(content, "Inspect ownership", function()
			task.spawn(function()
				local ok, result = request("POST", "/actions/check_lock", { resource = resource.Text })
				if ok then
					local owners = {}
					for _, lease in result.data.locks do
						table.insert(owners, agentName(lease.agent_id) .. " (" .. lease.mode .. ")")
					end
					notice.Text = if #owners > 0
						then table.concat(owners, ", ")
						else "No active covering reservation. Acquire before coordinated edits."
				else
					notice.Text = tostring(result)
				end
			end)
		end)
		button(content, "Request exclusive ownership", function()
			if sessionId then
				act(
					"acquire_lock",
					{ session_id = sessionId, resource = resource.Text, mode = "WRITE" },
					function(result)
						held[result.lease.id] = result.lease
					end
				)
			end
		end)
		label(content, "INTEGRATION", 24)
		for _, item in data.integration do
			label(
				content,
				string.sub(item.task_id, 1, 8)
					.. " · "
					.. (if item.ready then "Eligible for Git review" else item.reason),
				40
			)
		end
		label(
			content,
			"DISPLAY-PATH RESERVATIONS\nRenaming or reparenting changes the reservation path. Recheck selection after hierarchy changes; Studio edits are advisory and observable, not blocked.",
			85
		)
	elseif currentTab == "Agents" then
		if selectedAgent then
			label(
				content,
				selectedAgent.name
					.. "\nAgent: "
					.. selectedAgent.id
					.. "\nDeveloper: "
					.. selectedAgent.user_id,
				85
			)
		end
		for _, agent in data.agents do
			local session = agent.sessions[1]
			local status = if session then session.status else "OFFLINE"
			local count = 0
			for _, lease in data.locks do
				if lease.agent_id == agent.id then
					count += 1
				end
			end
			button(content, agent.name .. " · " .. status .. " · " .. count .. " reserved", function()
				selectedAgent = agent
				render()
			end)
			if session then
				label(
					content,
					"Task: "
						.. tostring(session.current_task_id or "None")
						.. "\nLast activity: "
						.. os.date("!%H:%M:%S UTC", session.last_heartbeat),
					44
				)
			end
		end
	elseif currentTab == "Tasks" then
		local search = box(content, "Filter title, agent, status or priority", 30)
		search.Text = filter
		search.FocusLost:Connect(function()
			filter = string.lower(search.Text)
			render()
		end)
		local tasks = table.clone(data.tasks)
		table.sort(tasks, function(a, b)
			return a.status .. a.title < b.status .. b.title
		end)
		for _, item in tasks do
			local text = item.status
				.. " · P"
				.. item.priority
				.. " · "
				.. item.title
				.. " · "
				.. agentName(item.assigned_agent_id)
			if filter == "" or string.find(string.lower(text), filter, 1, true) then
				button(content, text, function()
					selectedTask = item
					render()
				end)
			end
		end
		if selectedTask then
			local item = selectedTask
			label(
				content,
				"TASK "
					.. item.id
					.. "\n"
					.. item.description
					.. "\nResources: "
					.. table.concat(item.resources, ", "),
				100
			)
			button(content, "Claim selected task", function()
				if sessionId then
					act("claim_task", { session_id = sessionId, task_id = item.id })
				end
			end)
			for _, status in { "IN_PROGRESS", "COMPLETED", "CANCELLED" } do
				button(content, "Set " .. status, function()
					if sessionId then
						act("update_task", { session_id = sessionId, task_id = item.id, status = status })
					end
				end)
			end
			if data.role == "OWNER" then
				local assignee = box(content, "Agent ID to assign (owner only)", 30)
				local reason = box(content, "Reassignment reason", 30)
				button(content, "Assign and return to READY", function()
					if sessionId then
						act("update_task", {
							session_id = sessionId,
							task_id = item.id,
							status = "READY",
							assigned_agent_id = assignee.Text,
							reason = reason.Text,
						})
					end
				end)
			end
		end
	elseif currentTab == "Reservations" then
		for _, lease in data.locks do
			label(
				content,
				lease.resource
					.. "\n"
					.. agentName(lease.agent_id)
					.. " · "
					.. lease.mode
					.. " · expires "
					.. os.date("!%H:%M:%S UTC", lease.expires_at)
					.. "\nTask: "
					.. tostring(lease.task_id or "Manual reservation"),
				80
			)
			if held[lease.id] then
				local own = held[lease.id]
				button(content, "Release " .. string.sub(lease.id, 1, 8), function()
					act("release_lock", {
						session_id = sessionId,
						lease_id = own.id,
						lease_token = own.lease_token,
						generation = own.generation,
					}, function()
						held[own.id] = nil
					end)
				end)
			end
		end
		if #data.locks == 0 then
			label(content, "No active reservations. Request ownership from Overview.", 55)
		end
	elseif currentTab == "Messages" then
		local recipient = box(content, "Recipient agent ID (empty = project announcement)", 30)
		local subject = box(content, "Subject", 30)
		local body = box(content, "Message / dependency / ownership request", 75)
		button(content, "Send coordination message", function()
			if sessionId then
				act("send_message", {
					session_id = sessionId,
					recipient_agent_id = if recipient.Text == "" then nil else recipient.Text,
					message_type = if recipient.Text == "" then "ANNOUNCEMENT" else "DIRECT",
					subject = subject.Text,
					body = body.Text,
				})
			end
		end)
		for _, message in data.messages do
			label(content, message.message_type .. " · " .. message.subject .. "\n" .. message.body, 95)
			button(content, "Acknowledge " .. string.sub(message.id, 1, 8), function()
				if sessionId then
					act("acknowledge_message", { session_id = sessionId, message_id = message.id })
				end
			end)
		end
		label(content, "INTERFACE REGISTRY", 24)
		for _, contract in data.interfaces do
			local version = contract.versions[#contract.versions]
			label(
				content,
				contract.name
					.. " · v"
					.. version.version
					.. " · "
					.. version.approval_status
					.. "\n"
					.. version.description,
				65
			)
		end
	elseif currentTab == "Conflicts" then
		for _, conflict in data.conflicts do
			label(
				content,
				conflict.category
					.. " · "
					.. conflict.severity
					.. " · "
					.. conflict.status
					.. "\n"
					.. conflict.description
					.. "\nResources: "
					.. table.concat(conflict.resources, ", ")
					.. "\nAgents: "
					.. table.concat(conflict.agents, ", "),
				125
			)
			if data.role == "OWNER" and conflict.status ~= "RESOLVED" and conflict.status ~= "DISMISSED" then
				local reason = box(content, "Resolution reason (does not change files)", 30)
				button(content, "Mark resolved after inspection", function()
					act(
						"resolve_conflict",
						{ conflict_id = conflict.id, status = "RESOLVED", reason = reason.Text }
					)
				end)
			end
		end
		if #data.conflicts == 0 then
			label(content, "No reported conflicts. External edits may still require Git review.", 55)
		end
	end
end

refresh = function()
	if networkBusy or not token or not running then
		return
	end
	networkBusy = true
	task.spawn(function()
		if sessionId then
			request("POST", "/actions/heartbeat", { session_id = sessionId, status = "ONLINE" })
		end
		local ok, result = request("GET", "/project", nil)
		networkBusy = false
		if not running then
			return
		end
		if ok then
			data = result.data
			backoff = 1
			connection.Text = data.project.name .. " · Connected · " .. os.date("!%H:%M:%S UTC")
			connection.TextColor3 = accent
			local focused = UserInputService:GetFocusedTextBox()
			if not focused or not focused:IsDescendantOf(content) then
				render()
			end
		else
			backoff = math.min(backoff * 2, 8)
			connection.Text = "Disconnected · " .. tostring(result) .. " · Displayed state may be stale"
			connection.TextColor3 = Color3.fromRGB(234, 160, 117)
		end
	end)
end

for _, tab in { "Overview", "Agents", "Tasks", "Reservations", "Messages", "Conflicts", "Settings" } do
	button(navigation, tab, function()
		currentTab = tab
		render()
	end)
end
-- Seven tabs occupy three rows; keep the content clear of the last settings button.
navigation.Size = UDim2.new(1, 0, 0, 95)
content.Size = UDim2.new(1, 0, 1, -215)

local selectionConnection = Selection.SelectionChanged:Connect(function()
	selectedPath()
	if currentTab == "Overview" then
		render()
	end
end)
local editQueued: { [string]: boolean } = {}
local editConnection = ScriptEditorService.TextDocumentDidChange:Connect(function(document)
	if not notifications or not token then
		return
	end
	local source = document:GetScript()
	if not source then
		return
	end
	local path = "instance:" .. source:GetFullName()
	if editQueued[path] then
		return
	end
	editQueued[path] = true
	task.delay(1, function()
		editQueued[path] = nil
		if not running then
			return
		end
		local ok, result = request("POST", "/actions/check_lock", { resource = path })
		if ok then
			for _, lease in result.data.locks do
				if lease.mode == "WRITE" and lease.session_id ~= sessionId then
					notice.Text = "Observed edit overlaps "
						.. agentName(lease.agent_id)
						.. "'s reservation. Edit was not blocked."
					if not readOnly then
						request("POST", "/actions/report_conflict", {
							category = "STUDIO_EDIT",
							resources = { path },
							agents = { lease.agent_id },
							description = "Observed ScriptEditorService edit in a reserved display path. Editing identity cannot be proven.",
						})
					end
				end
			end
		end
	end)
end)
plugin.Unloading:Connect(function()
	running = false
	token = nil
	selectionConnection:Disconnect()
	editConnection:Disconnect()
	widget:Destroy()
end)
selectedPath()
render()
task.spawn(function()
	while running do
		if token and os.time() >= tokenExpiry then
			token = nil
			connection.Text =
				"Pairing credential expired. Run studiosync bridge pair and reconnect in Settings."
		end
		if token then
			refresh()
		end
		task.wait(math.min(interval * backoff, 30))
	end
end)
