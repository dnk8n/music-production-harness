-- A song's mixer (spec/components/renoise.md: REQ-REN-03, REQ-REN-10, REQ-REN-11).
-- Pure use of the Renoise API, so tests can run it against a fake `renoise`.
--
-- The plan names each device, its role and its parameters. A parameter's value
-- is given as Renoise shows it: a frequency ("Hz"), a level ("dB"), a time
-- ("ms"), a plain number, a choice's text, a fraction of its range, a raw
-- value, or a send track's name. The value that shows it is found from a table
-- of what the parameter shows across its range, so Renoise's own scales
-- needn't be known. Automation is written the same way, pattern by pattern.
--
--   mix = { tracks = { { devices, automation }, ... }, -- one per sequencer track
--           sends = { { name, devices, automation }, ... }, -- send tracks
--           master = { devices, automation } }
--   device = { path, name, role, params = { { names, value, unit, optional }, ... } }
--   automation = { device (its place among the planned devices, from 1), names, unit,
--                  patterns = { { pattern, points = { { time, value }, ... } }, ... } }
--   modes = { mix = { role, ... }, stem = { ... }, dry = { ... } }: the roles left on
--
-- The result file gets what each kind of device has (M.describe) and what each
-- planned device shows once built (M.shown), so one run on the Mac shows how
-- Renoise names and shows them.

local M = {}

M.SAMPLES = 1024 -- values tried across a parameter's range, to see what it shows
M.SILENT_DB = -90 -- a level at or below this is silence: the parameter's lowest

local ON = { on = true, enabled = true, yes = true, ["true"] = true }
local OFF = { off = true, disabled = true, no = true, ["false"] = true }

local cache = {} -- what each kind of device's parameters show, for one build

function M.reset()
  cache = {}
end

local function trim(text)
  return (tostring(text):gsub("^%s+", ""):gsub("%s+$", ""))
end

-- What Renoise shows, as a number in the plan's unit: "1.20 kHz" is 1200 (Hz),
-- "1.5 s" is 1500 (ms) and "-INF dB" is minus infinity. Nil for anything else.
function M.parse(text, unit)
  local shown = trim(text):lower():gsub(",", ".")
  if shown:find("inf", 1, true) then
    return shown:sub(1, 1) == "-" and -math.huge or math.huge
  end
  local number, rest = shown:match("^([%+%-]?%d*%.?%d+)%s*(.-)$")
  if not number then
    return nil
  end
  local value = tonumber(number)
  if unit == "Hz" and rest:sub(1, 1) == "k" then
    value = value * 1000
  elseif unit == "ms" and (rest == "s" or rest == "sec" or rest == "secs") then
    value = value * 1000
  end
  return value
end

