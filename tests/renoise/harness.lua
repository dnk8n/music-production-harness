-- Runs the bridge tool against fake_renoise.lua and prints what happened as JSON.
--   lua5.1 harness.lua <tool dir> build <plan.lua> [mix | stem | dry]
--   lua5.1 harness.lua <tool dir> job <installed tool dir> <ticks> [option ...]
--   lua5.1 harness.lua <tool dir> data <file.lua>
-- Job options: `refuse` (Renoise never starts a new song), `open=<path>`
-- (someone has that song open before the first tick), `changed` (with
-- unsaved changes) and `loading=<n>` (no song for the first n ticks, as while
-- one loads), and `slice=<s>` (a build pauses once it has run s seconds of
-- CPU time; 0 pauses at every chance). Each tick moves the clock on 1000 s,
-- so timeouts are quick.

local here = arg[0]:match("^(.*)/") or "."
local tool_dir, mode = arg[1], arg[2]
package.path = tool_dir .. "/?.lua;" .. here .. "/?.lua;" .. package.path

local fake = require("fake_renoise")
local json = require("json")

local function load_data(path)
  local chunk = assert(loadfile(path))
  setfenv(chunk, {})
  return chunk()
end

-- The settings in a device's preset data that aren't parameters, by tag.
local function preset_settings(device)
  local found = {}
  local data = device.active_preset_data
  if type(data) == "string" then
    for tag, value in data:gmatch("<([%w_]+)>([^<]*)</%1>") do
      if tag ~= "Value" and tag ~= "Visualization" then
        found[tag] = value
      end
    end
  end
  return found
end

-- A device as the tool left it: its name, whether it's on, what each
-- parameter shows, and its other preset settings.
local function device_state(device)
  local shown = {}
  for _, parameter in ipairs(device.parameters or {}) do
    shown[parameter.name] = parameter.value_string
  end
  return {
    name = device.name,
    display_name = device.display_name,
    active = device.is_active,
    shows = shown,
    preset = preset_settings(device),
  }
end