-- What a parameter shows across its range, lowest value first.
local function shows(kind, parameter)
  local key = kind .. "|" .. parameter.name
  if cache[key] then
    return cache[key]
  end
  local low, high = parameter.value_min, parameter.value_max
  local count = M.SAMPLES
  if parameter.value_quantum > 0 then
    count = math.max(1, math.min(count, math.floor((high - low) / parameter.value_quantum + 0.5)))
  end
  local was, rows = parameter.value, {}
  for k = 0, count do
    parameter.value = low + (high - low) * k / count
    rows[#rows + 1] = { value = parameter.value, text = parameter.value_string }
  end
  parameter.value = was
  cache[key] = { rows = rows, runs = {} }
  return cache[key]
end

-- Runs of values that show the same number, in order of value.
local function runs(shown, unit)
  if shown.runs[unit] then
    return shown.runs[unit]
  end
  local found = {}
  for _, row in ipairs(shown.rows) do
    local number = M.parse(row.text, unit)
    if number ~= nil then
      local last = found[#found]
      if last and last.shown == number then
        last.last, last.sum, last.count = row.value, last.sum + row.value, last.count + 1
      else
        found[#found + 1] =
          { shown = number, first = row.value, last = row.value, sum = row.value, count = 1 }
      end
    end
  end
  shown.runs[unit] = found
  return found
end

local function middle(run)
  return run.sum / run.count
end

-- The value at the lowest (or highest) end of what a parameter shows.
local function edge(found, lowest)
  local rising = found[1].shown <= found[#found].shown
  if lowest == rising then
    return found[1].first
  end
  return found[#found].last
end

local function by_number(found, unit, target)
  if #found == 0 then
    return nil
  end
  if unit == "dB" and target <= M.SILENT_DB then
    return edge(found, true)
  end
  local usable = {}
  for _, run in ipairs(found) do
    if run.shown > -math.huge and run.shown < math.huge then
      usable[#usable + 1] = run
    end
  end
  if #usable == 0 then
    return nil
  end
  local rising = usable[1].shown <= usable[#usable].shown
  local low, high = usable[1].shown, usable[#usable].shown
  if not rising then
    low, high = high, low
  end
  if target <= low then
    return edge(usable, true)
  elseif target >= high then
    return edge(usable, false)
  end
  local a, b = 1, #usable
  while b - a > 1 do
    local mid = math.floor((a + b) / 2)
    if (usable[mid].shown <= target) == rising then
      a = mid
    else
      b = mid
    end
  end
  local from, to = usable[a], usable[b]
  if from.shown == target then
    return middle(from)
  elseif to.shown == target then
    return middle(to)
  end
  local share = (target - from.shown) / (to.shown - from.shown)
  return middle(from) + (middle(to) - middle(from)) * share
end

local function by_choice(rows, wanted)
  local want = trim(wanted):lower()
  for _, row in ipairs(rows) do
    local shown = trim(row.text):lower()
    if shown == want or (ON[want] and ON[shown]) or (OFF[want] and OFF[shown]) then
      return row.value
    end
  end
  for _, row in ipairs(rows) do
    if trim(row.text):lower():find(want, 1, true) then
      return row.value
    end
  end
  return nil
end

-- A #Send's receiver: the send track whose name it shows, or by its place.
local function by_send(rows, wanted, parameter)
  local want = trim(wanted.name):lower()
  for _, row in ipairs(rows) do
    if trim(row.text):lower():find(want, 1, true) then
      return row.value
    end
  end
  return parameter.value_min + wanted.index
end

-- The value of `parameter` (on a device of kind `kind`) that shows `wanted`.
function M.value_for(kind, parameter, unit, wanted)
  local low, high = parameter.value_min, parameter.value_max
  if unit == "fraction" then
    return low + (high - low) * wanted
  elseif unit == "raw" then
    return math.min(math.max(wanted, low), high)
  end
  local shown = shows(kind, parameter)
  if unit == "choice" then
    return by_choice(shown.rows, wanted)
  elseif unit == "send" then
    return by_send(shown.rows, wanted, parameter)
  end
  return by_number(runs(shown, unit), unit, wanted)
end

-- The first of `names` the device has, in any case.
function M.parameter(device, names)
  for _, name in ipairs(names) do
    local want = name:lower()
    for _, parameter in ipairs(device.parameters) do
      if trim(parameter.name):lower() == want then
        return parameter
      end
    end
  end
  return nil
end

local function names_of(device)
  local found = {}
  for _, parameter in ipairs(device.parameters) do
    found[#found + 1] = parameter.name
  end
  return table.concat(found, ", ")
end

-- A device's parameters, for the result file: each one's range and what it
-- shows at its lowest, default and highest values.
function M.describe(device)
  local found = {}
  for _, parameter in ipairs(device.parameters) do
    local was, shown = parameter.value, {}
    for _, at in ipairs({ parameter.value_min, parameter.value_default, parameter.value_max }) do
      parameter.value = at
      shown[#shown + 1] = parameter.value_string
    end
    parameter.value = was
    found[#found + 1] = {
      name = parameter.name,
      min = parameter.value_min,
      max = parameter.value_max,
      default = parameter.value_default,
      quantum = parameter.value_quantum,
      shows = shown,
    }
  end
  return found
end

local function wanted_text(setting)
  if type(setting.value) == "table" then
    return tostring(setting.value.name)
  end
  return tostring(setting.value)
end

local function settle(device, kind, setting, problems, label)
  local parameter = M.parameter(device, setting.names)
  if parameter == nil then
    if not setting.optional then
      problems[#problems + 1] = string.format(
        "%s: no parameter called %s (it has %s)",
        label,
        table.concat(setting.names, " or "),
        names_of(device)
      )
    end
    return
  end
  local value = M.value_for(kind, parameter, setting.unit, setting.value)
  if value == nil then
    problems[#problems + 1] =
      string.format("%s: %s never shows %s", label, parameter.name, wanted_text(setting))
    return
  end
  parameter.value = math.min(math.max(value, parameter.value_min), parameter.value_max)
end

-- Adds planned devices after a track's own mixer device, names and sets them,
-- and records each kind's parameters in `described`. Anything already after
-- the mixer device goes first (a new song's master may come with devices), so
-- the planned devices are where the automation and the render modes expect.
local function add_devices(track, devices, described, problems, label)
  while #track.devices > 1 do
    track:delete_device_at(#track.devices)
  end
  for _, entry in ipairs(devices) do
    local ok, device = pcall(track.insert_device_at, track, entry.path, #track.devices + 1)
    if not ok or device == nil then
      problems[#problems + 1] = string.format("%s: Renoise has no device %s", label, entry.path)
    else
      if described[entry.path] == nil then
        described[entry.path] = M.describe(device)
      end
      device.display_name = entry.name
      for _, setting in ipairs(entry.params) do
        settle(device, entry.path, setting, problems, label .. ", " .. entry.name)
      end
    end
  end
end

-- Sets an automation's points. If Renoise refuses a point at the very end of
-- a pattern, the points are kept to its last line instead.
local function place(automation, points)
  if pcall(function()
    automation.points = points
  end) then
    return
  end
  local kept, seen = {}, {}
  for _, point in ipairs(points) do
    point.time = math.min(point.time, automation.length)
    if seen[point.time] then
      kept[seen[point.time]] = point
    else
      kept[#kept + 1] = point
      seen[point.time] = #kept
    end
  end
  automation.points = kept
end

local function automate(song, index, devices, lane, problems, label)
  local entry = devices[lane.device]
  local device = song:track(index):device(lane.device + 1) -- after the track's mixer device
  local parameter = device and M.parameter(device, lane.names)
  if entry == nil or parameter == nil then
    problems[#problems + 1] =
      string.format("%s: nothing called %s to automate", label, table.concat(lane.names, " or "))
    return
  end
  local low, span = parameter.value_min, parameter.value_max - parameter.value_min
  for _, part in ipairs(lane.patterns) do
    local pattern = song:pattern(song.sequencer.pattern_sequence[part.pattern])
    local track = pattern:track(index)
    local automation = track:find_automation(parameter) or track:create_automation(parameter)
    automation.playmode = renoise.PatternTrackAutomation.PLAYMODE_LINES
    local points = {}
    for _, point in ipairs(part.points) do
      local value = M.value_for(entry.path, parameter, lane.unit, point[2])
      if value == nil then
        problems[#problems + 1] =
          string.format("%s: %s never shows %s", label, parameter.name, tostring(point[2]))
        return
      end
      points[#points + 1] = { time = point[1], value = (value - low) / span, scaling = 0 }
    end
    place(automation, points)
  end
end

-- Makes the planned send tracks after the master, named after their sends.
function M.ensure_sends(song, sends)
  while song.send_track_count > 0 do
    song:delete_track_at(song.sequencer_track_count + 2)
  end
  for k, wanted in ipairs(sends) do
    local index = song.sequencer_track_count + 1 + k -- behind the master: a send track
    song:insert_track_at(index)
    song:track(index).name = wanted.name
  end
end

-- Calls `each(index, planned, label)` for every track the mix plans.
local function every_track(plan, each)
  local master = #plan.tracks + 1
  for index, planned in ipairs(plan.mix.tracks) do
    each(index, planned, plan.tracks[index].name)
  end
  each(master, plan.mix.master, "master")
  for k, planned in ipairs(plan.mix.sends) do
    each(master + k, planned, "send " .. planned.name)
  end
end

-- Turns each planned device on or off for a kind of render (REQ-REN-03):
-- "mix", as built; "stem", with only the ducks on; "dry", with all off.
function M.switch(song, plan, mode)
  if plan.mix == nil then
    return
  end
  local on = {}
  for _, role in ipairs(plan.modes[mode or "mix"]) do
    on[role] = true
  end
  every_track(plan, function(index, planned)
    local track = song:track(index)
    for k, entry in ipairs(planned.devices) do
      track:device(k + 1).is_active = on[entry.role] == true
    end
  end)
end

-- What each planned device shows once built, for the result file: whether it's
-- on, and each parameter the plan sets, by the name Renoise gives it.
function M.shown(song, plan)
  local found = {}
  every_track(plan, function(index, planned, label)
    local track, devices = song:track(index), {}
    for k, entry in ipairs(planned.devices) do
      local device = track:device(k + 1)
      local shows = {}
      for _, setting in ipairs(entry.params) do
        local parameter = device and M.parameter(device, setting.names)
        if parameter then
          shows[parameter.name] = parameter.value_string
        end
      end
      devices[#devices + 1] = { name = entry.name, active = device and device.is_active, shows = shows }
    end
    found[#found + 1] = { track = label, devices = devices }
  end)
  return found
end

-- Adds every planned device and its automation (after the sequencer tracks'
-- notes and the send tracks exist), then switches them on as built.
function M.build(song, plan, described, problems)
  every_track(plan, function(index, planned, label)
    add_devices(song:track(index), planned.devices, described, problems, label)
  end)
  if #problems > 0 then
    return -- the devices aren't where the automation expects them
  end
  every_track(plan, function(index, planned, label)
    for _, lane in ipairs(planned.automation) do
      automate(song, index, planned.devices, lane, problems, label)
    end
  end)
  M.switch(song, plan, "mix")
end

return M