local function track_mixer(track)
  local devices = {}
  for k = 2, #track.devices do
    devices[#devices + 1] = device_state(track.devices[k])
  end
  return { name = track.name, type = track.type, devices = devices }
end

local function instrument_state(instrument)
  local samples, sets = {}, {}
  for _, sample in ipairs(instrument.samples) do
    samples[#samples + 1] = {
      name = sample.name,
      sample = sample.loaded,
      base_note = sample.sample_mapping.base_note,
      note_range = sample.sample_mapping.note_range,
      volume = sample.volume,
      new_note_action = sample.new_note_action,
      modulation_set = sample.modulation_set_index,
      interpolation = sample.interpolation_mode,
    }
  end
  for _, set in ipairs(instrument.sample_modulation_sets or {}) do
    local devices = {}
    for _, device in ipairs(set.devices) do
      local state = device_state(device)
      state.target = device.target
      state.tempo_synced = device.tempo_synced
      devices[#devices + 1] = state
    end
    sets[#sets + 1] = { name = set.name, devices = devices }
  end
  return { name = instrument.name, samples = samples, modulation_sets = sets }
end

local function describe(song)
  local out = { bpm = song.transport.bpm, lpb = song.transport.lpb, tracks = {}, notes = {} }
  out.tpl = song.transport.tpl
  out.headroom_db = math.floor(math.lin2db(song.transport.track_headroom) * 1000 + 0.5) / 1000
  out.patterns = {}
  out.sections = {}
  out.automation = {}
  local offset = 0 -- lines in the patterns before this one
  for position, index in ipairs(song.sequencer.pattern_sequence) do
    local pattern = song.patterns[index]
    out.patterns[position] = pattern.number_of_lines
    local section = song.sequencer.sections[position]
    if section then
      out.sections[#out.sections + 1] = { position = position, line = offset, name = section }
    end
    for track_index, track in pairs(pattern.tracks) do
      for line_index, line in pairs(track.lines) do
        local column = line.columns[1]
        if column then
          local note = {
            track = track_index,
            line = offset + line_index - 1,
            note = column.note_value,
            instrument = column.instrument_value,
            volume = column.volume_value,
            delay = column.delay_value,
          }
          local effect = line.effects[1]
          if effect then
            note.effect = effect.number_string
            note.amount = effect.amount_value
          end
          out.notes[#out.notes + 1] = note
        end
      end
      for parameter, automation in pairs(track.automation or {}) do
        out.automation[#out.automation + 1] = {
          track = track_index,
          pattern = position,
          parameter = parameter.name,
          playmode = automation.playmode,
          points = automation.points,
        }
      end
    end
    offset = offset + pattern.number_of_lines
  end
  table.sort(out.notes, function(a, b)
    if a.track ~= b.track then
      return a.track < b.track
    end
    return a.line < b.line
  end)
  table.sort(out.automation, function(a, b)
    if a.track ~= b.track then
      return a.track < b.track
    elseif a.parameter ~= b.parameter then
      return a.parameter < b.parameter
    end
    return a.pattern < b.pattern
  end)
  for index = 1, song.sequencer_track_count do
    local track = song.tracks[index]
    local instrument = song.instruments[index]
    local sample = instrument.samples[1] or { sample_mapping = {} } -- a clip track may load none
    local devices = {}
    for _, device in ipairs(track.devices) do
      devices[#devices + 1] = { name = device.name, active = device.is_active }
    end
    local zones = {}
    for _, each in ipairs(instrument.samples) do
      local mapping = each.sample_mapping
      if mapping.note_range[1] == mapping.note_range[2] then -- a sample on a key of its own
        zones[#zones + 1] = {
          name = each.name,
          sample = each.loaded,
          base_note = mapping.base_note,
          note_range = mapping.note_range,
          volume = each.volume,
          autoseek = each.autoseek,
          autofade = each.autofade,
        }
      end
    end
    out.tracks[index] = {
      zones = zones,
      name = track.name,
      devices = devices,
      volume_column = track.volume_column_visible,
      delay_column = track.delay_column_visible,
      effect_columns = track.visible_effect_columns,
      instrument = instrument.name,
      sample = sample.loaded,
      volume = sample.volume,
      transpose = sample.transpose,
      fine_tune = sample.fine_tune,
      interpolation = sample.interpolation_mode,
      slices = sample.slice_markers,
      samples = #instrument.samples,
    }
  end
  out.instruments = #song.instruments
  out.instrument_states = {}
  for k, instrument in ipairs(song.instruments) do
    out.instrument_states[k] = instrument_state(instrument)
  end
  out.mixer = {}
  for index, track in ipairs(song.tracks) do
    out.mixer[index] = track_mixer(track)
  end
  return out
end

if mode == "data" then
  print(json.encode(load_data(arg[3])))
elseif mode == "build" then
  fake.install(tool_dir)
  local build = require("build")
  local song = renoise.song()
  local plan = load_data(arg[3])
  local described = {}
  local ok, problem = pcall(build.build, song, plan, described)
  local out = describe(song)
  out.error = (not ok) and tostring(problem) or nil
  out.described = described
  if ok then
    out.shown = build.shown(song, plan)
  end
  if arg[4] then -- then switch the mixer as one kind of render would
    build.switch(song, plan, arg[4])
    out.switched = describe(song).mixer
  end
  print(json.encode(out))
elseif mode == "job" then
  local state = fake.install(arg[3] .. "/")
  local options = {}
  for n = 5, #arg do
    local key, value = arg[n]:match("^([^=]+)=(.*)$")
    options[key or arg[n]] = value or true
  end
  state.refuse_new_song = options.refuse == true
  state.clock = 0
  os.time = function()
    return state.clock
  end
  dofile(tool_dir .. "/main.lua")
  if options.slice then
    require("slice").SLICE_S = tonumber(options.slice)
  end
  if options.open then
    state.open(options.open, options.changed == true)
  end
  for n = 1, tonumber(arg[4]) do
    state.clock = n * 1000
    state.loading = n <= (tonumber(options.loading) or 0)
    state.tick()
  end
  print(json.encode({
    saved = state.saved,
    renders = fake.renders,
    prompts = state.prompts,
    unsaved = state.unsaved,
    open = state.song.file_name,
  }))
end
